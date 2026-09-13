from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]
HealthCheckName = Literal["server", "license", "staad"]


class ApiError(BaseModel):
    error_code: str
    message: str
    retryable: bool = False
    attempts: int = 0
    stage: Optional[str] = None
    hint: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    last_stderr_excerpt: Optional[str] = None


class JobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str = "Job accepted."


class JobAbortResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress_percent: int = 0
    message: str = "Job is not started yet."
    attempts: int = 0
    max_attempts: int = 0
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result_ready: bool = False
    error: Optional[ApiError] = None
    duration_seconds: Optional[float] = None
    working_directory: Optional[str] = None
    cleanup_performed: bool = False


class JobResultResponse(BaseModel):
    """Structured extraction outcome (success or failure) for polling clients."""

    job_id: str
    status: JobStatus
    result: Optional[dict[str, Any]] = None
    error: Optional[ApiError] = None
    attempts: int = 0
    max_attempts: int = 0
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    working_directory: Optional[str] = None
    cleanup_performed: bool = False


class HealthResponse(BaseModel):
    check: HealthCheckName = Field(default="server")
    status: str = Field(default="ok")
    healthy: bool = Field(default=True)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = Field(default="FastAPI server is ready.")
    details: Optional[dict[str, Any]] = None
