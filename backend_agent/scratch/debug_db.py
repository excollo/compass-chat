import asyncio
from database import fetch_chat_history_by_po
import os
from dotenv import load_dotenv

load_dotenv()

async def debug_chat():
    po_num = "4100260584"
    print(f"Checking chat history for PO: {po_num}")
    messages = await fetch_chat_history_by_po(po_num)
    print(f"Found {len(messages)} messages.")
    for msg in messages:
        print(f"[{msg['sent_at']}] {msg['sender_type']}: {msg['message_text'][:30]}...")

    # Also check if there are ANY messages at all
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        all_po_nums = await conn.fetch("SELECT DISTINCT po_num FROM chat_history LIMIT 10")
        print(f"Available PO numbers in chat_history: {[r['po_num'] for r in all_po_nums]}")

if __name__ == "__main__":
    asyncio.run(debug_chat())
