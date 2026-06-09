import json
import uuid

import redis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.metrics import DLQ_SIZE, QUEUE_DEPTH
from app.models import Job, JobStatus
from app.schemas import DLQEntry, DLQListResponse, JobStatusEnum, JobSubmittedResponse

router = APIRouter(prefix="/api/v1/dlq", tags=["dead-letter-queue"])

settings = get_settings()
redis_client = redis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    db=2,  # separate DB for DLQ
    decode_responses=True,
)


def _remove_from_redis_dlq(job_id: uuid.UUID) -> None:
    """Remove a job entry from the Redis DLQ list if it exists."""
    try:
        job_id_str = str(job_id)
        elements = redis_client.lrange(settings.dlq_queue_name, 0, -1)
        for element in elements:
            data = json.loads(element)
            if data.get("job_id") == job_id_str:
                redis_client.lrem(settings.dlq_queue_name, 0, str(element))
                break
    except Exception as e:
        import logging

        logging.getLogger("app.api.dlq").warning(
            "Failed to remove job %s from Redis DLQ: %s", job_id, e
        )


@router.get(
    "",
    response_model=DLQListResponse,
    summary="List dead-letter queue entries",
)
async def list_dlq(
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> DLQListResponse:
    """List all jobs that have been moved to the dead-letter queue."""
    base_filter = Job.status == JobStatus.DEAD

    count_result = await db.execute(select(func.count()).select_from(Job).where(base_filter))
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Job)
        .where(base_filter)
        .order_by(Job.completed_at.desc().nullslast())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    jobs = result.scalars().all()

    return DLQListResponse(
        entries=[DLQEntry.model_validate(j) for j in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{job_id}",
    response_model=DLQEntry,
    summary="Inspect a single DLQ entry",
)
async def inspect_dlq_entry(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DLQEntry:
    """Get full details of a dead-letter queue entry including payload and error trace."""
    result = await db.execute(select(Job).where(Job.id == job_id, Job.status == JobStatus.DEAD))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DLQ entry {job_id} not found",
        )
    return DLQEntry.model_validate(job)


@router.post(
    "/{job_id}/retry",
    response_model=JobSubmittedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-enqueue a dead job for retry",
)
async def retry_dlq_entry(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JobSubmittedResponse:
    """Reset a dead job back to pending and re-enqueue it in Celery."""
    result = await db.execute(select(Job).where(Job.id == job_id, Job.status == JobStatus.DEAD))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DLQ entry {job_id} not found",
        )

    # Reset the job state
    job.status = JobStatus.PENDING
    job.retry_count = 0
    job.error = None
    job.result = None
    job.started_at = None
    job.completed_at = None

    await db.flush()

    # Re-enqueue in Celery
    from worker.celery_app import celery_app

    priority_queue = "high" if job.priority <= 3 else ("low" if job.priority > 7 else "default")
    task = celery_app.send_task(
        "worker.tasks.process_job",
        args=[str(job.id)],
        queue=priority_queue,
    )
    job.celery_task_id = task.id
    await db.flush()

    # Update metrics
    DLQ_SIZE.dec()
    QUEUE_DEPTH.labels(queue_name=priority_queue).inc()

    # Remove from Redis DLQ
    _remove_from_redis_dlq(job_id)

    return JobSubmittedResponse(
        id=job.id,
        status=JobStatusEnum(job.status.value),
        message="Dead job re-enqueued for processing",
    )


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Purge a DLQ entry",
)
async def purge_dlq_entry(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Permanently delete a dead-letter queue entry."""
    result = await db.execute(select(Job).where(Job.id == job_id, Job.status == JobStatus.DEAD))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DLQ entry {job_id} not found",
        )
    await db.delete(job)

    # Update metrics
    DLQ_SIZE.dec()

    # Remove from Redis DLQ
    _remove_from_redis_dlq(job_id)
