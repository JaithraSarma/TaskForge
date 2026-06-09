"""Job submission and management API endpoints."""

import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.metrics import JOBS_SUBMITTED_TOTAL, QUEUE_DEPTH
from app.models import Job, JobStatus
from app.schemas import (
    JobCreate,
    JobListResponse,
    JobResponse,
    JobStatusEnum,
    JobSubmittedResponse,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _priority_to_queue(priority: int) -> str:
    """Map numeric priority (1-10) to a named Celery queue."""
    if priority <= 3:
        return "high"
    elif priority <= 7:
        return "default"
    else:
        return "low"


@router.post(
    "",
    response_model=JobSubmittedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a new job",
)
async def submit_job(
    job_in: JobCreate,
    db: AsyncSession = Depends(get_db),
) -> JobSubmittedResponse:
    """Create a job record and enqueue it for async processing."""
    # Persist the job in PostgreSQL
    job = Job(
        job_type=job_in.job_type,
        payload=job_in.payload,
        priority=job_in.priority,
        max_retries=job_in.max_retries,
        status=JobStatus.PENDING,
    )
    db.add(job)
    await db.flush()  # get the generated ID before commit

    # Enqueue in Celery — import here to avoid circular import at module level
    from worker.celery_app import celery_app

    queue_name = _priority_to_queue(job_in.priority)
    task = celery_app.send_task(
        "worker.tasks.process_job",
        args=[str(job.id)],
        queue=queue_name,
    )

    # Store the Celery task ID for correlation
    job.celery_task_id = task.id
    await db.flush()

    # Increment Prometheus counter
    JOBS_SUBMITTED_TOTAL.labels(
        job_type=job_in.job_type,
        priority=queue_name,
    ).inc()

    # Increment queue depth
    QUEUE_DEPTH.labels(queue_name=queue_name).inc()

    return JobSubmittedResponse(id=job.id, status=JobStatusEnum(job.status.value))


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get job details",
)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Retrieve a single job by ID."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    return JobResponse.model_validate(job)


@router.get(
    "",
    response_model=JobListResponse,
    summary="List jobs with filters",
)
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(None, alias="status", description="Filter by status"),
    job_type: str | None = Query(None, description="Filter by job type"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
) -> JobListResponse:
    """List jobs with optional filtering and pagination."""
    query = select(Job)
    count_query = select(func.count()).select_from(Job)

    if status_filter:
        try:
            status_enum = JobStatus(status_filter)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status_filter}. Valid values: {[s.value for s in JobStatus]}",
            )
        query = query.where(Job.status == status_enum)
        count_query = count_query.where(Job.status == status_enum)

    if job_type:
        query = query.where(Job.job_type == job_type)
        count_query = count_query.where(Job.job_type == job_type)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination and ordering
    query = query.order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    jobs = result.scalars().all()

    return JobListResponse(
        jobs=[JobResponse.model_validate(j) for j in jobs],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a pending job",
)
async def cancel_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Cancel a job — only works if the job is still pending."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    if job.status != JobStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel job in '{job.status.value}' state. Only 'pending' jobs can be cancelled.",
        )

    # Revoke the Celery task
    if job.celery_task_id:
        from worker.celery_app import celery_app

        celery_app.control.revoke(job.celery_task_id, terminate=False)

    await db.delete(job)
