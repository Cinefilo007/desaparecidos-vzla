"""
config.py — Configuración centralizada con Pydantic Settings.
Carga las variables de entorno y las valida al arrancar.
"""
from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    # ── Telegram ────────────────────────────────────────────────────
    telegram_bot_token: str
    admin_chat_id: int = 0

    # ── Google Gemini ───────────────────────────────────────────────
    gemini_api_key: str

    # ── Presupuesto Gemini (tier gratuito) ──────────────────────────
    gemini_daily_token_limit:   int = 800_000
    gemini_daily_request_limit: int = 1_200
    gemini_rpm_limit:           int = 12

    # ── Base de datos ───────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./vzla_bot.db"

    # ── Redis ───────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"

    # ── Worker ──────────────────────────────────────────────────────
    worker_concurrency:      int   = 3
    max_video_duration_min:  int   = 10
    max_video_size_mb:       int   = 200

    # ── Mini App ────────────────────────────────────────────────────
    miniapp_url:  str = "http://localhost:8000"
    api_port:     int = 8000

    # ── Entorno ─────────────────────────────────────────────────────
    environment: str = "development"
    log_level:   str = "INFO"

    @field_validator("environment")
    @classmethod
    def validate_env(cls, v):
        if v not in ("development", "production"):
            raise ValueError("ENVIRONMENT debe ser 'development' o 'production'")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def gemini_model(self) -> str:
        return "gemini-2.0-flash"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Singleton — carga la configuración una sola vez."""
    return Settings()


settings = get_settings()
