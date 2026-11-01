from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://jobpilot:jobpilot@db:5432/jobpilot"
    database_url_unpooled: str = ""
    redis_url: str = "redis://redis:6379/0"
    api_token: SecretStr = SecretStr("")
    auth_required: bool = False
    hosted_mode: bool = False
    store_resume_files_in_db: bool = False
    embedded_searches: bool = True
    automatic_search_schedule: bool = True
    search_timezone: str = "Europe/Moscow"
    data_dir: Path = Path("data")
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""
    resend_api_key: SecretStr = SecretStr("")
    notification_email: str = ""
    notification_from: str = "JobPilot <onboarding@resend.dev>"
    notification_min_score: int = 0
    public_app_url: str = ""
    llm_provider: str = "openai"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    browser_timeout_ms: int = 30000
    application_timeout_seconds: int = 600
    application_browser_headless: bool = False
    application_session_seconds: int = 900
    application_session_limit: int = 3


@lru_cache
def settings() -> Settings:
    return Settings()
