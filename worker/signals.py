"""
Celery signal handlers for worker lifecycle metrics.

These signals fire automatically during task execution and are used
to update Prometheus gauges and counters in real time.
"""

import logging

from celery import Task
from celery.signals import (
    task_postrun,
    task_prerun,
    task_retry,
    worker_process_init,
    worker_shutdown,
)

from app.metrics import ACTIVE_WORKERS, QUEUE_DEPTH

logger = logging.getLogger(__name__)


@worker_process_init.connect
def on_worker_init(**kwargs: object) -> None:
    """Increment active worker gauge when a worker process starts."""
    ACTIVE_WORKERS.inc()
    logger.info("Worker process initialized")


@worker_shutdown.connect
def on_worker_shutdown(**kwargs: object) -> None:
    """Decrement active worker gauge when a worker process shuts down."""
    ACTIVE_WORKERS.dec()
    logger.info("Worker process shutting down")


@task_prerun.connect
def on_task_prerun(sender: object = None, **kwargs: object) -> None:
    """Decrement queue depth when a task starts executing (it's been dequeued)."""
    task = kwargs.get("task")
    if isinstance(task, Task):
        queue = getattr(task.request, "delivery_info", {}).get("routing_key", "default")
        QUEUE_DEPTH.labels(queue_name=queue).dec()


@task_retry.connect
def on_task_retry(sender: object = None, **kwargs: object) -> None:
    """Increment queue depth when a task is retried (it's put back in the queue)."""
    task = kwargs.get("task")
    if isinstance(task, Task):
        queue = getattr(task.request, "delivery_info", {}).get("routing_key", "default")
        QUEUE_DEPTH.labels(queue_name=queue).inc()


@task_postrun.connect
def on_task_postrun(sender: object = None, **kwargs: object) -> None:
    """Log task completion for observability."""
    task_id = kwargs.get("task_id", "unknown")
    state = kwargs.get("state", "unknown")
    logger.debug("Task %s finished with state: %s", task_id, state)
