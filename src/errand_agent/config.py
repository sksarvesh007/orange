"""Centralized application configuration using pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Production settings supporting environment variable overrides."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Environment
    app_env: Literal["development", "testing", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    workers: int = 4

    # Persistence
    database_url: str = "sqlite+aiosqlite:///errand_agent.db"
    redis_url: str = "redis://localhost:6379/0"

    # Tracing & Observability
    trace_dir: Path = Field(default_factory=lambda: Path("artifacts/traces"))
    log_level: str = "INFO"
    log_json: bool = False
    enable_metrics: bool = True

    # Langfuse (Optional exporter, Section 7A)
    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "http://localhost:3000"

    # Optional Live LLM Model Configuration (Section 10)
    llm_provider: Literal["mock", "openai", "anthropic", "gemini"] = "mock"
    llm_model_name: str = "gpt-4o-mini"
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 5.0


def get_settings() -> Settings:
    """Retrieve cached application settings instance."""
    return Settings()
