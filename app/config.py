"""Application configuration via environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # -- Application --
    app_name: str = "TaskForge"
    app_version: str = "1.0.0"
    debug: bool = False

    # -- Database (async for API) --
    database_url: str = (
        "postgresql+asyncpg://taskforge:taskforge_secret@taskforge-db:5432/taskforge"
    )

    # -- Database (sync for Celery workers — do NOT use asyncpg here) --
    database_url_sync: str = (
        "postgresql+psycopg2://taskforge:taskforge_secret@taskforge-db:5432/taskforge"
    )

    # -- Redis / Celery --
    celery_broker_url: str = "redis://taskforge-redis:6379/0"
    celery_result_backend: str = "redis://taskforge-redis:6379/1"
    redis_host: str = "taskforge-redis"
    redis_port: int = 6379

    # -- Job processing --
    max_retries: int = 5
    retry_base_delay: int = 2
    dlq_queue_name: str = "taskforge-dlq"

    # -- Server --
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
