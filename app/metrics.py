"""Prometheus metric definitions for TaskForge.

These metrics are shared between the API layer and the Celery worker layer
via the prometheus_client registry.
"""

from prometheus_client import Counter, Gauge, Histogram

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

QUEUE_DEPTH = Gauge(
    "taskforge_queue_depth",
    "Current number of tasks waiting in each queue",
    labelnames=["queue_name"],
)

DLQ_SIZE = Gauge(
    "taskforge_dlq_size",
    "Current number of entries in the dead-letter queue",
)

ACTIVE_WORKERS = Gauge(
    "taskforge_active_workers",
    "Number of currently active worker processes",
)
