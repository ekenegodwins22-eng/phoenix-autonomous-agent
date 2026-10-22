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
