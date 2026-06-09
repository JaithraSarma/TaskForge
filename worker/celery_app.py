"""
Celery application configuration.

IMPORTANT: Workers use psycopg2 (sync driver). The API uses asyncpg (async driver).
Do NOT mix — Celery's prefork pool is fundamentally synchronous.
"""

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "taskforge",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    # -- Serialization --
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # -- Reliability --
    # acks_late=True ensures that if a worker crashes after dequeuing a task
    # but before completing it, the message is NOT acknowledged and Redis will
    # re-deliver it to another worker. Without this, a crash silently drops
    # the job.
    task_acks_late=True,
    # If a worker is lost (e.g., OOM-killed), reject the task so the broker
    # can re-deliver it rather than acknowledging it as successful.
    task_reject_on_worker_lost=True,
    # -- Timeouts --
    task_time_limit=300,  # hard kill after 5 minutes
    task_soft_time_limit=240,  # raise SoftTimeLimitExceeded after 4 minutes
    # -- Concurrency --
    worker_prefetch_multiplier=1,  # fetch one task at a time for fair scheduling
    worker_concurrency=4,
    # -- Queue routing --
    task_default_queue="default",
    task_queues={
        "high": {"exchange": "high", "routing_key": "high"},
        "default": {"exchange": "default", "routing_key": "default"},
        "low": {"exchange": "low", "routing_key": "low"},
    },
    task_routes={
        "worker.tasks.process_job": {"queue": "default"},
    },
    # -- Result expiry --
    result_expires=3600,
    # -- Timezone --
    timezone="UTC",
    enable_utc=True,
)

# Auto-discover tasks from the worker package
celery_app.autodiscover_tasks(["worker"])
