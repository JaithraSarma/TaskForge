"""Tests for the Celery worker lifecycle signal handlers.

The handlers keep the Prometheus gauges in sync with worker state.
They fire automatically in a running worker; here we invoke them directly.
"""

from app.metrics import ACTIVE_WORKERS
from worker import signals


def _gauge_value(gauge: object, **labels: str) -> float:
    """Read the current value of a (optionally labelled) Prometheus gauge."""
    target = gauge.labels(**labels) if labels else gauge
    return target._value.get()


class TestWorkerLifecycle:
    """worker_process_init / worker_process_shutdown adjust the active-worker gauge."""

    def test_init_then_shutdown_is_balanced(self) -> None:
        before = _gauge_value(ACTIVE_WORKERS)
        signals.on_worker_init()
        assert _gauge_value(ACTIVE_WORKERS) == before + 1
        signals.on_worker_process_shutdown()
        assert _gauge_value(ACTIVE_WORKERS) == before


class TestPostrun:
    """task_postrun only logs; it must not raise on partial kwargs."""

    def test_postrun_handles_missing_kwargs(self) -> None:
        signals.on_task_postrun()
        signals.on_task_postrun(task_id="abc", state="SUCCESS")
