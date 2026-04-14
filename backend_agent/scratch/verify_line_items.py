import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
from database import fetch_all_vendor_pos, format_po_block

async def test_line_items_fetch():
    # Ruwa Chem Engineers vendor phone from previous logs
    vendor_phone = "8302220633" 
    
    print(f"🔍 Fetching POs for {vendor_phone}...")
    pos = await fetch_all_vendor_pos(vendor_phone)
    
    if not pos:
        print("❌ No POs found.")
        return
        
    print(f"✅ Found {len(pos)} POs.")
    for po in pos:
        num = po['po_num']
        items = po.get('line_items', [])
        print(f"  - PO {num}: {len(items)} line items found.")
        for item in items:
            print(f"    * {item['description']} ({item['quantity']} {item['unit']})")

    print("\n--- AI Context Block Preview ---")
    print(format_po_block(pos))

if __name__ == "__main__":
    asyncio.run(test_line_items_fetch())
