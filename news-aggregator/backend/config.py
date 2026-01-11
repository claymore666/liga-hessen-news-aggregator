"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "News Aggregator"
    debug: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/news_aggregator.db"

    # LLM - General
    llm_enabled: bool = True  # Set to False to disable all LLM processing

    # LLM - Ollama (primary)
    ollama_base_url: str = "http://gpu1:11434"
    ollama_model: str = "liga-relevance"
    ollama_timeout: int = 120

    # LLM - OpenRouter (fallback)
    openrouter_api_key: str = ""
    openrouter_model: str = "mistralai/mistral-7b-instruct:free"
    openrouter_timeout: int = 60

    # Relevance Pre-filter (embedding classifier)
    classifier_url: str = "http://gpu1:8082"
    classifier_threshold: float = 0.8  # Skip LLM if irrelevant confidence > this
    classifier_enabled: bool = True

    # Scheduler
    fetch_interval_minutes: int = 30
    cleanup_days: int = 30

    # API
    api_prefix: str = "/api"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


settings = Settings()
