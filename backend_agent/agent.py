import asyncio
import logging
from typing import List, Dict, Any

from openai import AsyncOpenAI

from config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    SESSION_MEMORY_WINDOW,
)

logger = logging.getLogger(__name__)

# Load the system prompt ONCE at startup
try:
    with open("prompt.txt", "r", encoding="utf-8") as _f:
        SYSTEM_PROMPT: str = _f.read()
except FileNotFoundError:
    SYSTEM_PROMPT = """You are a procurement assistant for Compass Group India.
You help vendors with their Purchase Orders over chat — keep it friendly, short, and natural like a WhatsApp conversation.

PO context is provided below. Use it only when the vendor asks something specific.

---
BEFORE REPLYING:

1. Check the PO data provided in context.
2. Check session memory — never ask something the vendor already told you.
3. Understand what the vendor is saying, then reply naturally.

---
HOW TO HANDLE:

SITUATION A — Vendor replying to delivery confirmation

  Says YES (confirm / haan / 1 / theek hai / on time / etc.):
  → Acknowledge warmly in one line. Done.
  → intent=CONFIRMED, escalate=false, conversation_complete=true

  Says NO but no reason (nahi / 2 / issue hai / etc.):
  → Ask why, casually. One line.
  → intent=UNCLEAR, escalate=false, conversation_complete=false

  Says NO with reason already (delay / partial / price / quality / etc.):
  → Acknowledge their reason. Tell them team will follow up.
  → intent=<mapped below>, escalate=true, conversation_complete=true

SITUATION B — Vendor giving reason (after you asked)

  → Acknowledge in one line. Say team will reach out shortly.
  → intent=<mapped below>, escalate=true, conversation_complete=true

  Mapping:
  - Delay / late / kal / not ready → DELAYED
  - Partial / half / thoda / some items → PARTIAL
  - Cannot deliver / price / brand / quality / vehicle / any hard stop → REJECTED

SITUATION C — Vendor asking about their PO

  → Answer from PO context only.
  → Only share: PO number, PO date, delivery date, vendor name, vendor code, unit description.
  → If not available: tell them team will get in touch.
  → intent=INFO_QUERY, escalate=false, conversation_complete=false

SITUATION D — Greeting or casual message

  → Greet back warmly, one line. Gently bring up the PO.
  → intent=UNCLEAR, escalate=false, conversation_complete=false

SITUATION E — Unclear message

  → Ask one simple open question to understand what they need.
  → intent=UNCLEAR, escalate=false, conversation_complete=false

---
ESCALATION — only when ALL three are true:
  1. Vendor cannot or will not deliver on time
  2. You know the reason
  3. You have acknowledged it and told them team will follow up

---
TONE — most important:
- WhatsApp style. Short. Warm. Human.
- Max 1 to 2 lines per reply.
- No bullet points, no formatting in replies.
- No standalone filler lines like "Sure!", "Got it!", "Noted!" — weave acknowledgment naturally.
- Match vendor language — Hindi, English, or Hinglish, mirror their tone.
- Never volunteer PO details unless asked.
- Be humble and polite — never sound robotic or rude.

---
At the very end of every reply, on a new line, output:
INTENT_JSON: {"intent": "UNCLEAR", "po_num": "", "vendor_name": "", "reason": "", "escalate": false, "conversation_complete": false}

Fill actual values:
- intent: CONFIRMED / DELAYED / PARTIAL / REJECTED / INFO_QUERY / UNCLEAR
- po_num: from PO context — never leave blank if available
- vendor_name: from PO context if available, else empty string
- reason: vendor's issue in plain language, else empty string
- escalate: true only when issue is clear and team needs to act
- conversation_complete: true only when nothing more is needed from vendor"""

    logger.warning("prompt.txt not found — using default system prompt placeholder.")

# OpenAI async client
_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Session memory store: { session_id -> list of message dicts }
_memory: Dict[str, List[Dict[str, str]]] = {}
# Asyncio lock to protect shared _memory dict under concurrent requests
_memory_lock = asyncio.Lock()


async def call_agent(
    session_id: str,
    message_text: str,
    po_data_block: str,
) -> str:
    """
    Append the user message to session history, call OpenAI, append the
    assistant reply, trim to the configured window, and return the raw
    AI output string.
    """
    async with _memory_lock:
        history = _memory.get(session_id, [])
        # Append current user message
        history.append({"role": "user", "content": message_text})

    # Build messages array for OpenAI
    system_message = {
        "role": "system",
        "content": f"{SYSTEM_PROMPT}\n\n{po_data_block}",
    }
    messages: List[Dict[str, str]] = [system_message] + list(history)

    # Call OpenAI
    response = await _client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=OPENAI_TEMPERATURE,
        max_tokens=500,
        messages=messages,  # type: ignore[arg-type]
    )

    ai_output: str = response.choices[0].message.content or ""

    # Update memory atomically
    async with _memory_lock:
        history = _memory.get(session_id, history)
        history.append({"role": "assistant", "content": ai_output})
        # Trim to last N messages
        _memory[session_id] = history[-SESSION_MEMORY_WINDOW:]

    return ai_output


SUMMARY_SYSTEM_PROMPT = (
    "You are a procurement operations assistant for Compass Group. "
    "Analyze this WhatsApp conversation between Compass and a supplier for a Purchase Order. "
    "Generate a concise operational summary covering: "
    "(1) Current PO status — confirmed, delayed, at_risk, or unresolved "
    "(2) Key issues or exceptions raised by the supplier "
    "(3) Any delivery dates, quantities, or reasons mentioned "
    "(4) Whether human intervention is required. "
    "Be factual and use procurement language."
)

_RISK_KEYWORDS = {
    "high": {"delayed", "delay", "rejected", "cannot deliver", "escalat", "at_risk", "partial"},
    "medium": {"unclear", "pending", "unresolved", "issue", "problem"},
}


def _detect_risk(summary_text: str, messages: List[Dict[str, Any]]) -> str:
    """Derive a risk_level from summary text and intent fields in the messages."""
    text_lower = summary_text.lower()
    intents = {(m.get("intent") or "").lower() for m in messages}
    combined = text_lower + " " + " ".join(intents)

    for word in _RISK_KEYWORDS["high"]:
        if word in combined:
            return "high"
    for word in _RISK_KEYWORDS["medium"]:
        if word in combined:
            return "medium"
    return "none"


def _derive_key_intent(messages: List[Dict[str, Any]]) -> str:
    """Return the most significant intent found in the conversation."""
    priority = ["REJECTED", "DELAYED", "PARTIAL", "ESCALATED", "CONFIRMED", "INFO_QUERY", "UNCLEAR"]
    intents_found = {(m.get("intent") or "").upper() for m in messages}
    for p in priority:
        if p in intents_found:
            return p
    return "UNCLEAR"


async def generate_po_summary(
    po_num: str,
    messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Format chat_history rows into a transcript and call OpenAI to produce
    a procurement operational summary.  Uses the same _client / OPENAI_MODEL /
    response-parsing pattern as call_agent and summarize_handback.
    """
    # Build a readable transcript
    transcript_lines: List[str] = []
    for msg in messages:
        sender = (msg.get("sender_type") or "unknown").upper()
        text   = (msg.get("message_text") or "").strip()
        ts     = str(msg.get("sent_at") or "")
        line   = f"[{ts}] {sender}: {text}"
        if msg.get("intent"):
            line += f"  (intent: {msg['intent']})"
        transcript_lines.append(line)

    transcript = "\n".join(transcript_lines)
    user_content = (
        f"PO Number: {po_num}\n\n"
        f"CONVERSATION TRANSCRIPT:\n{transcript}"
    )

    response = await _client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=OPENAI_TEMPERATURE,
        max_tokens=600,
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
    )

    summary_text: str = (response.choices[0].message.content or "").strip()
    key_intent = _derive_key_intent(messages)
    risk_level = _detect_risk(summary_text, messages)

    return {
        "summary_text": summary_text,
        "key_intent":   key_intent,
        "risk_level":   risk_level,
        "model_used":   OPENAI_MODEL,
    }


async def summarize_handback(history: List[Dict[str, Any]]) -> str:
    """Ask OpenAI to summarize the human-led conversation for the bot to resume naturally."""
    if not history:
        return ""

    # Format history for summarization
    formatted_chat = ""
    for msg in history:
        sender = msg.get("sender_type", "unknown")
        text = msg.get("message_text", "")
        formatted_chat += f"{sender.upper()}: {text}\n"

    prompt = (
        "You are an AI assistant helping a procurement bot resume a conversation. "
        "The recent chat history below was handled by a human operator. "
        "Summarize what was discussed and any outcome so the bot knows the current status. "
        "Keep it concise (one short paragraph). "
        "Do not include system messages in your summary.\n\n"
        f"CHAT HISTORY:\n{formatted_chat}"
    )

    try:
        response = await _client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.3,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.error(f"❌ Error in summarization: {exc}")
        return ""
