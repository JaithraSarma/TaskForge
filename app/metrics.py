"""Prometheus metric definitions for TaskForge.

These metrics are shared between the API layer and the Celery worker layer
via the prometheus_client registry.
"""

from collections.abc import Iterator
from typing import cast

import redis
from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from app.config import get_settings

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

JOBS_SUBMITTED_TOTAL = Counter(
    "taskforge_jobs_submitted_total",
    "Total number of jobs submitted to the system",
    labelnames=["job_type", "priority"],
)

JOBS_COMPLETED_TOTAL = Counter(
    "taskforge_jobs_completed_total",
    "Total number of jobs that reached a terminal state",
    labelnames=["job_type", "status"],
)

JOBS_RETRY_TOTAL = Counter(
    "taskforge_jobs_retry_total",
    "Total number of job retry attempts",
    labelnames=["job_type"],
)

# ---------------------------------------------------------------------------
# Histograms
# ---------------------------------------------------------------------------

JOB_DURATION_SECONDS = Histogram(
    "taskforge_job_duration_seconds",
    "Time taken to process a job from start to completion",
    labelnames=["job_type"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------

# multiprocess_mode="livesum": the API and the workers run as separate
# processes writing to a shared multiprocess directory. "livesum" sums each
# live process's value into one series (and drops processes that have exited),
# which is the correct aggregation for these event-driven gauges (DLQ_SIZE,
# ACTIVE_WORKERS). Without it the default per-process series never combine
# into a meaningful total.

DLQ_SIZE = Gauge(
    "taskforge_dlq_size",
    "Current number of entries in the dead-letter queue",
    multiprocess_mode="livesum",
)

ACTIVE_WORKERS = Gauge(
    "taskforge_active_workers",
    "Number of currently active worker processes",
    multiprocess_mode="livesum",
)


class QueueDepthCollector(Collector):
    """Expose queue depth by reading the broker's Redis list lengths at scrape time.

    This is the broker's own source of truth, so it stays correct across retries
    and multiple worker processes, unlike an inc/dec counter which can drift.
    """

    _QUEUES = ("high", "default", "low")

    def collect(self) -> Iterator[GaugeMetricFamily]:
        """Yield one taskforge_queue_depth gauge with a sample per queue."""
        family = GaugeMetricFamily(
            "taskforge_queue_depth",
            "Tasks currently waiting in each priority queue, read from the broker.",
            labels=["queue_name"],
        )
        try:
            client = redis.Redis.from_url(get_settings().celery_broker_url)
            try:
                for queue_name in self._QUEUES:
                    # decode_responses is irrelevant to LLEN; cast past redis-py's
                    # sync/async union return type so mypy sees a plain int.
                    length = cast("int", client.llen(queue_name))
                    family.add_metric([queue_name], float(length))
            finally:
                client.close()
        except Exception:
            # A metrics scrape must never fail because the broker is unreachable.
            pass
        yield family


QUEUE_DEPTH_COLLECTOR = QueueDepthCollector()
