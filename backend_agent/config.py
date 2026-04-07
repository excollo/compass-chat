import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY: str = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE: float = float(os.getenv("OPENAI_TEMPERATURE", "0.1"))

DATABASE_URL: str = os.environ["DATABASE_URL"]

BACKEND_URL: str = os.getenv("BACKEND_URL", "https://compass-chat.onrender.com").rstrip("/")

SESSION_MEMORY_WINDOW: int = int(os.getenv("SESSION_MEMORY_WINDOW", "20"))

PORT: int = int(os.getenv("PORT", "8000"))
