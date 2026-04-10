import logging
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel

from agent import call_agent, summarize_handback
from config import BACKEND_URL, PORT
from database import close_pool, fetch_po_data, format_po_block, fetch_chat_history, update_thread_state_db
from intent_parser import parse_intent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Compass Python Orchestration Service starting up…")
    yield
    logger.info("Shutting down — closing Postgres pool…")
    await close_pool()


app = FastAPI(
    title="Compass Orchestration Service",
    description="Python AI microservice for vendor chat orchestration",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class ChatWebhookBody(BaseModel):
    session_id: str
    po_id: str
    supplier_name: str
    vendor_phone: str
    message_text: str
    timestamp: str


# ---------------------------------------------------------------------------
# Background task — all AI work happens here
# ---------------------------------------------------------------------------

async def process_chat(body: ChatWebhookBody) -> None:
    # 1. Strip all incoming string fields
    session_id   = body.session_id.strip()
    po_id        = body.po_id.strip()
    vendor_phone = body.vendor_phone.strip()
    message_text = body.message_text.strip()

    print(f"\n🚀 [AGENT] Received message: '{message_text}' for PO: {po_id}")
    logger.info("Processing chat | session=%s po=%s", session_id, po_id)

    # ── THREAD STATE GATE — check before doing anything ───────────────
    from database import get_thread_state
    thread_info = await get_thread_state(po_id)

    if not thread_info["can_bot_send"]:
        print(f"🛑 [AGENT] Bot paused for PO {po_id} — "
              f"thread_state: {thread_info['thread_state']}")
        logger.info(
            "Bot paused | po=%s | state=%s",
            po_id, thread_info["thread_state"]
        )
        return  # stop here — human is in control, do nothing

    print(f"✅ [AGENT] Thread state: {thread_info['thread_state']} — proceeding")
    # ── END GATE ───────────────────────────────────────────────────────

    # 2. Fetch PO data from Postgres (existing — do not change)
    try:
        po_data = await fetch_po_data(po_id)
        print(f"📊 [AGENT] PO Data Fetch: {'SUCCESS' if po_data else 'FAILED/NOT FOUND'}")
    except Exception as exc:
        print(f"❌ [AGENT] Postgres fetch failed: {exc}")
        logger.error("Postgres fetch failed for po_id=%s: %s", po_id, exc)
        po_data = None

    po_data_block = format_po_block(po_data)

    # 3. Call OpenAI agent
    print("🤖 [AGENT] Calling OpenAI...")
    try:
        # inject context summary if bot is resuming after human takeover
        context_addon = ""
        if thread_info.get("bot_context_summary"):
            context_addon = (
                f"\n\nCONTEXT FROM PREVIOUS HUMAN CONVERSATION:\n"
                f"{thread_info['bot_context_summary']}\n\n"
                f"Use this context to continue naturally. "
                f"Do not re-ask questions that were already answered by the operator."
            )

        ai_output = await call_agent(
            session_id,
            message_text,
            po_data_block + context_addon  # inject context into PO block
        )
        print(f"✅ [AGENT] OpenAI Response: {ai_output[:50]}...")
    except Exception as exc:
        print(f"❌ [AGENT] OpenAI call failed: {exc}")
        logger.error("OpenAI call failed for session=%s: %s", session_id, exc)
        return 

    # 4. Parse intent
    reply_text, intent_data, should_escalate, admin_message = parse_intent(
        ai_output, po_id, message_text
    )
    po_num = intent_data["po_num"]

    # 5. POST bot reply back to Node.js backend
    payload = {
        "po_id":         po_num,
        "sender_type":   "bot",
        "sender_label":  "Compass Bot",
        "message_text":  reply_text,
        "vendor_phone":  vendor_phone,
        "supplier_name": body.supplier_name,
        "intent":        intent_data.get("intent"),
        "reason":        intent_data.get("reason", ""),
        "escalate":      should_escalate,
        "admin_message": admin_message,
        "conversation_complete": intent_data.get("conversation_complete", False)
    }

    print(f"📤 [AGENT] Sending reply to Node Backend: {BACKEND_URL}/api/chat-message")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{BACKEND_URL}/api/chat-message", json=payload)
            print(f"🏁 [AGENT] Backend POST Status: {resp.status_code}")
            logger.info("Backend POST → %s %s", resp.status_code, resp.text[:120])
        except Exception as exc:
            print(f"❌ [AGENT] Backend POST failed: {exc}")
            logger.error("Backend POST failed: %s", exc)




# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@app.post("/webhook/chat", status_code=200)
async def webhook_chat(body: ChatWebhookBody, background_tasks: BackgroundTasks):
    """
    Receive a vendor chat message and immediately return 200 OK.
    All AI processing is done asynchronously in the background.
    """
    background_tasks.add_task(process_chat, body)
    return {"status": "accepted"}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", status_code=200)
async def health():
    return {"status": "ok"}


class HandbackBody(BaseModel):
    po_id: str


@app.post("/webhook/handback")
async def webhook_handback(body: HandbackBody, background_tasks: BackgroundTasks):
    """
    Triggered when a human hands control back to the bot.
    Summarizes the human conversation and resets state.
    """
    po_id = body.po_id

    async def process_handback():
        logger.info(f"Generating handback summary for PO: {po_id}")
        # 1. Fetch recent messages
        history = await fetch_chat_history(po_id)
        
        # 2. Call LLM to summarize
        summary = await summarize_handback(history)
        logger.info(f"Handback summary generated: {summary[:100]}...")

        # 3. Update Supabase thread_state to 'bot_active'
        await update_thread_state_db(po_id, "bot_active", bot_context_summary=summary)
        logger.info(f"PO {po_id} state updated to bot_active with summary.")

    background_tasks.add_task(process_handback)
    return {"status": "accepted"}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
