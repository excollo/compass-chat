import os
import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

po_num = "4100260863"
url = f"{os.getenv('SUPABASE_URL')}/rest/v1/selected_open_po_line_items"
headers = {
    "apikey": os.getenv("SUPABASE_SERVICE_KEY"),
    "Authorization": f"Bearer {os.getenv('SUPABASE_SERVICE_KEY')}"
}
params = {
    "po_num": f"eq.{po_num}"
}

try:
    r = httpx.get(url, headers=headers, params=params)
    r.raise_for_status()
    data = r.json()
    if data:
        po = data[0]
        print(f"PO {po_num} FOUND:")
        print(f"  Phone: {po['vendor_phone']}")
        print(f"  Status: {po['status']}")
        print(f"  Vendor: {po['vendor_name']}")
    else:
        print(f"PO {po_num} NOT FOUND in selected_open_po_line_items")
except Exception as e:
    print("Error:", e)
