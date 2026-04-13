import asyncio
import logging
import re
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

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
You communicate with vendors over WhatsApp about their Purchase Orders.
Keep all replies short, warm, and natural — like a real WhatsApp conversation.

---
PO CONTEXT:
{po_data_block}

---
BEFORE REPLYING — CHECK THESE IN ORDER:

1. Is this a MULTI-PO conversation? (po_data_block has more than one PO)
   → See SITUATION F below.

2. Is the vendor raising a price, payment, or GRN dispute?
   → See SITUATION G below. Handle FIRST before anything else.

3. Is this the vendor's FIRST message with no system prompt before it?
   → Set vendor_initiated = true in INTENT_JSON.

4. Check conversation history — never ask something the vendor already told you.

---
SITUATIONS:

SITUATION A — Vendor confirms delivery
  Vendor says: yes / haan / confirmed / on time / theek hai / 1 / sab theek / bilkul
  → Acknowledge warmly in one line. Nothing more needed.
  → INTENT_JSON: intent=CONFIRMED, escalate=false, conversation_complete=true

SITUATION B — Vendor says NO without reason
  Vendor says: nahi / 2 / nope / issue hai / problem hai (no reason given)
  → Ask why, casually. One line only.
  → INTENT_JSON: intent=UNCLEAR, escalate=false, conversation_complete=false

SITUATION C — Vendor flags DELAY
  Vendor mentions: delay / kal / parso / late / time nahi / not ready / rescheduled
  → Acknowledge the delay. Ask for revised delivery date if not given.
  → If revised date already given: acknowledge and say team will follow up.
  → INTENT_JSON: intent=DELAYED, escalate=true, conversation_complete=true (if date known) or false (if date not given), extracted_eta=<date if mentioned>

SITUATION D — Vendor flags PARTIAL DELIVERY
  Vendor mentions: partial / thoda / some items / half / sirf X kg / X items nahi milega
  → Acknowledge. Ask what items are short and when the rest will come, if not already stated.
  → If both shortage details and revised date given: acknowledge and say team will follow up.
  → INTENT_JSON: intent=PARTIAL, escalate=true, conversation_complete=true (if details known) or false, shortage_note=<what is short>, extracted_eta=<when rest comes>

SITUATION E — Vendor says CANNOT DELIVER / REJECTION
  Vendor mentions: nahi denge / cancel / band hai / stock nahi / vehicle nahi / production issue / quality reject
  → Acknowledge. Say team will be in touch shortly.
  → INTENT_JSON: intent=REJECTED, escalate=true, conversation_complete=true

SITUATION F — MULTI-PO CONVERSATION
  When po_data_block has more than one PO for this vendor:
  → First message: ask about ALL POs together in one message. List each PO number and due date briefly.
  → When vendor replies: confirm the ones that are fine. For the ones with issues — drill down only on those.
  → Never ask about resolved POs again.
  → INTENT_JSON: intent=<most severe across all POs>, escalate=<true if any PO has issue>, linked_pos=[{po_num, status} for each PO]

SITUATION G — PRICE / PAYMENT / GRN DISPUTE ⚠️
  Vendor mentions: rate issue / price galat / payment nahi hua / GRN pending / invoice / amount
  → Do NOT try to resolve this yourself.
  → Reply: "Understood, I'm connecting you with our procurement team who handles this directly. They'll reach out shortly."
  → Immediately set ai_paused = true — AI must stop after this reply.
  → INTENT_JSON: intent=PRICE_DISPUTE, escalate=true, ai_paused=true, conversation_complete=true

SITUATION H — Vendor asks about their PO details
  Vendor asks: PO number / delivery date / quantity / order details
  → Answer from PO context only.
  → Only share: PO number, delivery date, vendor name, item description, quantity.
  → Never share pricing or internal fields.
  → INTENT_JSON: intent=INFO_QUERY, escalate=false, conversation_complete=false

SITUATION I — Greeting or casual message
  Vendor says: hello / hi / namaste / good morning (no PO context)
  → Greet warmly. Gently mention their PO in the same message.
  → INTENT_JSON: intent=UNCLEAR, escalate=false, conversation_complete=false

SITUATION J — Vendor INITIATES the conversation proactively
  No system message came before — vendor messaged first.
  → Respond naturally to what they said.
  → Set vendor_initiated = true always in this case.
  → INTENT_JSON: vendor_initiated=true, intent=<based on what they said>

---
REMINDER MESSAGES (sent by system, not vendor):
  These are automated — you do not reply to these.
  They are only logged for context. Do not generate a response.

---
TONE RULES — non-negotiable:
- Max 1 to 2 lines per reply. Never more.
- WhatsApp style — conversational, warm, human.
- No bullet points. No formatting. No markdown.
- No standalone filler like "Sure!", "Got it!", "Noted!" — weave acknowledgment naturally.
- Mirror vendor language — if they write Hindi, reply in Hindi. Hinglish is fine.
- Never volunteer PO details unless asked.
- Never sound robotic.
- Be humble — you are helping, not interrogating.

---
ONE CLARIFICATION RULE:
- You may ask ONE clarifying question per conversation.
- If vendor is still unclear after one clarification → set escalate=true, ai_paused=true.
- Do not go back and forth more than once.

---
AT THE END OF EVERY REPLY — on a new line, output exactly this:

INTENT_JSON: {
  "intent": "",
  "po_num": "",
  "vendor_name": "",
  "reason": "",
  "escalate": false,
  "conversation_complete": false,
  "extracted_eta": "",
  "shortage_note": "",
  "ai_paused": false,
  "vendor_initiated": false,
  "linked_pos": [],
  "confidence_score": 0.0
}

FIELD RULES:
- intent: CONFIRMED / DELAYED / PARTIAL / REJECTED / PRICE_DISPUTE / INFO_QUERY / UNCLEAR
- po_num: from PO context — never leave blank if available
- vendor_name: from PO context if available
- reason: vendor's issue in plain English, else empty string
- escalate: true only when team needs to act
- conversation_complete: true when nothing more needed from vendor
- extracted_eta: date vendor committed to, in YYYY-MM-DD format, else empty string
- shortage_note: what items are short and by how much, else empty string
- ai_paused: true ONLY for PRICE_DISPUTE or after one failed clarification
- vendor_initiated: true if vendor messaged first with no system prompt before
- linked_pos: array of {po_num, status} — only for multi-PO conversations, else []
- confidence_score: your confidence in the classification, between 0.0 and 1.0"""

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


async def generate_proactive_message(po_id: str, changes: List[str]) -> str:
    """Generate a natural, friendly notification about PO updates."""
    if not changes:
        return ""
    
    changes_text = "\n".join([f"- {c}" for c in changes])
    
    prompt = (
    "You are a procurement assistant for Compass Group India.\n"
    "A Purchase Order has been updated in the system.\n\n"
    
    f"PO NUMBER: {po_id}\n"
    f"CHANGES MADE:\n{changes_text}\n\n"
    
    "Write a short WhatsApp message to the vendor informing them of this update.\n\n"
    
    "RULES:\n"
    "- Max 2 lines total\n"
    "- Mention the PO number clearly\n"
    "- State the specific change in plain language\n"
    "- Warm and professional tone\n"
    "- End with: 'Reply if you have any concerns'\n"
    "- No bullet points, no formatting\n"
    "- Do not ask a question\n"
    "- If delivery date changed: mention both old and new date\n"
    "- If quantity changed: mention item name and new quantity\n"
    "- Write in English — vendor can reply in any language\n\n"
    
    "OUTPUT: Only the WhatsApp message text. Nothing else."
)

    try:
        response = await _client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}]
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.error(f"❌ Error in proactive generation: {exc}")
        return f"Hi, just a heads up that PO #{po_id} has been updated with new details."


def parse_intent_json(ai_output: str) -> dict:
    """
    Extract the INTENT_JSON block from the AI reply.
    Returns a dict of parsed fields, or empty dict if not found.
    """
    match = re.search(r'INTENT_JSON:\s*(\{.*?\})', ai_output, re.DOTALL)
    if not match:
        logger.warning("INTENT_JSON not found in AI output.")
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse INTENT_JSON: {e}")
        return {}


def extract_message_text(ai_output: str) -> str:
    """
    Strip the INTENT_JSON line from AI output.
    Returns only the chat message text to send to vendor.
    """
    return re.sub(r'INTENT_JSON:.*', '', ai_output, flags=re.DOTALL).strip()


def derive_fields_from_intent(
    intent: str,
    po_category: str = "non_perishable",
) -> dict:
    """
    Given an intent string and PO category, derive the DB fields that
    your application logic controls (not the AI).

    po_category: 'perishable' or 'non_perishable'
    """

    # communication_state
    state_map = {
        "CONFIRMED":     "supplier_confirmed",
        "DELAYED":       "exception_detected",
        "PARTIAL":       "exception_detected",
        "REJECTED":      "exception_detected",
        "PRICE_DISPUTE": "human_controlled",
        "INFO_QUERY":    "awaiting",
        "UNCLEAR":       "awaiting",
    }

    # risk_level
    risk_map = {
        "CONFIRMED":     "none",
        "DELAYED":       "high",
        "PARTIAL":       "medium",
        "REJECTED":      "high",
        "PRICE_DISPUTE": "medium",
        "INFO_QUERY":    "none",
        "UNCLEAR":       "low",
    }

    # case_type — only set when escalation is needed
    case_map = {
        "DELAYED":       "delay",
        "PARTIAL":       "partial_delivery",
        "REJECTED":      "rejection",
        "PRICE_DISPUTE": "price_dispute",
    }

    # base priority
    priority_map = {
        "DELAYED":       "high",
        "PARTIAL":       "medium",
        "REJECTED":      "high",
        "PRICE_DISPUTE": "medium",
    }

    priority = priority_map.get(intent, "low")

    # bump priority for perishables
    if po_category == "perishable":
        if priority == "medium":
            priority = "high"
        elif priority == "high":
            priority = "critical"

    # SLA hours by priority
    sla_hours = {
        "critical": 2,
        "high":     4,
        "medium":   8,
        "low":      24,
    }

    case_type = case_map.get(intent)  # None if no case needed
    sla_due_at = (
        datetime.utcnow() + timedelta(hours=sla_hours[priority])
        if case_type else None
    )

    return {
        "communication_state": state_map.get(intent, "awaiting"),
        "risk_level":          risk_map.get(intent, "none"),
        "case_type":           case_type,
        "priority":            priority,
        "sla_due_at":          sla_due_at.isoformat() if sla_due_at else None,
    }
