import os
import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

po_nums = ["4100260584", "4100260863"]
url = f"{os.getenv('SUPABASE_URL')}/rest/v1/selected_open_po_line_items"
headers = {
    "apikey": os.getenv("SUPABASE_SERVICE_KEY"),
    "Authorization": f"Bearer {os.getenv('SUPABASE_SERVICE_KEY')}"
}

for po_num in po_nums:
    try:
        params = {"po_num": f"eq.{po_num}"}
        r = httpx.get(url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()
        if data:
            po = data[0]
            print(f"PO: {po_num} | Vendor: {po['vendor_name']} | Code: {po['vendor_code']} | Phone: {po['vendor_phone']}")
        else:
            print(f"PO {po_num} not found")
    except Exception as e:
        print(f"Error for {po_num}: {e}")
