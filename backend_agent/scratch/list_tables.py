import os
import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

url = f"{os.getenv('SUPABASE_URL')}/rest/v1/"
headers = {
    "apikey": os.getenv("SUPABASE_SERVICE_KEY"),
    "Authorization": f"Bearer {os.getenv('SUPABASE_SERVICE_KEY')}"
}

try:
    r = httpx.get(url, headers=headers)
    r.raise_for_status()
    # The response is OpenAPI spec in JSON
    spec = r.json()
    print("Tables found in Supabase:")
    for path in spec.get("paths", {}).keys():
        if path.startswith("/") and path != "/":
            print(f" - {path.strip('/')}")
except Exception as e:
    print("Error:", e)
