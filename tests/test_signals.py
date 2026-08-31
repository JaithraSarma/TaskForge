"""Tests for the Celery worker lifecycle signal handlers.

The handlers keep the Prometheus gauges in sync with worker and queue state.
They fire automatically in a running worker; here we invoke them directly.
"""

from unittest.mock import MagicMock

from celery import Task

from app.metrics import ACTIVE_WORKERS, QUEUE_DEPTH
from worker import signals


def _gauge_value(gauge: object, **labels: str) -> float:
    """Read the current value of a (optionally labelled) Prometheus gauge."""
    target = gauge.labels(**labels) if labels else gauge
    return target._value.get()


def _task_on_queue(queue: str) -> Task:
    """Build a fake Celery task whose delivery routing_key is `queue`."""
    task = MagicMock(spec=Task)
    task.request.delivery_info = {"routing_key": queue}
    return task


class TestWorkerLifecycle:
    """worker_process_init / worker_shutdown adjust the active-worker gauge."""

    def test_init_then_shutdown_is_balanced(self) -> None:
        before = _gauge_value(ACTIVE_WORKERS)
        signals.on_worker_init()
        assert _gauge_value(ACTIVE_WORKERS) == before + 1
        signals.on_worker_shutdown()
        assert _gauge_value(ACTIVE_WORKERS) == before


class TestQueueDepthSignals:
    """task_prerun decrements and task_retry increments the queue-depth gauge."""

    def test_prerun_decrements_queue_depth(self) -> None:
        before = _gauge_value(QUEUE_DEPTH, queue_name="high")
        signals.on_task_prerun(task=_task_on_queue("high"))
        assert _gauge_value(QUEUE_DEPTH, queue_name="high") == before - 1

    def test_retry_increments_queue_depth(self) -> None:
        before = _gauge_value(QUEUE_DEPTH, queue_name="low")
        signals.on_task_retry(task=_task_on_queue("low"))
        assert _gauge_value(QUEUE_DEPTH, queue_name="low") == before + 1

    def test_non_task_sender_is_ignored(self) -> None:
        before = _gauge_value(QUEUE_DEPTH, queue_name="default")
        signals.on_task_prerun(task=None)
        signals.on_task_retry(task=None)
        assert _gauge_value(QUEUE_DEPTH, queue_name="default") == before


class TestPostrun:
    """task_postrun only logs; it must not raise on partial kwargs."""

    def test_postrun_handles_missing_kwargs(self) -> None:
        signals.on_task_postrun()
        signals.on_task_postrun(task_id="abc", state="SUCCESS")
