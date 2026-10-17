import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / '.env')

class GoogleAuthSettings:
    def __init__(self):
        self.GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
        self.GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
        self.GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8501")

class Settings:
    def __init__(self):
        self.google_auth = GoogleAuthSettings()
        # Existing project configuration stores the OpenAI key as LAYER_BASE.
        self.OPENAI_API_KEY: str = (os.getenv("OPENAI_API_KEY", "").strip()
                                    or os.getenv("LAYER_BASE", "").strip())
        self.OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4.1")
        db_url = os.getenv("DATABASE_URL", os.getenv("LAYER_BASE_CONNECTION_STRING", "")).strip()
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg accepts ssl=require, whereas psycopg2 uses sslmode=require
        if "sslmode=" in db_url:
            db_url = db_url.replace("sslmode=", "ssl=")
        self.DATABASE_URL: str = db_url


def get_settings() -> Settings:
    return Settings()
