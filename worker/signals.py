"""
Celery signal handlers for worker lifecycle metrics.

These signals fire automatically during task execution and are used
to update Prometheus gauges and counters in real time.
"""

import logging

from celery.signals import (
    setup_logging,
    task_postrun,
    worker_process_init,
    worker_process_shutdown,
)

from app.logging_config import configure_logging
from app.metrics import ACTIVE_WORKERS

logger = logging.getLogger(__name__)


@setup_logging.connect
def on_setup_logging(**kwargs: object) -> None:
    """Own logging configuration for the worker.

    Connecting to setup_logging tells Celery not to install its own root
    logger, so worker output uses the same structured JSON format as the API.
    """
    configure_logging()


@worker_process_init.connect
def on_worker_init(**kwargs: object) -> None:
    """Increment active worker gauge when a worker process starts."""
    configure_logging()
    ACTIVE_WORKERS.inc()
    logger.info("Worker process initialized")


@worker_process_shutdown.connect
def on_worker_process_shutdown(**kwargs: object) -> None:
    """Clean up this worker process's metric files on exit.

    Without this, dead worker processes keep contributing to the livesum gauges
    (active_workers), so the count accumulates across restarts instead of
    reflecting the live workers.
    """
    import os

    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        # Remove this process's metric files outright. Do NOT also call dec(),
        # which would recreate the file at -1 and leave a stale negative sample.
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(os.getpid())
    else:
        # Single-process mode: just decrement the in-process gauge.
        ACTIVE_WORKERS.dec()
    logger.info("Worker process shutting down")


@task_postrun.connect
def on_task_postrun(sender: object = None, **kwargs: object) -> None:
    """Log task completion for observability."""
    task_id = kwargs.get("task_id", "unknown")
    state = kwargs.get("state", "unknown")
    logger.debug("Task %s finished with state: %s", task_id, state)
