"""Pydantic schemas for request validation and response serialization."""

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class JobStatusEnum(str, Enum):
    """Mirror of the SQLAlchemy JobStatus for Pydantic."""

    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    dead = "dead"


class PriorityLevel(str, Enum):
    """Maps human-readable priority to queue routing."""

    high = "high"
    default = "default"
    low = "low"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class JobCreate(BaseModel):
    """Schema for creating a new job."""

    job_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Type of job to execute",
        examples=["email_notification", "data_export", "image_resize", "webhook_delivery"],
    )
    payload: dict = Field(
        default_factory=dict,
        description="Arbitrary JSON payload for the job handler",
    )
    priority: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Priority 1-10 (1-3=high, 4-7=default, 8-10=low)",
    )
    max_retries: int = Field(
        default=5,
        ge=0,
        le=20,
        description="Maximum retry attempts before moving to DLQ",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class JobResponse(BaseModel):
    """Full job detail response."""

    id: uuid.UUID
    job_type: str
    payload: dict
    priority: int
    status: JobStatusEnum
    result: dict | None = None
    error: str | None = None
    retry_count: int
    max_retries: int
    celery_task_id: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    """Paginated list of jobs."""

    jobs: list[JobResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class JobSubmittedResponse(BaseModel):
    """Response returned immediately after job submission."""

    id: uuid.UUID
    status: JobStatusEnum
    message: str = "Job submitted successfully"


# ---------------------------------------------------------------------------
# DLQ schemas
# ---------------------------------------------------------------------------


class DLQEntry(BaseModel):
    """Dead-letter queue entry for inspection."""

    id: uuid.UUID
    job_type: str
    payload: dict
    error: str | None
    retry_count: int
    max_retries: int
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class DLQListResponse(BaseModel):
    """Paginated DLQ entries."""

    entries: list[DLQEntry]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "healthy"
    version: str
    service: str
