import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class GoogleAuthSettings:
    def __init__(self):
        self.GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
        self.GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
        self.GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8501")

class Settings:
    def __init__(self):
        self.google_auth = GoogleAuthSettings()
        db_url = os.getenv("LAYER_BASE_CONNECTION_STRING", "").strip().strip('\\').strip('/')
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg accepts ssl=require, whereas psycopg2 uses sslmode=require
        if "sslmode=" in db_url:
            db_url = db_url.replace("sslmode=", "ssl=")
        self.DATABASE_URL: str = db_url


def get_settings() -> Settings:
    return Settings()
 