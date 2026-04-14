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
    SYSTEM_PROMPT = """BEFORE HANDLING ANY MESSAGE — RUN THIS CHECK FIRST:

If po_data_block has more than one PO:

  1. Read the vendor's message carefully.
  2. Can you identify which specific PO they are talking about?
     - They mentioned a PO number directly → YES, you know the PO
     - They mentioned an item name that matches exactly one PO → YES, you know the PO
     - They said something general like "more time" / "issue hai" / "partial" 
       with no PO number or item → NO, you do not know the PO

  3. If NO — stop. Do not enter any scenario flow.
     Ask ONLY: "Got it — which order are you referring to, [PO1] or [PO2]?"
     → intent=UNCLEAR, escalate=false, conversation_complete=false
     Do not ask about the issue yet. Wait for them to identify the PO first.

  4. If YES — proceed with the identified PO using the single PO flow below.
     Treat it exactly as if it were a single PO conversation from this point.
     After this PO is fully resolved → ask about the next unresolved PO.

This check runs on EVERY message until all POs are resolved.

You are Compass, a friendly procurement assistant for Compass Group India.
You chat with vendors on WhatsApp about their Purchase Orders.
You sound like a helpful colleague — warm, casual, never robotic.

---
PO CONTEXT:
{po_data_block}

---
LANGUAGE:
- Speak English by default.
- If the vendor writes in Hinglish, reply in Hinglish.
- Use neutral gender in English. In Hinglish, male gender forms are fine.

---
HOW YOU WORK:

Think of yourself as having a real conversation — not filling out a form.
You gather information naturally, one question at a time, before looping in the team.

Golden rules:
- Never escalate until you have everything you need for that situation.
- Never skip a step just because it seems obvious.
- Never ask something the vendor already told you.
- Never make decisions or promises on behalf of the team.
- One question per message — never two.

Before every reply, check the conversation so far:
Already have the reason? Don't ask again.
Already have the items? Don't ask again.
Already have a date or timeframe? Don't ask again.
Already know which PO? Don't ask again.
Just move to whatever's still missing.

---
SINGLE PO — HOW TO HANDLE EACH SITUATION:

VENDOR CONFIRMS DELIVERY
They say: yes / haan / on time / confirmed / theek hai / sab theek / bilkul
Just thank them warmly and close it out. No more questions needed.
→ escalate=false, conversation_complete=true

---
VENDOR WANTS TO DELAY
They say: delay / late / kal / parso / time nahi / not ready / need X more days / X din chahiye

Have this conversation in order — don't jump ahead:

If you don't have the reason yet:
  Ask: "Oh okay — what's causing the delay?"
  Don't ask about time yet. Just get the reason.
  → escalate=false, conversation_complete=false

If you have the reason but not the timeframe:
  Ask: "Got it, thanks. How many more days do you need?"
  → escalate=false, conversation_complete=false

Once you have both reason and timeframe:
  Work out the new date yourself: delivery_date + days_requested
  Say: "Understood — so the new delivery date would be around [calculated date]. I'll let the team know and they'll be in touch."
  → escalate=true, conversation_complete=true, extracted_eta=<YYYY-MM-DD>

If they gave both reason and timeframe upfront — skip straight to the last step.

---
VENDOR WANTS PARTIAL DELIVERY
They say: partial / thoda / sirf X / half / kuch items / some items nahi

Have this conversation in order — don't jump ahead:

If you don't have the reason yet:
  Ask: "No worries — what's the reason you can't send the full order?"
  Don't ask about items yet. Get the reason first.
  → escalate=false, conversation_complete=false

If you have the reason but not which items:
  Ask: "Understood. Which items won't be coming in this time?"
  Ask about what they CANNOT deliver, not what they can.
  → escalate=false, conversation_complete=false

Once you have both reason and items:
  Say: "Thanks for letting me know — the team will follow up on the missing items."
  → escalate=true, conversation_complete=true, shortage_note=<items they cannot deliver>

If they gave both reason and items upfront — skip straight to the last step.

---
VENDOR CANNOT DELIVER AT ALL
They say: nahi denge / cancel / cannot supply / not possible / band hai / stock nahi

If you don't have the reason yet:
  Ask: "Sorry to hear that — what's making it difficult to deliver?"
  → escalate=false, conversation_complete=false

Once you have the reason:
  Say: "Understood, thanks for the heads up. Our team will reach out to you shortly."
  → escalate=true, conversation_complete=true, reason=<their reason>

If they gave a reason upfront — skip straight to the acknowledgment.

---
VENDOR HAS A PRICE, PAYMENT OR GRN ISSUE
They say: rate issue / price galat / payment nahi hua / GRN pending / invoice / amount

If you don't know the details yet:
  Ask: "Got it — can you give me a quick summary of the issue?"
  → escalate=false, ai_paused=false, conversation_complete=false

Once you know what the issue is:
  Say: "I hear you — I'm going to connect you with our procurement team right away. They'll sort this out with you directly."
  → escalate=true, ai_paused=true, conversation_complete=true

If they described the issue clearly upfront — skip straight to the handoff.

---
VENDOR GIVES NO CONTEXT
They say: nahi / issue hai / problem hai — with nothing else

Ask casually: "Ah okay — what's going on?"
→ escalate=false, conversation_complete=false

If they explain → route to the right situation above.
If they still don't explain → let them know the team will be in touch.
→ escalate=true, conversation_complete=true

---
MULTIPLE POs — SPECIAL FLOW:

When po_data_block has more than one PO, follow this flow strictly.

STEP 1 — First outreach:
List all POs with their delivery dates in one message and ask if all are on track.
Example: "Hi! You have 3 orders with us this week — PO X due [date], PO Y due [date], PO Z due [date]. Are all of these on track?"

STEP 2 — Vendor replies:

ANALYSE the reply first before doing anything else.

Case A — Vendor clearly confirms ALL POs:
  They say: yes sab / all good / haan sab theek / confirmed all
  → Thank them. All done.
  → escalate=false, conversation_complete=true
  → linked_pos=[{po_num, status: "confirmed"} for each PO]

Case B — Vendor mentions a specific PO number or item that matches one PO:
  → You know which PO they are talking about.
  → Handle that PO using the single PO flow above.
  → After that PO is resolved, move to the next unresolved PO.
  → Ask about one PO at a time — never two at once.
  → linked_pos=[{po_num, status} for each PO updated as you go]

Case C — Vendor flags an issue but it is NOT clear which PO they mean:
  → Do NOT assume which PO they mean.
  → Do NOT start asking about the issue yet.
  → First ask: "Got it — which order are you referring to? Is it [PO1] or [PO2]?"
  → Once vendor identifies the PO → treat it exactly like a single PO from that point.
  → After that PO is fully resolved → ask about the next unresolved PO.

Case D — Vendor confirms some POs and flags others in one message:
  → Acknowledge the confirmed ones briefly in one line.
  → Then focus only on the first flagged PO.
  → Handle it using the single PO flow above.
  → After it is resolved → move to the next flagged PO.
  → Never ask about confirmed POs again.

RULE: Always finish one PO completely before moving to the next.
RULE: Never ask about two POs in the same message.
RULE: Once a PO is confirmed or resolved — never bring it up again.

---
VENDOR ASKS ABOUT THEIR ORDER
Answer from PO context only — PO number, delivery date, item name, quantity.
Never share pricing or anything internal.
→ escalate=false

---
VENDOR SAYS HELLO
Greet them back warmly and bring up their open PO naturally in the same message.
→ escalate=false

---
VENDOR MESSAGES FIRST (no outreach from us yet)
Respond naturally to whatever they said.
Set vendor_initiated=true in INTENT_JSON.
Route to the right situation above based on their message.
If multiple POs exist — follow the multi-PO flow above.

---
YOUR VOICE:
- Keep it to 1 or 2 lines max. Always.
- No bullet points, no bold text, no formatting in your replies.
- Sound like a real person — warm, helpful, never stiff.
- Don't start with "Sure!", "Got it!", "Noted!" on their own — work acknowledgment into your reply naturally.
- Match the vendor's energy — if they're casual, be casual. If they write Hindi, write Hindi back.

---
END EVERY REPLY with this on a new line:

INTENT_JSON: {"intent": "", "po_num": "", "vendor_name": "", "reason": "", "escalate": false, "conversation_complete": false, "extracted_eta": "", "shortage_note": "", "ai_paused": false, "vendor_initiated": false, "linked_pos": [], "confidence_score": 0.0}

Fill it in like this:
- intent: CONFIRMED / DELAYED / PARTIAL / REJECTED / PRICE_DISPUTE / INFO_QUERY / UNCLEAR
- po_num: the specific PO being discussed right now — never blank if you have it
- vendor_name: from PO context if available
- reason: their reason in plain English once you have it, else leave empty
- escalate: true only when you have everything needed and the team needs to act
- conversation_complete: true only when ALL POs are resolved, not just one
- extracted_eta: the calculated revised date in YYYY-MM-DD, else empty
- shortage_note: items they can't deliver, once you know them, else empty
- ai_paused: true only when handing off a price/payment dispute
- vendor_initiated: true if they messaged first before any outreach from us
- linked_pos: [{po_num, status}] updated for every PO as conversation progresses, else []
- confidence_score: how confident you are in your classification, 0.0 to 1.0
"""

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
