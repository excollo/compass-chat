import os
import httpx
from dotenv import load_dotenv

# Load from backend_agent/.env
load_dotenv(dotenv_path=".env")

url = f"{os.getenv('SUPABASE_URL')}/rest/v1/selected_open_po_line_items?limit=1"
headers = {
    "apikey": os.getenv("SUPABASE_SERVICE_KEY"),
    "Authorization": f"Bearer {os.getenv('SUPABASE_SERVICE_KEY')}"
}

try:
    r = httpx.get(url, headers=headers)
    r.raise_for_status()
    data = r.json()
    if data:
        print("Columns found:", list(data[0].keys()))
    else:
        print("No data found to determine columns.")
except Exception as e:
    print("Error fetching schema:", e)
    if hasattr(e, 'response'):
        print(e.response.text)
