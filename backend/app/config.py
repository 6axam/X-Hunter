from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    dev_auth_enabled: bool = False
    session_secret: str = "development-only-change-me"
    session_max_age_seconds: int = 3600
    telegram_init_data_max_age_seconds: int = 600
    database_url: str = "sqlite+aiosqlite:///./olx_hunter.sqlite3"
    redis_url: str = "redis://localhost:6379/0"
    media_root: Path = Path("./media")
    media_max_bytes: int = 10_000_000
    media_max_pixels: int = 30_000_000
    media_allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["olxcdn.com"]
    )
    export_root: Path = Path("./exports")
    log_root: Path = Path("/data/logs")
    log_level: str = "INFO"
    log_max_bytes: int = 5_000_000
    log_backup_count: int = 5
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    default_timezone: str = "Europe/Kyiv"
    scan_interval_minutes: int = 15
    scan_max_pages: int = 3
    source_http_enabled: bool = False
    source_browser_enabled: bool = False
    source_browser_headless: bool = True

    telegram_bot_token: str | None = None
    owner_telegram_id: int | None = None
    public_base_url: str = "http://localhost:8080"
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    openrouter_research_model: str | None = None
    openrouter_daily_budget_usd: str = "1.00"
    research_max_rounds: int = 4
    research_max_queries_per_round: int = 5
    research_max_runtime_seconds: int = 720

    @field_validator("owner_telegram_id", mode="before")
    @classmethod
    def blank_owner_id_is_unconfigured(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("media_allowed_hosts", mode="before")
    @classmethod
    def parse_media_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value

    @field_validator("session_secret")
    @classmethod
    def require_production_secret(cls, value: str, info) -> str:
        app_env = info.data.get("app_env", "development")
        if app_env == "production" and len(value) < 32:
            raise ValueError("SESSION_SECRET must contain at least 32 characters in production")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
