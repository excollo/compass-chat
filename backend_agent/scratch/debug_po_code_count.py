import os
import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

po_num = "4200196015"
url = f"{os.getenv('SUPABASE_URL')}/rest/v1/selected_open_po_line_items?po_num=eq.{po_num}&select=vendor_code"
headers = {
    "apikey": os.getenv("SUPABASE_SERVICE_KEY"),
    "Authorization": f"Bearer {os.getenv('SUPABASE_SERVICE_KEY')}"
}

try:
    r = httpx.get(url, headers=headers)
    r.raise_for_status()
    data = r.json()
    if data:
        code = data[0]['vendor_code']
        print(f"Vendor Code for {po_num}: {code}")
        
        # Now check ALL POs for this code using the same logic the agent uses
        all_po_url = f"{os.getenv('SUPABASE_URL')}/rest/v1/selected_open_po_line_items?vendor_code=eq.{code}&status=neq.Closed&select=po_num"
        r_all = httpx.get(all_po_url, headers=headers)
        r_all.raise_for_status()
        pos = r_all.json()
        print(f"All Active POs for code {code}:")
        for p in pos:
            print(f" - {p['po_num']}")
            
    else:
        print(f"PO {po_num} not found.")
except Exception as e:
    print("Error:", e)
