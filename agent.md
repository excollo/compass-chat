# Agent Orchestrator — Update Guide
**File:** `agent.py`  
**Project:** Compass Group Procurement  
**Purpose:** This document covers all changes needed in `agent.py` — updated prompts, new helper functions, and the complete flow for all 6 conversation cases and PO update notifications.

---

## 1. What Changes and Why

| What | Change | Why |
|---|---|---|
| `SYSTEM_PROMPT` | Full rewrite | Covers all 6 cases, extracts new INTENT_JSON fields |
| `generate_proactive_message()` | Prompt tightened | Produces cleaner, structured PO update messages |
| `parse_intent_json()` | New helper | Extracts INTENT_JSON from AI output |
| `extract_message_text()` | New helper | Strips INTENT_JSON from chat reply |
| `derive_fields_from_intent()` | New helper | Maps intent → DB fields (state, risk, case_type, priority, SLA) |

---

## 2. Replace `SYSTEM_PROMPT`

Replace the entire `SYSTEM_PROMPT` string (the fallback inside the `except FileNotFoundError` block AND `prompt.txt` if you use it) with the following.

```
You are a procurement assistant for Compass Group India.
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
  → INTENT_JSON: intent=DELAYED, escalate=true, conversation_complete=true (if date known)
     or false (if date not given), extracted_eta=<date if mentioned>

SITUATION D — Vendor flags PARTIAL DELIVERY
  Vendor mentions: partial / thoda / some items / half / sirf X kg / X items nahi milega
  → Acknowledge. Ask what items are short and when the rest will come, if not already stated.
  → If both shortage details and revised date given: acknowledge and say team will follow up.
  → INTENT_JSON: intent=PARTIAL, escalate=true,
     conversation_complete=true (if details known) or false,
     shortage_note=<what is short>, extracted_eta=<when rest comes>

SITUATION E — Vendor says CANNOT DELIVER / REJECTION
  Vendor mentions: nahi denge / cancel / band hai / stock nahi / vehicle nahi /
  production issue / quality reject
  → Acknowledge. Say team will be in touch shortly.
  → INTENT_JSON: intent=REJECTED, escalate=true, conversation_complete=true

SITUATION F — MULTI-PO CONVERSATION
  When po_data_block has more than one PO for this vendor:
  → First message: ask about ALL POs together in one message.
     List each PO number and due date briefly.
  → When vendor replies: confirm the ones that are fine.
     For the ones with issues — drill down only on those.
  → Never ask about resolved POs again.
  → INTENT_JSON: intent=<most severe across all POs>, escalate=<true if any PO has issue>,
     linked_pos=[{po_num, status} for each PO]

SITUATION G — PRICE / PAYMENT / GRN DISPUTE
  Vendor mentions: rate issue / price galat / payment nahi hua / GRN pending /
  invoice / amount
  → Do NOT try to resolve this yourself.
  → Reply: "Understood, I'm connecting you with our procurement team who handles
     this directly. They'll reach out shortly."
  → Immediately set ai_paused = true — AI must stop after this reply.
  → INTENT_JSON: intent=PRICE_DISPUTE, escalate=true, ai_paused=true,
     conversation_complete=true

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

INTENT_JSON: {"intent": "", "po_num": "", "vendor_name": "", "reason": "", "escalate": false, "conversation_complete": false, "extracted_eta": "", "shortage_note": "", "ai_paused": false, "vendor_initiated": false, "linked_pos": [], "confidence_score": 0.0}

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
- confidence_score: your confidence in the classification, between 0.0 and 1.0
```

---

## 3. Replace `generate_proactive_message()` Prompt

Inside the existing `generate_proactive_message()` function, replace the `prompt = (...)` block with this:

```python
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
```

---

## 4. Add These Three Helper Functions

Add these functions to `agent.py` **below** the `call_agent()` function.  
These are used by the calling file (your routes/webhook handler) after `call_agent()` returns.

### 4a. `parse_intent_json()`

```python
import re
import json

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
```

---

### 4b. `extract_message_text()`

```python
def extract_message_text(ai_output: str) -> str:
    """
    Strip the INTENT_JSON line from AI output.
    Returns only the chat message text to send to vendor.
    """
    return re.sub(r'INTENT_JSON:.*', '', ai_output, flags=re.DOTALL).strip()
```

---

### 4c. `derive_fields_from_intent()`

```python
from datetime import datetime, timedelta
from typing import Optional

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
```

---

## 5. Complete Flow — How the Calling File Uses These

This is the pattern your webhook handler / route file should follow after calling `call_agent()`.

### Flow A — Inbound supplier message → AI reply

```
1. Supplier sends WhatsApp message
        ↓
2. Your webhook receives it
        ↓
3. Check chat_history for ai_paused = true on this po_num
   → If true: do NOT call call_agent(). Route to SPOC inbox only.
   → If false: continue
        ↓
4. Save supplier message to chat_history
   sender_type = 'supplier', direction = 'inbound'
        ↓
5. Call call_agent(session_id, message_text, po_data_block)
        ↓
6. Get ai_output string back
        ↓
7. Call parse_intent_json(ai_output)      → intent_data dict
   Call extract_message_text(ai_output)   → clean message to send
   Call derive_fields_from_intent(intent, po_category) → derived dict
        ↓
8. Send clean message to vendor via WhatsApp
        ↓
9. Save AI message to chat_history with all fields populated
   (see field mapping table in section 6 below)
        ↓
10. If intent_data['escalate'] == true:
    → Create case record / notify SPOC dashboard
```

---

### Flow B — PO Update → Vendor Notification

```
1. User updates PO in your system (delivery date / quantity / status)
        ↓
2. Your route detects what changed
   changes = ["Delivery date changed from 13 Apr to 15 Apr"]
        ↓
3. Call generate_proactive_message(po_id, changes)
        ↓
4. Get message_text back
        ↓
5. Send message_text to vendor via WhatsApp
        ↓
6. Save to chat_history:
   sender_type = 'system'
   intent = 'PO_UPDATE'
   direction = 'outbound'
   communication_state = 'awaiting'
   escalation_required = False
   risk_level = 'none'
```

---

### Flow C — Human Takeover

```
1. SPOC clicks "Take Over" on dashboard
        ↓
2. Your route sets ai_paused = true on latest chat_history row for po_num
   (so call_agent is blocked for this thread)
        ↓
3. Save system message to chat_history:
   sender_type = 'system'
   message_text = 'Priya Sharma has taken over. AI is paused.'
   communication_state = 'human_controlled'
   human_takeover_at = now()
   takeover_by = 'Priya Sharma'
        ↓
4. SPOC sends manual message via dashboard
        ↓
5. Save to chat_history:
   sender_type = 'operator'
   direction = 'outbound'
   communication_state = 'human_controlled'
```

---

## 6. Field Mapping — What Populates Each Column

| Column | Source | When |
|---|---|---|
| `intent` | `parse_intent_json()` → `intent` | Every AI reply row |
| `escalation_required` | `parse_intent_json()` → `escalate` | Every AI reply row |
| `extracted_eta` | `parse_intent_json()` → `extracted_eta` | DELAYED / PARTIAL rows only |
| `shortage_note` | `parse_intent_json()` → `shortage_note` | PARTIAL rows only |
| `ai_paused` | `parse_intent_json()` → `ai_paused` | PRICE_DISPUTE rows only |
| `vendor_initiated` | `parse_intent_json()` → `vendor_initiated` | First message rows only |
| `linked_pos` | `parse_intent_json()` → `linked_pos` | Multi-PO rows only |
| `confidence_score` | `parse_intent_json()` → `confidence_score` | Every AI reply row |
| `communication_state` | `derive_fields_from_intent()` | Every AI reply row |
| `risk_level` | `derive_fields_from_intent()` | Every AI reply row |
| `case_type` | `derive_fields_from_intent()` | Exception rows only (null otherwise) |
| `priority` | `derive_fields_from_intent()` + po_category | Exception rows only |
| `sla_due_at` | `derive_fields_from_intent()` → calculated | Exception rows only |
| `assigned_spoc` | Your SPOC mapping lookup | Exception rows only |
| `human_takeover_at` | Your route sets this | Operator takeover rows only |
| `takeover_by` | Your route sets this | Operator takeover rows only |
| `reminder_count` | Your orchestrator increments | System reminder rows only |
| `sla_breached` | Background scheduler checks | Set by scheduler, not agent |
| `vendor_phone` | From PO context | Every row |
| `direction` | Your code sets: inbound/outbound | Every row |
| `sender_type` | Your code sets: ai/supplier/operator/system | Every row |

---

## 7. INTENT_JSON Per Case — Expected Output

| Case | intent | escalate | ai_paused | Extra fields |
|---|---|---|---|---|
| Case 1 — Amul Partial | `PARTIAL` | `true` | `false` | `shortage_note`, `extracted_eta` |
| Case 2 — Suguna No Response | Handled by orchestrator, not AI | — | — | `reminder_count` incremented |
| Case 3 — Mother Dairy Dispute | `PRICE_DISPUTE` | `true` | `true` | — |
| Case 4 — Britannia Happy | `CONFIRMED` | `false` | `false` | `confidence_score=0.95` |
| Case 5 — Capital Vendor Initiated | `DELAYED` | `true` | `false` | `vendor_initiated=true`, `extracted_eta` |
| Case 6 — ITC Multi PO | `PARTIAL` | `true` | `false` | `linked_pos=[...]` array |

---

## 8. What Does NOT Change

- `call_agent()` function signature — no changes needed
- `_memory` session store — no changes needed
- `generate_po_summary()` — no changes needed
- `summarize_handback()` — no changes needed
- `SUMMARY_SYSTEM_PROMPT` — no changes needed
- `_detect_risk()` and `_derive_key_intent()` — no changes needed
- All imports at top of file — no changes needed