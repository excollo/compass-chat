import asyncio
import httpx
import json
from main import ChatWebhookBody, process_chat
from database import fetch_po_data
from agent import call_agent
from intent_parser import parse_intent

async def debug_flow():
    test_body = ChatWebhookBody(
        session_id="4100260294",
        po_id="4100260294",
        supplier_name="Ess Emm Corporation",
        vendor_phone="1234567890",
        message_text="give me 2 more days",
        timestamp="2024-03-03T12:00:00Z"
    )
    
    print("\n--- 🔍 STARTING AGENT DEBUG ---")
    
    # 1. Test Database
    print("\n1. Testing Database Fetch...")
    try:
        po_data = await fetch_po_data(test_body.po_id)
        if po_data:
            print(f"✅ PO Found: {po_data.get('vendor_name')}")
        else:
            print("⚠️ PO Not Found in database (This might be okay if using generic context)")
    except Exception as e:
        print(f"❌ Database Error: {e}")

    # 2. Test OpenAI
    print("\n2. Testing OpenAI Call...")
    try:
        from database import format_po_block
        block = format_po_block(po_data)
        ai_output = await call_agent(test_body.session_id, test_body.message_text, block)
        print(f"✅ OpenAI Response received ({len(ai_output)} chars)")
        print(f"--- AI Raw Output ---\n{ai_output}\n-------------------")
    except Exception as e:
        print(f"❌ OpenAI Error: {e}")
        return

    # 3. Test Parsing
    print("\n3. Testing Intent Parsing...")
    try:
        reply_text, intent_data, should_escalate, admin_message = parse_intent(
            ai_output, test_body.po_id, test_body.message_text
        )
        print(f"✅ Intent: {intent_data.get('intent')}")
        print(f"✅ Escalate: {should_escalate}")
        if not reply_text:
            print("⚠️ Warning: Empty reply text!")
    except Exception as e:
        print(f"❌ Parsing Error: {e}")

    # 4. Test Backend POST
    print("\n4. Testing Backend Connection (Node.js)...")
    from config import BACKEND_URL
    print(f"Targeting: {BACKEND_URL}/api/chat-message")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{BACKEND_URL}/api/chat-message", json={"test": "ping"})
            print(f"✅ Node Backend reachable (Status: {resp.status_code})")
    except Exception as e:
        print(f"❌ Backend Unreachable: {e}")

if __name__ == "__main__":
    asyncio.run(debug_flow())
