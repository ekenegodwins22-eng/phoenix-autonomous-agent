from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    app_name: str = "Phoenix Autonomous Agent"
    app_url: str = "http://127.0.0.1:8000"
    # Legacy PRIMARY_* names: kept so existing deploy env keeps working. They seed the ollama
    # engine when OLLAMA_* is not set.
    primary_base_url: str = ""
    primary_api_key: str = ""
    primary_model: str = ""
    primary_timeout_seconds: float = 60
    primary_chat_retry_attempts: int = 3
    primary_chat_retry_base_seconds: float = 0.5
    primary_chat_retry_max_seconds: float = 8
    primary_model_cache_seconds: float = 30
    primary_model_discovery_attempts: int = 2
    primary_model_discovery_retry_base_seconds: float = 0.5
    # Local engines (OpenAI-compatible: /v1/models and /v1/chat/completions).
    ollama_base_url: str = ""
    ollama_api_key: str = ""
    ollama_model: str = ""
    llamacpp_base_url: str = ""
    llamacpp_api_key: str = ""
    llamacpp_model: str = ""
    smollm2_base_url: str = ""
    smollm2_api_key: str = ""
    smollm2_model: str = ""
    agentrouter_base_url: str = "https://agentrouter.org/v1"
    agentrouter_api_key: str = ""
    agentrouter_model: str = "gpt-4o-mini"
    fastapi_relay_base_url: str = "https://fastapi.senseiphoenix.name.ng"
    fastapi_relay_api_key: str = ""
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
    execution_engine: str = "browser_use"
    model_action_retries: int = 2
    browser_headless: bool = True
    browser_navigation_timeout_ms: int = 30000
    browser_action_timeout_ms: int = 10000
    browser_retry_attempts: int = 3
    browser_close_timeout_seconds: float = 8
    browser_stream_interval_seconds: float = 0.75
    browser_stream_jpeg_quality: int = 60
    browser_proxy_server: str | None = None
    browser_proxy_username: str | None = None
    browser_proxy_password: str | None = None
    browser_proxy_required: bool = False
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
    phoenix_worker_id: str = "primary-web"
    phoenix_worker_poll_seconds: float = 2.0
    phoenix_worker_idle_exit_seconds: int = 0

    @model_validator(mode="after")
    def _legacy_primary_fallbacks(self):
        # Old single-router deploys set PRIMARY_* and point it at an OpenAI-compatible engine.
        # Use them for ollama only when OLLAMA_* is absent, so the current deploy env still works.
        if not self.ollama_base_url and self.primary_base_url:
            self.ollama_base_url = self.primary_base_url
        if not self.ollama_api_key and self.primary_api_key:
            self.ollama_api_key = self.primary_api_key
        if not self.ollama_model and self.primary_model:
            self.ollama_model = self.primary_model
        return self


settings = Settings()
