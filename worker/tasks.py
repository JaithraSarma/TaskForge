"""
Celery task definitions with retry logic and DLQ routing.

IMPORTANT: This module uses psycopg2 (sync driver) for database access.
Do NOT import or use asyncpg/async sessions here — Celery workers are sync.
"""

import json
import logging
import random
import traceback
from datetime import datetime, timezone

import redis
from celery import Task
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.metrics import (
    DLQ_SIZE,
    JOB_DURATION_SECONDS,
    JOBS_COMPLETED_TOTAL,
    JOBS_RETRY_TOTAL,
)
from app.models import JobStatus
from worker.celery_app import celery_app
from worker.handlers import get_handler

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Sync database engine for Celery workers (psycopg2, NOT asyncpg)
# ---------------------------------------------------------------------------
sync_engine = create_engine(
    settings.database_url_sync,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=300,
)
SyncSessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False)

# Redis client for DLQ operations
redis_client = redis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    db=2,  # separate DB for DLQ
    decode_responses=True,
)


def _get_sync_session() -> Session:
    """Create a synchronous database session for worker use."""
    return SyncSessionLocal()


def _push_to_dlq(job_id: str, job_type: str, payload: dict, error: str) -> None:
    """Push a permanently failed job to the Redis dead-letter queue."""
    entry = {
        "job_id": str(job_id),
        "job_type": job_type,
        "payload": json.dumps(payload),
        "error": error,
        "failed_at": datetime.now(timezone.utc).isoformat(),
    }
    redis_client.lpush(settings.dlq_queue_name, json.dumps(entry))
    DLQ_SIZE.inc()
    logger.warning("Job %s moved to DLQ: %s", job_id, error[:200])


class ProcessJobTask(Task):
    """Custom Celery task base with automatic retry and DLQ routing."""

    name = "worker.tasks.process_job"
    acks_late = True

    def on_failure(
        self, exc: Exception, task_id: str, args: tuple, kwargs: dict, einfo: object
    ) -> None:
        """Called when the task fails after all retries are exhausted."""
        logger.error("Task %s permanently failed: %s", task_id, exc)


@celery_app.task(
    base=ProcessJobTask,
    bind=True,
    max_retries=None,  # we manage retries manually based on job.max_retries
)
def process_job(self: Task, job_id: str) -> dict:
    """
    Process a single job.

    Flow:
    1. Fetch job from PostgreSQL (sync)
    2. Update status to RUNNING
    3. Dispatch to the appropriate handler
    4. On success: status → SUCCEEDED
    5. On failure: retry with exponential backoff, or → DEAD (DLQ)
    """
    # Import model here to avoid issues with declarative base at import time
    import uuid

    from app.models import Job

    # Ensure job_id is parsed as uuid.UUID for compatibility with SQLite
    job_uuid = uuid.UUID(job_id) if isinstance(job_id, str) else job_id

    session = _get_sync_session()
    start_time = datetime.now(timezone.utc)

    try:
        # Fetch the job
        job = session.query(Job).filter(Job.id == job_uuid).first()
        if not job:
            logger.error("Job %s not found in database — skipping", job_id)
            return {"error": "Job not found"}

        # Update to RUNNING
        job.status = JobStatus.RUNNING
        job.started_at = start_time
        session.commit()

        # Dispatch to handler
        handler = get_handler(job.job_type)
        result = handler(job.payload)

        # SUCCESS
        end_time = datetime.now(timezone.utc)
        job.status = JobStatus.SUCCEEDED
        job.result = result
        job.completed_at = end_time
        session.commit()

        # Metrics
        duration = (end_time - start_time).total_seconds()
        JOB_DURATION_SECONDS.labels(job_type=job.job_type).observe(duration)
        JOBS_COMPLETED_TOTAL.labels(job_type=job.job_type, status="succeeded").inc()

        logger.info("Job %s completed successfully in %.2fs", job_id, duration)
        return {"status": "succeeded", "result": result}

    except (KeyError, Exception) as exc:
        # Determine if we should retry or send to DLQ
        job = session.query(Job).filter(Job.id == job_uuid).first()
        if not job:
            return {"error": "Job disappeared during processing"}

        job.retry_count += 1
        error_msg = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"

        if job.retry_count <= job.max_retries:
            # RETRY with exponential backoff + jitter
            job.status = JobStatus.PENDING
            job.error = error_msg[:2000]
            session.commit()

            # Exponential backoff: 2^retry * base_delay + random jitter
            countdown = (2**job.retry_count) * settings.retry_base_delay + random.uniform(0, 1)

            JOBS_RETRY_TOTAL.labels(job_type=job.job_type).inc()
            logger.warning(
                "Job %s failed (attempt %d/%d), retrying in %.1fs: %s",
                job_id,
                job.retry_count,
                job.max_retries,
                countdown,
                str(exc)[:200],
            )

            raise self.retry(exc=exc, countdown=countdown)

        else:
            # PERMANENT FAILURE → Dead-Letter Queue
            end_time = datetime.now(timezone.utc)
            job.status = JobStatus.DEAD
            job.error = error_msg[:2000]
            job.completed_at = end_time
            session.commit()

            # Push to Redis DLQ
            _push_to_dlq(job_id, job.job_type, job.payload, error_msg[:2000])

            # Metrics
            duration = (end_time - start_time).total_seconds()
            JOB_DURATION_SECONDS.labels(job_type=job.job_type).observe(duration)
            JOBS_COMPLETED_TOTAL.labels(job_type=job.job_type, status="dead").inc()

            logger.error(
                "Job %s permanently failed after %d retries — moved to DLQ",
                job_id,
                job.max_retries,
            )
            return {"status": "dead", "error": str(exc)}

    finally:
        session.close()
