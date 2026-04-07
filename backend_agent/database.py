import asyncpg
from typing import Optional, Dict, Any
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

