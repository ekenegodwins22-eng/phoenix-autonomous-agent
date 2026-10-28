from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field

def now_utc():
    return datetime.now(timezone.utc)

class TaskRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=8000)
    start_url: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=10, le=3600)
    provider: Literal["primary", "gemini", "auto"] = "auto"
    model: str | None = Field(default=None, min_length=1, max_length=160)
    proxy_mode: Literal["direct", "configured"] = "direct"
    effort: Literal["low", "medium", "high"] | None = None
    fast_mode: bool = False
    deep_research: bool = False
    browser_target: Literal["remote", "local"] | None = None

class BrowserSettings(BaseModel):
    headless: bool | None = None
    viewport_width: int = Field(default=1280, ge=320, le=3840)
    viewport_height: int = Field(default=800, ge=240, le=2160)
    user_agent: str = Field(default="", max_length=400)
    slow_mo_ms: int = Field(default=0, ge=0, le=2000)

class AgentSettings(BaseModel):
    system_prompt: str = Field(default="", max_length=4000)
    max_steps: int | None = Field(default=None, ge=1, le=200)

class OwnerSettings(BaseModel):
    agent: AgentSettings = Field(default_factory=AgentSettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)

class TaskDraft(BaseModel):
    instruction: str = Field(min_length=3, max_length=8000)
    start_url: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=10, le=3600)
    provider: Literal["primary", "gemini", "auto"] = "auto"
    model: str | None = Field(default=None, max_length=160)
    proxy_mode: Literal["direct", "configured"] = "direct"
    effort: Literal["low", "medium", "high"] | None = None
    fast_mode: bool = False
    deep_research: bool = False

class ConfigDocument(BaseModel):
    format: Literal["phoenix-config"]
    version: Literal[1]
    settings: OwnerSettings = Field(default_factory=OwnerSettings)
    task: TaskDraft | None = None

class TaskStatus(BaseModel):
    id: str
    slug: str = ""
    instruction: str
    status: Literal["queued", "running", "waiting_human", "recovering", "completed", "failed", "cancelled"]
    progress: str | None = None
    human_help: str | None = None
    waiting_details: dict | None = None
    created_at: datetime
    updated_at: datetime
    result: str | None = None
    error: str | None = None
    checkpoint: str | None = None
    provider: str | None = None
    model: str | None = None
    proxy_mode: Literal["direct", "configured"] = "direct"
    proxy_identifier: str | None = None
    effort: str | None = None
    fast_mode: bool = False
    deep_research: bool = False
    attachment_count: int = 0
    recovery_count: int = 0
    cancel_requested: bool = False
    session_url: str | None = None
    logs: list[dict] = Field(default_factory=list)

class TaskMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)

class TaskMessage(BaseModel):
    id: int
    task_id: str
    created_at: datetime
    role: Literal['user', 'agent', 'system']
    content: str


class AgentSettingsIn(BaseModel):
    system_prompt: str = Field(default='', max_length=4000)
    max_steps: int | None = Field(default=None, ge=1, le=200)


class BrowserSettingsIn(BaseModel):
    headless: bool | None = None
    viewport_width: int = Field(default=1280, ge=320, le=3840)
    viewport_height: int = Field(default=800, ge=240, le=2160)
    user_agent: str = Field(default='', max_length=400)
    slow_mo_ms: int = Field(default=0, ge=0, le=2000)


class OwnerSettings(BaseModel):
    agent: AgentSettingsIn = Field(default_factory=AgentSettingsIn)
    browser: BrowserSettingsIn = Field(default_factory=BrowserSettingsIn)


class ConfigTaskDraft(BaseModel):
    instruction: str = Field(default='', max_length=8000)
    provider: str | None = None
    model: str | None = None
    proxy_mode: str | None = None
    effort: Literal['low', 'medium', 'high'] | None = None
    fast_mode: bool = False
    deep_research: bool = False
    start_url: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=10, le=3600)


class ConfigDocument(BaseModel):
    format: Literal['phoenix-config'] = 'phoenix-config'
    version: int = 1
    settings: OwnerSettings = Field(default_factory=OwnerSettings)
    task: ConfigTaskDraft | None = None
