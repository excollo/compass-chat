Prompt
Make the following changes to the existing Python FastAPI backend in
backend_agent/. Do not change agent.py, intent_parser.py, or the
/webhook/chat endpoint signature. Match existing code style exactly.

Change 1 — Add Supabase client to database.py
At the bottom of database.py, after all existing asyncpg code, add:
pythonimport os
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

_supabase_client: Client = None


def get_supabase() -> Client:
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client


async def get_thread_state(po_num: str) -> dict:
    """
    Check Supabase for the current thread_state of a PO.
    Returns can_bot_send=True only when thread_state is 'bot_active'.
    Called before every bot send to gate the conversation.
    """
    try:
        supabase = get_supabase()
        result = supabase.table("selected_open_po_line_items") \
            .select("thread_state, bot_context_summary") \
            .eq("po_num", po_num) \
            .single() \
            .execute()

        if not result.data:
            return {
                "thread_state": "bot_active",
                "bot_context_summary": None,
                "can_bot_send": True
            }

        state = result.data.get("thread_state", "bot_active")
        return {
            "thread_state": state,
            "bot_context_summary": result.data.get("bot_context_summary"),
            "can_bot_send": state == "bot_active"
        }
    except Exception as exc:
        # if Supabase check fails, allow bot to continue
        # to avoid blocking the entire conversation
        print(f"⚠️ [GATE] Supabase thread state check failed: {exc} — defaulting to bot_active")
        return {
            "thread_state": "bot_active",
            "bot_context_summary": None,
            "can_bot_send": True
        }

Change 2 — Add thread state gate to process_chat in main.py
In the existing process_chat async function, add the thread state check as
the very first operation after stripping string fields. Insert it between the
print log line and step 2 (Fetch PO data):
pythonasync def process_chat(body: ChatWebhookBody) -> None:
    # 1. Strip all incoming string fields (existing — do not change)
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
        ...

Change 3 — Inject bot_context_summary into agent call in main.py
Find the existing step 3 where call_agent is called. Replace only the
call_agent call (do not change anything else around it):
python    # 3. Call OpenAI agent
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

Change 4 — Add to backend_agent/.env
SUPABASE_URL=your_supabase_project_url
SUPABASE_SERVICE_KEY=your_supabase_service_role_key
Get these from Supabase dashboard → Settings → API.
Use the service_role key (not anon key) so the backend can bypass RLS.

Change 5 — Add to requirements.txt
supabase>=2.0.0

Do NOT change

agent.py
intent_parser.py
config.py
The /webhook/chat endpoint signature or response
The existing asyncpg pool logic in database.py
The httpx POST to Node.js backend


How to test after building
bash# 1. Start Python backend
cd backend_agent && python main.py

# 2. Set thread_state to human_controlled in Supabase for one PO
# UPDATE selected_open_po_line_items SET thread_state = 'human_controlled' WHERE po_num = '4100260367'

# 3. Send a test message to the webhook
curl -X POST http://localhost:8000/webhook/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"4100260367","po_id":"4100260367","supplier_name":"Test","vendor_phone":"9999999999","message_text":"test","timestamp":"2026-01-01T00:00:00Z"}'

# Expected: logs show "Bot paused" and no reply is sent to server.js
