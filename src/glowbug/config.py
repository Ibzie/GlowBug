from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from the environment and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "GlowBug"
    app_env: str = "development"
    debug: bool = False
    secret_key: str = "oogabooga"
    database_url: str = "postgresql+asyncpg://glowbug:glowbug@localhost:55432/glowbug"
    redis_url: str = "redis://localhost:6379/0"
    timezone: str = "UTC"


@lru_cache
def get_settings() -> Settings:
    return Settings()
