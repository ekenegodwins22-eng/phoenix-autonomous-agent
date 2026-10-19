from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    app_name: str = "Phoenix Autonomous Agent"
    app_url: str = "http://127.0.0.1:8000"
    primary_base_url: str = "https://concerned-swordfish-suhailtechlnfo-01fd2de0.koyeb.app"
    primary_api_key: str = ""
    primary_model: str = "deepseek-v4-flash"
    primary_timeout_seconds: float = 60
    primary_chat_retry_attempts: int = 3
    primary_chat_retry_base_seconds: float = 0.5
    primary_chat_retry_max_seconds: float = 8
    primary_model_cache_seconds: float = 30
    primary_model_discovery_attempts: int = 2
    primary_model_discovery_retry_base_seconds: float = 0.5
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_models: str = "gemini-2.5-flash,gemini-2.5-pro"
    gemini_timeout_seconds: float = 45
    auth_secret: str = "change-me-before-deploying"
    auth_token_hours: int = 168
    database_url: str | None = None
    database_path: str = "./data/phoenix.db"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    max_task_history: int = 100
    max_task_recovery_attempts: int = 2
    task_timeout_seconds: int = 900
    max_agent_steps: int = 60
    model_action_retries: int = 2
    browser_headless: bool = True
    browser_navigation_timeout_ms: int = 30000
    browser_action_timeout_ms: int = 10000
    browser_retry_attempts: int = 3
    browser_close_timeout_seconds: float = 8
    mailbox_api_base_url: str = "https://gmail-alias-api.senseiphoenix.name.ng"
    mailbox_api_token: str = ""
    mailbox_api_cookie: str = ""
    mailbox_timeout_seconds: float = 20
    playwright_browsers_path: str = "/ms-playwright"
    runtime_temp_dir: str = "/tmp/phoenix"
    runtime_temp_retention_seconds: int = 3600
    runtime_cleanup_max_entries: int = 100
    resource_disk_path: str = "/"
    minimum_free_disk_bytes: int = 268435456
    maximum_memory_used_percent: float = 92

settings = Settings()
