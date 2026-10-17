from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field

def now_utc():
    return datetime.now(timezone.utc)

class TaskRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=8000)
    start_url: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=10, le=1800)
    provider: Literal["primary", "gemini", "auto"] = "auto"
    model: str | None = Field(default=None, min_length=1, max_length=160)

class TaskStatus(BaseModel):
    id: str
    instruction: str
    status: Literal["queued", "running", "completed", "failed"]
    progress: str | None = None
    created_at: datetime
    updated_at: datetime
    result: str | None = None
    error: str | None = None
    provider: str | None = None
    model: str | None = None
