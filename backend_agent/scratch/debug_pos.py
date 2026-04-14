import os
import httpx
from dotenv import load_dotenv

# Load from .env
load_dotenv(dotenv_path=".env")

vendor_phone = "8302220633"
url = f"{os.getenv('SUPABASE_URL')}/rest/v1/selected_open_po_line_items"
headers = {
    "apikey": os.getenv("SUPABASE_SERVICE_KEY"),
    "Authorization": f"Bearer {os.getenv('SUPABASE_SERVICE_KEY')}"
}
params = {
    "vendor_phone": f"eq.{vendor_phone}"
}

try:
    r = httpx.get(url, headers=headers, params=params)
    r.raise_for_status()
    data = r.json()
    print(f"Total POs found in Supabase for phone {vendor_phone}: {len(data)}")
    for po in data:
        print(f"- PO Num: {po['po_num']}, Status: {po['status']}, DB Phone: {po['vendor_phone']}")
except Exception as e:
    print("Error fetching POs:", e)
    if hasattr(e, 'response'):
        print(e.response.text)
