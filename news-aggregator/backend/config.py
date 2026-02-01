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

    # Instance identification
    instance_type: str = "production"  # production | training | development

    # Database - Connection
    # Can use either DATABASE_URL directly or build from components
    database_url: str = ""  # Full connection string (takes precedence if set)
    database_host: str = ""  # Hostname for building connection string
    database_port: int = 5432  # Port for PostgreSQL
    database_name: str = "liga_news"  # Database name
    database_user: str = ""  # Username
    database_password: str = ""  # Password
    database_driver: str = "postgresql+asyncpg"  # SQLAlchemy async driver

    # Database - Connection Pool (PostgreSQL only)
    # Pool sizing for concurrent workers:
    # - Scheduler: up to 10 concurrent channel fetches
    # - LLM Worker: batch_size=10
    # - Classifier Worker: batch_size=50
    # - API requests: ~20
    # Total potential: ~90, so pool_size + max_overflow should be >= 50
    database_pool_size: int = 20  # Number of persistent connections
    database_pool_max_overflow: int = 30  # Extra connections allowed
    database_pool_timeout: int = 30  # Seconds to wait for connection
    database_pool_recycle: int = 1800  # Recycle connections after 30 min

    def get_database_url(self) -> str:
        """Get database URL, building from components if not set directly.

        Priority:
        1. DATABASE_URL if set
        2. Build from components (host, port, name, user, password)

        Raises ValueError if no database configuration is provided.
        """
        if self.database_url:
            return self.database_url

        if self.database_host and self.database_user:
            # Build PostgreSQL URL from components
            password_part = f":{self.database_password}" if self.database_password else ""
            return (
                f"{self.database_driver}://{self.database_user}{password_part}"
                f"@{self.database_host}:{self.database_port}/{self.database_name}"
            )

        raise ValueError(
            "Database not configured. Set DATABASE_URL or DATABASE_HOST + DATABASE_USER"
        )

    def get_database_info(self) -> dict:
        """Get database connection info for health checks (no credentials)."""
        url = self.get_database_url()

        # Parse PostgreSQL URL
        if "@" in url:
            # Remove credentials: driver://user:pass@host:port/db -> host:port/db
            after_at = url.split("@")[-1]
            host_port_db = after_at.split("/")
            host_port = host_port_db[0] if host_port_db else "unknown"
            db_name = host_port_db[1] if len(host_port_db) > 1 else "unknown"
            return {
                "type": "postgresql",
                "host": host_port,
                "database": db_name,
                "pool_size": self.database_pool_size,
                "max_overflow": self.database_pool_max_overflow,
            }

        return {"type": "unknown"}

    # LLM - General
    llm_enabled: bool = True  # Set to False to disable all LLM processing

    # LLM - Ollama (primary)
    ollama_base_url: str = "http://gpu1:11434"
    ollama_model: str = "qwen3:14b-q8_0"  # Base model with system prompt (NOT liga-relevance)
    ollama_timeout: int = 120

    # LLM - OpenRouter (fallback)
    openrouter_api_key: str = ""
    openrouter_model: str = "mistralai/mistral-7b-instruct:free"
    openrouter_timeout: int = 60

    # Relevance Pre-filter (embedding classifier)
    classifier_url: str = "http://gpu1:8082"
    classifier_threshold: float = 0.8  # Skip LLM if irrelevant confidence > this
    classifier_enabled: bool = True
    classifier_use_priority: bool = False  # Use classifier priority instead of LLM
    classifier_use_ak: bool = False  # Use classifier AK instead of LLM

    # GPU1 Power Management (Wake-on-LAN)
    gpu1_wol_enabled: bool = True  # Enable WoL feature
    gpu1_ollama_url: str = "http://192.168.0.141:11434"  # Ollama URL on gpu1 for availability check
    gpu1_mac_address: str = "58:47:ca:7c:18:cc"  # gpu1 MAC address
    gpu1_broadcast: str = "255.255.255.255"  # Global broadcast (some NICs require this for WoL)
    gpu1_ssh_host: str = "192.168.0.141"  # gpu1 IP for SSH
    gpu1_ssh_user: str = "ligahessen"  # SSH user for shutdown (dedicated user)
    gpu1_ssh_key_path: str = "/app/ssh/id_ed25519"  # SSH key path in container
    gpu1_auto_shutdown: bool = True  # Shutdown after idle if we woke it
    gpu1_idle_timeout: int = 300  # Seconds idle before auto-shutdown (5 min)
    gpu1_wake_timeout: int = 120  # Max seconds to wait for Ollama after WoL
    gpu1_active_hours_start: int = 7  # Hour (0-23) when gpu1 usage allowed (default 7 AM)
    gpu1_active_hours_end: int = 16  # Hour (0-23) when gpu1 usage stops (default 4 PM)
    gpu1_active_weekdays_only: bool = True  # Only wake on weekdays (Mon-Fri)

    # Scheduler
    scheduler_enabled: bool = True  # Set to False to disable scheduler on startup
    fetch_interval_minutes: int = 30
    cleanup_days: int = 30

    # Workers
    llm_worker_enabled: bool = True  # Set to False to disable LLM worker on startup
    classifier_worker_enabled: bool = True  # Set to False to disable classifier on startup
    worker_status_poll_interval: int = 10  # Seconds between DB status sync/command polls

    # Proxy Pool
    proxy_pool_min: int = 20  # Minimum working proxies to maintain
    proxy_pool_max: int = 25  # Maximum working proxies (buffer)
    proxy_known_max: int = 100  # Maximum known good proxies to store
    proxy_https_pool_min: int = 5  # Minimum HTTPS-capable proxies for X scraper

    # API
    api_prefix: str = "/api"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


settings = Settings()
