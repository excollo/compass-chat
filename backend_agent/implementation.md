# Compass Group — Python Orchestration Service
## Implementation Prompt for Antigravity

---

## 1. What to Build

Build a Python backend service that replaces an existing n8n workflow. This service receives vendor chat messages, processes them through an OpenAI AI agent with conversation memory and Postgres PO data context, parses the AI output, and POSTs the bot reply back to a separate Node.js backend.

This is a standalone Python microservice — it does not handle the frontend, SSE, or Postgres schema. It only handles AI orchestration.

**Tech stack:**
- Python 3.11+
- FastAPI — single POST endpoint
- OpenAI Python SDK — gpt-4o-mini, temperature 0.1
- asyncpg or psycopg2 — Postgres connection
- httpx — async HTTP POST to backend
- python-dotenv — ENV loading
- uvicorn — ASGI server

---

## 2. The Single API Endpoint

Build exactly one endpoint:

```
POST /webhook/chat
```

This receives the vendor's message from the Node.js backend. It must return `200 OK` immediately and process the AI logic asynchronously in the background using FastAPI `BackgroundTasks`.

**Incoming request body (JSON):**

| Field | Type | Description |
|---|---|---|
| session_id | string | Unique session key — same as po_id for this demo |
| po_id | string | Purchase Order number e.g. 4100260367 |
| supplier_name | string | Vendor display name |
| vendor_phone | string | Vendor phone — strip whitespace and \r\n on receipt |
| message_text | string | The vendor's actual message |
| timestamp | string | ISO timestamp of message |

**CRITICAL — strip all incoming string fields:**
```python
vendor_phone = body.vendor_phone.strip()
message_text = body.message_text.strip()
po_id        = body.po_id.strip()
session_id   = body.session_id.strip()
```

---

## 3. Postgres — PO Data Fetch

Connect to Postgres using `DATABASE_URL` from ENV.

On every request, run this query using the incoming `po_id`:

```sql
SELECT po_num, po_date, delivery_date, vendor_name, vendor_code,
       unit_description, vendor_phone, status
FROM selected_open_po_line_items
WHERE po_num = $1
```

Fetch result as a dict. If no rows returned, set `po_data = None`.

Inject `po_data` as a formatted string into the system prompt before calling OpenAI.

**Format the injected PO data block exactly like this:**
```
PO Data from Database:
PO Number: {po_num}
PO Date: {po_date}
Delivery Date: {delivery_date}
Vendor: {vendor_name} ({vendor_code})
Items: {unit_description}
Status: {status}
```

If `po_data` is None, inject:
```
PO Data from Database:
No PO data found for this PO ID.
```

---

## 4. Session Memory

Maintain conversation history per `session_id` in a Python dict. Use a context window of last **20 messages**.

Each memory entry is a dict: `{ "role": "user" | "assistant", "content": "..." }`

**On each request, in order:**
1. Load existing history for `session_id` from memory dict
2. Append new user message: `{ "role": "user", "content": message_text }`
3. Call OpenAI with full history (see Section 5)
4. Append assistant reply: `{ "role": "assistant", "content": reply_text }`
5. Trim history to last 20 messages
6. Save back to memory dict

Session memory key = `session_id` from incoming request body.

For the demo, a simple Python dict is fine. Use an asyncio lock if running async workers.

---

## 5. OpenAI Agent Call

Use the OpenAI Python SDK. Call `chat.completions.create` with these exact settings:

| Parameter | Value |
|---|---|
| model | gpt-4o-mini |
| temperature | 0.1 |
| max_tokens | 500 |
| messages | [ system_message ] + session_history_with_current_message |

**Messages array structure:**
- Index 0: `{ "role": "system", "content": SYSTEM_PROMPT + "\n\n" + PO_DATA_BLOCK }`
- Index 1 to N: full session history including the current user message already appended

The system prompt content loads from `prompt.txt` in the project root at startup. Leave it as a placeholder — content will be provided separately.

```python
with open("prompt.txt", "r") as f:
    SYSTEM_PROMPT = f.read()
```

Extract AI output from:
```python
ai_output = response.choices[0].message.content
```

---

## 6. Parse Intent Logic

After getting AI output, run this parsing logic. This is a Python port of the existing n8n code node.

### 6.1 Extract INTENT_JSON

```python
import re, json

match = re.search(r'INTENT_JSON:\s*(\{[\s\S]*?\})', ai_output)
try:
    intent_data = json.loads(match.group(1)) if match else {}
except Exception:
    intent_data = {}

# Defaults if parsing fails
intent_data.setdefault('intent', 'UNCLEAR')
intent_data.setdefault('po_num', '')
intent_data.setdefault('vendor_name', '')
intent_data.setdefault('reason', '')
intent_data.setdefault('escalate', False)
intent_data.setdefault('conversation_complete', False)
```

### 6.2 Clean Reply Text

```python
reply_text = re.sub(r'INTENT_JSON:[\s\S]*$', '', ai_output).strip()
```

### 6.3 po_num Fallback

```python
po_num = intent_data.get('po_num') or po_id
```

Always fallback to the incoming `po_id`. Never send an empty `po_num` to the backend.

### 6.4 Escalation Logic

```python
ESCALATE_INTENTS = [
    'PARTIAL', 'REJECTED', 'DELAYED',
    'PRICE_UPDATE', 'QUANTITY_CHANGE',
    'PO_CANCELLATION', 'PAYMENT_ISSUE', 'QUALITY_ISSUE'
]

should_escalate = (
    intent_data.get('escalate') == True
    and intent_data.get('intent') in ESCALATE_INTENTS
    and intent_data.get('intent') != 'INFO_QUERY'
)
```

### 6.5 Priority and Label Maps

```python
PRIORITY_MAP = {
    'PO_CANCELLATION': '🔴 CRITICAL',
    'REJECTED':        '🔴 HIGH',
    'PRICE_UPDATE':    '🟠 HIGH',
    'PAYMENT_ISSUE':   '🟠 HIGH',
    'PARTIAL':         '🟡 MEDIUM',
    'DELAYED':         '🟡 MEDIUM',
    'QUANTITY_CHANGE': '🟡 MEDIUM',
    'QUALITY_ISSUE':   '🟡 MEDIUM',
}

INTENT_LABELS = {
    'PARTIAL':         'Partial Delivery',
    'REJECTED':        'Rejection',
    'DELAYED':         'Delivery Delay',
    'PRICE_UPDATE':    'Price Update Request',
    'QUANTITY_CHANGE': 'Quantity Change Request',
    'PO_CANCELLATION': 'PO Cancellation Request',
    'PAYMENT_ISSUE':   'Payment Query',
    'QUALITY_ISSUE':   'Quality / Spec Issue',
}
```

### 6.6 Build admin_message

Only build when `should_escalate` is True:

```python
if should_escalate:
    priority  = PRIORITY_MAP.get(intent_data['intent'], '🟡')
    label     = INTENT_LABELS.get(intent_data['intent'], intent_data['intent'])
    reason    = intent_data.get('reason') or 'See vendor message'

    admin_message = (
        f"{priority} *PO Exception — Action Required*\n\n"
        f"*PO Number:* {po_num}\n"
        f"*Vendor:* {intent_data.get('vendor_name', '')}\n"
        f"*Issue Type:* {label}\n"
        f"*Vendor Said:* \"{message_text}\"\n"
        f"*Details:* {reason}\n\n"
        f"Please review and contact vendor."
    )
else:
    admin_message = ''
```

---

## 7. POST Bot Reply to Backend

After parsing, POST to the Node.js backend using `httpx.AsyncClient`:

```
POST {BACKEND_URL}/api/chat-message
```

**Request body:**

| Field | Value |
|---|---|
| po_id | po_num (with fallback applied) |
| sender_type | "bot" (hardcoded) |
| sender_label | "Compass Bot" (hardcoded) |
| message_text | reply_text (cleaned, no INTENT_JSON) |
| intent | intent_data['intent'] |
| reason | intent_data['reason'] |
| escalate | should_escalate (boolean) |
| admin_message | admin_message (empty string if no escalation) |

```python
async with httpx.AsyncClient(timeout=10.0) as client:
    try:
        await client.post(
            f"{BACKEND_URL}/api/chat-message",
            json={
                "po_id":         po_num,
                "sender_type":   "bot",
                "sender_label":  "Compass Bot",
                "message_text":  reply_text,
                "intent":        intent_data.get('intent'),
                "reason":        intent_data.get('reason', ''),
                "escalate":      should_escalate,
                "admin_message": admin_message,
            }
        )
    except Exception as e:
        print(f"Backend POST failed: {e}")
```

Log errors but do not crash or retry if the POST fails.

---

## 8. File Structure

```
/app
  main.py           ← FastAPI app, POST /webhook/chat, BackgroundTasks
  agent.py          ← OpenAI call + session memory logic
  database.py       ← Postgres connection + PO fetch query
  intent_parser.py  ← Parse Intent — regex, escalation, admin_message
  config.py         ← ENV loading via python-dotenv
  prompt.txt        ← System prompt placeholder (filled separately)
  .env              ← Environment variables
  requirements.txt  ← All dependencies
  Dockerfile        ← Optional for deployment
```

---

## 9. Environment Variables

| Variable | Example | Description |
|---|---|---|
| OPENAI_API_KEY | sk-... | OpenAI API key |
| OPENAI_MODEL | gpt-4o-mini | Model name |
| OPENAI_TEMPERATURE | 0.1 | Keep low for deterministic replies |
| DATABASE_URL | postgresql://user:pass@host/db | Postgres connection string |
| BACKEND_URL | https://compass-chat.onrender.com | Node.js backend base URL — no trailing slash |
| SESSION_MEMORY_WINDOW | 20 | Max messages to keep per session |
| PORT | 8000 | App port |

**.env file:**
```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
OPENAI_TEMPERATURE=0.1
DATABASE_URL=postgresql://user:password@host:5432/dbname
BACKEND_URL=https://compass-chat.onrender.com
SESSION_MEMORY_WINDOW=20
PORT=8000
```

---

## 10. requirements.txt

```
fastapi
uvicorn[standard]
openai
asyncpg
httpx
python-dotenv
pydantic
```

---

## 11. What NOT to Build

- No frontend — backend microservice only
- No SSE or WebSocket — that stays in the Node.js backend
- No Postgres schema changes — tables already exist, only read from them
- No authentication — internal service, no auth needed
- No WhatsApp integration — demo uses chat frontend
- No database writes — only SELECT from Postgres
- Do not modify the system prompt — leave prompt.txt as a placeholder

---

## 12. Critical Notes

**vendor_phone stripping** — incoming data may have trailing `\r\n` or whitespace. Always `.strip()` all string fields immediately on receipt before any processing.

**Return 200 immediately** — the endpoint must respond before doing any AI work. Use FastAPI `BackgroundTasks`. The Node.js backend does not wait for the bot reply — it arrives via a separate POST callback.

**Failure handling** — if OpenAI call fails or times out, log the error and do not retry. The vendor simply won't receive a reply. Acceptable for demo.

**Session identity** — `session_id` and `po_id` are the same value in this demo. Session memory is always keyed by `session_id`.

**System prompt** — load `prompt.txt` once at startup into a module-level variable. Do not read the file on every request.

**po_num never blank** — always apply the fallback `po_num = intent_data.get('po_num') or po_id` before sending to the backend. An empty `po_id` will cause the backend to reject the request.