from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    app_name: str = "Phoenix Autonomous Agent"
    app_url: str = "http://127.0.0.1:8000"
    primary_base_url: str = "https://concerned-swordfish-suhailtechlnfo-01fd2de0.koyeb.app"
    primary_api_key: str = ""
    primary_model: str = ""
    primary_timeout_seconds: float = 60
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_models: str = "gemini-2.5-flash,gemini-2.5-pro"
    gemini_timeout_seconds: float = 45
    auth_secret: str = "change-me-before-deploying"
    auth_token_hours: int = 168
    database_path: str = "./data/phoenix.db"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    max_task_history: int = 100
    task_timeout_seconds: int = 300
    max_agent_steps: int = 20
    browser_headless: bool = True
    browser_navigation_timeout_ms: int = 30000
    browser_action_timeout_ms: int = 10000
    browser_close_timeout_seconds: float = 8
    mailbox_api_base_url: str = "https://gmail-alias-api.senseiphoenix.name.ng"
    mailbox_api_token: str = ""
    mailbox_api_cookie: str = ""
    mailbox_timeout_seconds: float = 20

settings = Settings()
