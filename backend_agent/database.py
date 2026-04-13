import asyncpg
from typing import Optional, Dict, Any, List
from config import DATABASE_URL

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Return (or create) the shared connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _pool


async def close_pool() -> None:
    """Gracefully close the connection pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def ensure_tables() -> None:
    """Create application-managed tables if they do not already exist."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS public.po_summaries (
                id UUID NOT NULL DEFAULT gen_random_uuid(),
                po_num TEXT NOT NULL,
                summary_text TEXT NOT NULL,
                key_intent TEXT NULL,
                risk_level TEXT NULL DEFAULT 'none',
                message_count INT NULL DEFAULT 0,
                generated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                model_used TEXT NULL,
                CONSTRAINT po_summaries_pkey PRIMARY KEY (id)
            );
        """)


async def fetch_chat_history_by_po(po_num: str) -> List[Dict[str, Any]]:
    """Fetch chat_history rows for a PO ordered by sent_at ASC using asyncpg pool."""
    pool = await get_pool()
    query = """
        SELECT id, po_num, sender_type, message_text, direction,
               escalation_required, vendor_phone, sent_at, intent
        FROM chat_history
        WHERE po_num = $1
        ORDER BY sent_at ASC
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, po_num)
    return [dict(row) for row in rows]


async def insert_po_summary(
    po_num: str,
    summary_text: str,
    key_intent: str,
    risk_level: str,
    message_count: int,
    model_used: str,
) -> Dict[str, Any]:
    """Insert a generated PO summary into po_summaries and return the stored row."""
    pool = await get_pool()
    query = """
        INSERT INTO public.po_summaries
            (po_num, summary_text, key_intent, risk_level, message_count, model_used)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, po_num, summary_text, key_intent, risk_level,
                  message_count, generated_at, model_used
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            query, po_num, summary_text, key_intent, risk_level, message_count, model_used
        )
    return dict(row)



async def fetch_po_data(po_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch PO line item data from Postgres for the given po_id.
    Includes quantity and unit info for better AI context.
    """
    pool = await get_pool()
    query = """
        SELECT po_num, po_date, delivery_date, vendor_name, vendor_code,
               article_description, po_quantity, unit, vendor_phone, status
        FROM selected_open_po_line_items
        WHERE po_num = $1
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, po_id)

    if row is None:
        return None
    return dict(row)


def format_po_block(po_data: Optional[Dict[str, Any]]) -> str:
    """
    Format the PO data into a structured string for the AI agent context.
    """
    if po_data is None:
        return "PO Data from Database:\nNo PO data found for this PO ID."

    return (
        f"PO Data from Database:\n"
        f"PO Number: {po_data.get('po_num', '')}\n"
        f"PO Date: {po_data.get('po_date', '')}\n"
        f"Delivery Date: {po_data.get('delivery_date', '')}\n"
        f"Vendor: {po_data.get('vendor_name', '')} ({po_data.get('vendor_code', '')})\n"
        f"Quantity: {po_data.get('po_quantity', '')} {po_data.get('unit', '')}\n"
        f"Items: {po_data.get('article_description', '')}\n"
        f"Status: {po_data.get('status', '')}"
    )


import os
import httpx

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")


async def get_thread_state(po_num: str) -> dict:
    """
    Check Supabase via REST API for the current thread_state of a PO.
    Returns can_bot_send=True only when thread_state is 'bot_active'.
    """
    try:
        # Construct the PostgREST URL
        # e.g., https://xyz.supabase.co/rest/v1/selected_open_po_line_items?po_num=eq.123&select=thread_state,bot_context_summary
        url = f"{SUPABASE_URL}/rest/v1/selected_open_po_line_items"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}"
        }
        params = {
            "po_num": f"eq.{po_num}",
            "select": "thread_state,bot_context_summary"
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

        if not data:
            return {
                "thread_state": "bot_active",
                "bot_context_summary": None,
                "can_bot_send": True
            }

        result = data[0]
        state = result.get("thread_state", "bot_active")
        return {
            "thread_state": state,
            "bot_context_summary": result.get("bot_context_summary"),
            "can_bot_send": state == "bot_active"
        }
    except Exception as exc:
        print(f"⚠️ [GATE] Supabase REST check failed: {exc} — defaulting to bot_active")
        return {
            "thread_state": "bot_active",
            "bot_context_summary": None,
            "can_bot_send": True
        }


async def fetch_chat_history(po_id: str) -> list:
    """Fetch the full chat history for a PO to summarize context."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/chat_history"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}"
        }
        params = {
            "po_num": f"eq.{po_id}",
            "order": "sent_at.asc"
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        print(f"❌ [DB] Failed to fetch chat history: {exc}")
        return []


async def update_thread_state_db(po_num: str, state: str, bot_context_summary: str = None) -> bool:
    """Update the thread state and bot_context_summary in Supabase."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/selected_open_po_line_items"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Prefer": "return=minimal"
        }
        params = {"po_num": f"eq.{po_num}"}
        payload = {"thread_state": state}
        
        if bot_context_summary:
            from datetime import datetime
            payload["bot_context_summary"] = bot_context_summary
            payload["handed_back_at"] = datetime.utcnow().isoformat()

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(url, headers=headers, params=params, json=payload)
            resp.raise_for_status()
            return True
    except Exception as exc:
        print(f"❌ [DB] Failed to update thread state: {exc}")
        return False

