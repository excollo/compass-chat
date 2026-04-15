import asyncpg
from typing import Optional, Dict, Any, List
from config import DATABASE_URL

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Return (or create) the shared connection pool."""
    global _pool
    if _pool is None:
        # statement_cache_size=0 is REQUIRED for Supabase/PgBouncer in Transaction mode
        _pool = await asyncpg.create_pool(
            DATABASE_URL, 
            min_size=1, 
            max_size=5,
            statement_cache_size=0
        )
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


async def fetch_all_vendor_pos(vendor_phone: str, po_id: str = "") -> List[Dict[str, Any]]:
    """
    Fetch ALL open POs belonging to the same vendor entity as the phone number.
    Uses vendor_code to link POs that might have different phone numbers.
    """
    pool = await get_pool()
    
    # 1. Find vendor_codes associated with this phone number (phone-normalized)
    #    and/or the current PO as fallback.
    code_query = """
        SELECT DISTINCT vendor_code
        FROM selected_open_po_line_items
        WHERE
          (
            $1 <> ''
            AND vendor_phone IS NOT NULL
            AND regexp_replace(vendor_phone, '\\D', '', 'g') =
                regexp_replace($1, '\\D', '', 'g')
          )
          OR ($2 <> '' AND po_num = $2)
    """
    
    async with pool.acquire() as conn:
        code_rows = await conn.fetch(code_query, vendor_phone or "", po_id or "")
        vendor_codes = [r['vendor_code'] for r in code_rows if r['vendor_code']]
        
        if not vendor_codes:
            # Final fallback: return current PO context at least, even when vendor_code is missing.
            if po_id:
                po_only_query = """
                    SELECT po_num, po_date, delivery_date, vendor_name, vendor_code,
                           article_description, po_quantity, unit, status, vendor_phone
                    FROM selected_open_po_line_items
                    WHERE po_num = $1
                """
                po_only_rows = await conn.fetch(po_only_query, po_id)
                return [dict(row) for row in po_only_rows]
            return []
            
        # 2. Fetch all open POs for these vendor_codes
        po_query = """
            SELECT po_num, po_date, delivery_date, vendor_name, vendor_code,
                   article_description, po_quantity, unit, status, vendor_phone
            FROM selected_open_po_line_items
            WHERE vendor_code = ANY($1)
              AND status != 'Closed'
        """
        rows = await conn.fetch(po_query, vendor_codes)
        po_list = [dict(row) for row in rows]
        
        if not po_list:
            return []
            
        # 3. Fetch DETAIL line items for these POs from open_po_detail
        po_nums = [p['po_num'] for p in po_list]
        detail_query = """
            SELECT po_num, article_description, po_quantity, unit_description
            FROM open_po_detail
            WHERE po_num = ANY($1)
        """
        detail_rows = await conn.fetch(detail_query, po_nums)
        
        # 4. Group details by PO Number
        details_map = {}
        for d in detail_rows:
            pnum = d['po_num']
            if pnum not in details_map:
                details_map[pnum] = []
            details_map[pnum].append({
                "description": d['article_description'],
                "quantity": d['po_quantity'],
                "unit": d['unit_description']
            })
            
        # 5. Attach details to main PO list
        for po in po_list:
            po['line_items'] = details_map.get(po['po_num'], [])
            
    return po_list


def format_po_block(po_list: List[Dict[str, Any]]) -> str:
    """
    Format one or many POs into a structured string for the AI agent context.
    """
    if not po_list:
        return "PO Data from Database:\nNo active PO data found for this vendor."

    block = "PO Data from Database:\n"
    for i, po in enumerate(po_list):
        if i > 0:
            block += "\n--- NEXT PO ---\n"
            
        block += (
            f"PO Number: {po.get('po_num', '')}\n"
            f"PO Date: {po.get('po_date', '')}\n"
            f"Delivery Date: {po.get('delivery_date', '')}\n"
            f"Vendor: {po.get('vendor_name', '')} ({po.get('vendor_code', '')})\n"
            f"Status: {po.get('status', '')}\n"
            f"Line Items:\n"
        )
        
        items = po.get("line_items", [])
        if items:
            for item in items:
                block += f"  - {item['description']} (Qty: {item['quantity']} {item['unit']})\n"
        else:
            block += f"  - {po.get('article_description', 'N/A')} (Qty: {po.get('po_quantity', 'N/A')} {po.get('unit', '')})\n"
            
    return block.strip()


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
            "can_bot_send": state != "human_controlled"
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
        # Update both thread_state (for bot logic) and communication_state (for UI display)
        payload = {
            "thread_state": state,
            "communication_state": state
        }
        
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

async def update_po_operational_fields(po_num: str, fields: dict) -> bool:
    """
    Update the operational fields in Supabase selected_open_po_line_items.
    Fields can include: risk_level, priority, communication_state, case_type, sla_due_at, etc.
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/selected_open_po_line_items"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Prefer": "return=minimal"
        }
        params = {"po_num": f"eq.{po_num}"}
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(url, headers=headers, params=params, json=fields)
            if resp.status_code >= 400:
                print(f"❌ [DB] Update failed ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
            return True
    except Exception as exc:
        print(f"❌ [DB] Failed to update operational fields: {exc}")
        return False
