import os
import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

table_name = "open_po_detail"
url = f"{os.getenv('SUPABASE_URL')}/rest/v1/{table_name}?select=*&limit=1"
headers = {
    "apikey": os.getenv("SUPABASE_SERVICE_KEY"),
    "Authorization": f"Bearer {os.getenv('SUPABASE_SERVICE_KEY')}"
}

try:
    r = httpx.get(url, headers=headers)
    r.raise_for_status()
    data = r.json()
    if data:
        print(f"Columns for {table_name}:", data[0].keys())
    else:
        print(f"Table {table_name} is empty.")
except Exception as e:
    print("Error:", e)
