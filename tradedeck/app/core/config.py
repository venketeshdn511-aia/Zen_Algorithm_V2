import os
from pathlib import Path
from typing import Optional
from datetime import timezone

from pydantic_settings import BaseSettings, SettingsConfigDict
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

# Base Directory logic
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    # ── Environment ───────────────────────────────────────────────────────────
    IS_RENDER: bool = os.getenv("RENDER", "false").lower() == "true"
    ENV: str = "production" if os.getenv("RENDER", "false").lower() == "true" else os.getenv("ENV", "local")

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: Optional[str] = None
    
    # Fallback/Dev Postgres connection parameters
    DB_USER: str = "trader"
    DB_PASSWORD: Optional[str] = None
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "tradedeck"

    # ── API Keys & Secrets ───────────────────────────────────────────────────
    FYERS_APP_ID: Optional[str] = None
    FYERS_SECRET_ID: Optional[str] = None
    FYERS_REDIRECT_URI: str = "http://127.0.0.1:8080"
    FYERS_ACCESS_TOKEN: Optional[str] = None
    
    MONGO_URI: Optional[str] = None
    
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # ── Computed Properties ──────────────────────────────────────────────────
    @property
    def ASYNC_DATABASE_URL(self) -> str:
        """Derive the async SQLAlchemy URL."""
        # 1. Use DATABASE_URL if explicitly provided
        if self.DATABASE_URL:
            url = self.DATABASE_URL
        # 2. Try to construct Postgres URL if DB_PASSWORD is provided
        elif self.DB_PASSWORD:
            url = f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        # 3. Final fallback to SQLite
        else:
            if self.IS_RENDER:
                db_dir = "/tmp"
                os.makedirs(db_dir, exist_ok=True)
                url = f"sqlite+aiosqlite:///{db_dir}/tradedeck_local.db"
            else:
                url = "sqlite+aiosqlite:///tradedeck_local.db"
        
        # Cloud compatibility: Handle 'postgres://' (common in Render/Heroku)
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            
        return url

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True
    )

settings = Settings()
