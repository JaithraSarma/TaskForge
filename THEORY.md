# TaskForge: Complete Reference and Presenter's Guide

This document is the single reference for understanding, presenting, and defending
TaskForge in an interview. It explains what the system does, why each design choice
was made, what every file is responsible for, the bugs that were found and fixed
while hardening it, the reliability guarantees it makes, and a set of anticipated
interview questions with crisp answers.

Read sections 1 to 3 for the story, section 4 for the code tour, section 5 for the
design rationale, section 6 for the debugging log (the most interesting part to talk
about), and section 10 for the question bank.

---

## 1. What TaskForge Is

TaskForge is a distributed background job processing system. A client sends a small
HTTP request describing work to be done, the API accepts it immediately and returns
`202 Accepted`, and the actual work runs later on a pool of worker processes. This is
the same pattern behind sending email, generating reports, processing uploads, and
delivering webhooks in real products.

The problem it solves: some work is too slow to do inside a web request. If an API
did the work inline, the caller would wait seconds or minutes and a traffic spike
would exhaust the web server. TaskForge decouples "accepting work" from "doing work"
so the API stays fast and the workers absorb load at their own pace.

Core properties:

- Accepts work over a REST API and returns in milliseconds.
- Runs work asynchronously on a horizontally scalable worker pool.
- Guarantees at-least-once execution: a job is not silently lost if a worker dies.
- Retries transient failures with exponential backoff and jitter.
- Sends permanently failed jobs to a dead-letter queue for inspection and replay.
- Exposes full metrics and structured logs so its behaviour is observable.
- Ships as a Docker Compose stack and a set of Kubernetes manifests with autoscaling.

---

## 2. The Technology Stack and Why Each Piece

| Layer | Technology | Why |
|---|---|---|
| API framework | FastAPI, Uvicorn, Pydantic v2 | Async request handling, automatic OpenAPI docs, strict request validation |
| Task queue | Celery 5 | Mature work-queue framework with retries, routing, and worker lifecycle signals |
| Broker and DLQ | Redis 7 | Fast in-memory broker; also holds the dead-letter list |
| Database | PostgreSQL 16, SQLAlchemy 2.0, Alembic | Durable source of truth for job state; JSONB payloads; versioned schema |
| Metrics | prometheus-client, Prometheus, Grafana | Numeric time-series for throughput, latency, failures, and queue health |
| Worker inspector | Flower | Live view of Celery workers and tasks |
| Packaging | Docker, Docker Compose, Kubernetes | Reproducible local stack and a production-shaped deployment |
| CI/CD | GitHub Actions, GHCR, Trivy | Lint, type-check, test, build, publish, and scan on every push |
| Quality | ruff, mypy, pytest, pre-commit | Style, static types, tests with a coverage floor, pre-push gates |

Key architectural decision: the API and the workers use two different database
drivers on purpose. The API is asynchronous and uses asyncpg through SQLAlchemy's
async engine so a slow query never blocks the event loop. Celery's prefork worker
pool is synchronous, so the worker uses the plain psycopg2 driver. Mixing an async
driver into a sync worker process does not work, so the two are kept explicit with
separate `DATABASE_URL` and `DATABASE_URL_SYNC` settings.

---

## 3. End-to-End Flow

The lifecycle of a single job, step by step:

1. Client sends `POST /api/v1/jobs` with a job type, a JSON payload, a numeric
   priority (1 to 10), and a max-retries count.
2. FastAPI validates the body with a Pydantic schema. Invalid input is rejected with
   `422` before anything is persisted.
3. The API writes a `Job` row to PostgreSQL with status `pending` and commits it.
4. Only after the commit does the API publish a task to Redis. Priority maps to one
   of three queues: `high` (1 to 3), `default` (4 to 7), `low` (8 to 10).
5. The API returns `202 Accepted` with the job id. The client never waits for the work.
6. A Celery worker pulls the task from Redis, flips the row to `running`, and calls
   the handler registered for that job type.
7. On success the row becomes `succeeded` and the result is written back.
8. On failure the worker increments the retry count and either reschedules the task
   with exponential backoff plus jitter, or, once retries are exhausted, marks the
   row `dead` and pushes a record onto the Redis dead-letter list.
9. Prometheus scrapes the API's `/metrics` endpoint. Worker-side counters reach
   Prometheus through a shared multiprocess directory that the API reads and merges.
10. Grafana renders the metrics. Flower shows live worker and task state.

PostgreSQL is the source of truth for job state. Redis only holds in-flight queue
messages and the dead-letter list.

---

## 4. File-by-File Reference

### Application (`app/`)

- `app/main.py`: The FastAPI application factory. Builds the app, installs the CORS
  middleware and a request-id middleware, wires the Prometheus instrumentation and
  the `/metrics` endpoint (multiprocess-aware), includes the routers, and defines the
  `/health` liveness and `/health/ready` readiness probes. The lifespan hook
  configures logging and initializes the database on startup.
- `app/config.py`: Pydantic-settings `Settings` loaded from environment variables:
  database URLs, Redis and Celery URLs, retry parameters, CORS origins, and logging
  level and format. Cached with `lru_cache` so it is read once.
- `app/database.py`: The async SQLAlchemy engine and session factory used by the API,
  the `get_db` dependency (opens a session, commits on success, rolls back on error),
  and `init_db` which creates tables under a PostgreSQL advisory lock so concurrent
  API workers do not race on schema creation.
- `app/models.py`: The `Job` ORM model and the `JobStatus` enum (pending, running,
  succeeded, failed, dead). Columns include the payload (JSONB), result, error text,
  retry and max-retry counts, the Celery task id, and timestamps.
- `app/schemas.py`: Pydantic request and response models (job creation, job response,
  paginated lists, DLQ entries, health and readiness responses). These define the API
  contract and validation rules.
- `app/metrics.py`: All Prometheus metric definitions (counters, a histogram, gauges)
  and the `QueueDepthCollector`, which reports queue depth by reading the broker's
  Redis list lengths at scrape time.
- `app/logging_config.py`: Structured logging. A JSON formatter, a context variable
  that carries a correlation id, a filter that stamps that id on every record, and
  `configure_logging`, which installs a single stdout handler and routes Uvicorn's
  loggers through it.
- `app/api/router.py`: The job endpoints. Submit (`POST`), get one (`GET /{id}`), list
  with filters and pagination (`GET`), and cancel a pending job (`DELETE /{id}`). Also
  the `_priority_to_queue` helper that maps a numeric priority to a named queue.
- `app/api/dlq.py`: The dead-letter queue endpoints. List, inspect one, retry
  (reset to pending and re-enqueue), and purge. A helper removes the matching entry
  from the Redis dead-letter list.

### Worker (`worker/`)

- `worker/celery_app.py`: The Celery application and its configuration: JSON
  serialization, reliability settings (late acking, reject-on-worker-lost, broker
  visibility timeout), time limits, prefetch and concurrency, queue definitions and
  routing, result expiry, and UTC. It also imports `worker.signals` so the signal
  handlers register.
- `worker/tasks.py`: The `process_job` task. Uses a synchronous psycopg2 engine.
  Fetches the job, sets it running, dispatches to the handler, and on success or
  failure updates the row and the metrics. On failure it either retries with backoff
  plus jitter or dead-letters the job. Binds the job id as the logging correlation id.
- `worker/handlers.py`: The registry of job handlers. Four simulated handlers
  (email notification, data export, image resize, webhook delivery), each with a
  realistic latency and a small random failure rate so retries and the DLQ are
  actually exercised. `get_handler` looks a handler up by job type.
- `worker/signals.py`: Celery signal handlers. Configures logging for the worker,
  increments the active-worker gauge when a process starts, cleans up that process's
  metric files on shutdown, and logs task completion.

### Tests (`tests/`)

- `tests/conftest.py`: Shared fixtures. Sets test environment variables, an async
  SQLite engine, an override for the database dependency, table setup and teardown per
  test, a mocked Celery app, and a FastAPI test client.
- `tests/test_api.py`: Job submission, retrieval, listing, pagination, filtering,
  cancellation, and the health and readiness endpoints.
- `tests/test_dlq.py`: DLQ list, inspect, retry, and purge.
- `tests/test_tasks.py`: The handler registry and each handler's success and failure
  paths.
- `tests/test_signals.py`: The worker lifecycle signal handlers.
- `tests/test_logging.py`: The JSON formatter and the request-id middleware.
- `tests/test_metrics.py`: The broker-backed queue-depth collector, including its
  behaviour when the broker is unreachable.
- `tests/test_database.py`: That `init_db` is safe to call repeatedly.

### Infrastructure and tooling

- `Dockerfile`: Multi-stage build. A base stage installs system and Python
  dependencies and patches them, and drops pip afterward to remove its vendored
  copies. The app stage adds the code, a non-root user, and a health check. One image
  serves both the API and the workers with different commands.
- `entrypoint.sh`: Runs as root only long enough to make the Prometheus multiprocess
  directory writable by the non-root user, then drops privileges with gosu and execs
  the given command.
- `docker-compose.yml`: The full local stack: Postgres, Redis, the API, the worker,
  Prometheus, Grafana, and Flower, with health checks and a shared multiprocess volume.
- `k8s/`: Kubernetes manifests mirroring the compose topology. Namespace, ConfigMap,
  Secret, a Postgres StatefulSet, a Redis Deployment, the API Deployment (2 replicas),
  the worker Deployment, and a HorizontalPodAutoscaler. See `k8s/README.md`.
- `monitoring/`: Prometheus scrape configuration and the provisioned Grafana
  data source and dashboard.
- `migrations/`: Alembic environment and the initial migration that creates the jobs
  table, the status enum, and the indexes.
- `.github/workflows/ci.yml`: Lint, type-check, tests with a coverage floor, and a
  Docker build on every push and pull request.
- `.github/workflows/cd.yml`: Builds and publishes the image to GHCR on pushes to main
  and tags, and scans it with Trivy (reporting only fixable findings).
- `scripts/seed_jobs.py` and `scripts/load_test.py`: One job of each type, and a
  concurrent load generator.
- `pyproject.toml`, `requirements.txt`, `Makefile`, `.pre-commit-config.yaml`:
  Tooling configuration, dependencies, developer commands, and pre-commit hooks.

---

## 5. Design Decisions and Rationale

- Late acknowledgment (`task_acks_late=True`). Celery's default acks a message before
  running it, so a worker that is killed mid-task loses the job silently. Acking late
  keeps the message on the broker until the task finishes; if the worker dies, the
  message is redelivered.

- Reject on worker lost (`task_reject_on_worker_lost=True`). If the worker process is
  lost, the task is rejected back to the broker rather than acknowledged as complete.

- Broker visibility timeout (`visibility_timeout=600`). With the Redis broker, a task
  whose worker is lost is only redelivered after the visibility timeout, which
  defaults to one hour. Set to 600 seconds, comfortably above the 300 second hard time
  limit, so an ungracefully killed task is redelivered within minutes, not an hour,
  while a legitimately long task is never redelivered while still running.

- Commit before enqueue. The job row is committed to PostgreSQL as pending before the
  Celery task is published. If the task were published first, a fast worker could
  dequeue and query the row before the API's transaction committed, find nothing, and
  drop the job. This applies to both submission and DLQ retry.

- Two database drivers. asyncpg for the async API, psycopg2 for the sync worker, kept
  explicit rather than papered over.

- Priority routing over a single queue. Numeric priority maps to high, default, and
  low queues at submit time so a burst of low-priority work cannot starve
  time-sensitive work.

- Exponential backoff with jitter. Retry delay is `2^retry * base + random(0, 1)`.
  Plain exponential backoff makes every failed job retry in lockstep; the jitter term
  spreads retries so a recovering dependency is not hit by a synchronized wave.

- Dead-letter queue. Once retries are exhausted, the job is marked dead and pushed to
  a Redis list. A dedicated API lets an operator inspect the payload and error, replay
  the job, or purge it, without digging through logs.

- Liveness versus readiness. `/health` returns 200 whenever the process is up.
  `/health/ready` actually pings Postgres and Redis and returns 503 if either is down.
  Kubernetes uses the first to decide whether to restart a pod and the second to
  decide whether to send it traffic.

- Multiprocess Prometheus. The API runs multiple Uvicorn workers and the worker runs
  multiple processes. Each writes to a shared directory and the API's `/metrics`
  endpoint merges them. Per-worker gauges use `multiprocess_mode="livesum"` so live
  processes' values combine, and each worker cleans up its files on exit so the count
  does not accumulate across restarts. Queue depth is not a counter at all; it is read
  from the broker at scrape time, which stays correct across retries.

- Structured JSON logging with correlation ids. Every log line is JSON with a
  `request_id`. The API generates one per request and echoes it on the response; the
  worker binds the job id it is processing. That single field lets you follow one job
  from the API call, through the queue, to the worker that ran it.

- Advisory-locked schema init. With more than one API worker, both would run
  `create_all` and race on creating the status enum type. A PostgreSQL advisory lock
  serializes them.

- Horizontal autoscaling. The API Deployment is fronted by a HorizontalPodAutoscaler
  that scales from 2 to 6 pods at 70 percent CPU utilization.

---

## 6. Debugging Log: Bugs Found and Fixed

This section is the most useful to talk through in an interview, because it shows how
the system was hardened by running it, not just by unit testing it. The unit tests run
against SQLite and an in-memory broker, so a whole class of issues only appeared when
the real Postgres, Redis, Celery, and Prometheus stack was running. Each entry lists
the symptom, the root cause, and the fix.

1. Worker signals never registered.
   Symptom: `active_workers` and the queue metrics were always zero in a running
   worker. Root cause: Celery autodiscovery imported `worker.tasks` but nothing
   imported `worker.signals`, so the signal handlers never connected. Fix: import
   `worker.signals` from `celery_app.py`.

2. Celery hijacked logging.
   Symptom: worker logs were plain text even though structured logging was configured.
   Root cause: Celery installs its own root logger by default. Fix: connect the
   `setup_logging` signal so Celery yields logging configuration to the app.

3. API logs were not JSON.
   Symptom: the API container emitted Uvicorn's default log format. Root cause:
   Uvicorn installs handlers on its own loggers, which do not propagate to the root
   handler. Fix: clear those handlers and route them through the structured handler.

4. Enqueue-before-commit race on submit.
   Symptom: occasional job stuck in pending with a worker log line "Job not found in
   database, skipping". Root cause: the task was published while the row was only
   flushed, not committed; a fast worker read the row before the commit landed. Fix:
   commit the row before publishing the task.

5. Same race on DLQ retry, with a worse effect.
   Symptom: a retried job that failed again ended up dead with a null error. Root
   cause: the retry endpoint flushed the reset and relied on the request teardown to
   commit, so the worker reprocessed the job and wrote its outcome, after which the
   deferred commit clobbered it back to the reset state. Fix: commit the reset before
   re-enqueuing.

6. `/metrics` returned 500 with "file corrupted".
   Symptom: after some time the metrics endpoint failed for every request. Root cause:
   the worker liveness probe ran `celery inspect ping` every 20 seconds; that
   short-lived process imported the app and created Prometheus multiprocess files that
   accumulated in the shared directory, and one was left half-written. Fix: unset
   `PROMETHEUS_MULTIPROC_DIR` for the probe command so it writes no metric files.

7. Destructive startup wipe erased live metrics.
   Symptom: `active_workers` read zero right after the API restarted. Root cause: the
   API wiped the shared multiprocess directory on startup, deleting the workers' live
   metric files. Fix: stop wiping on startup; a clean slate comes from recreating the
   volume.

8. `active_workers` accumulated across restarts.
   Symptom: after a worker restart the gauge read 8 or higher instead of the
   concurrency of 4. Root cause: dead worker processes' metric files were never
   removed, so `livesum` kept counting them. Fix: call `mark_process_dead` on
   worker-process shutdown.

9. `queue_depth` drifted persistently negative.
   Symptom: after a batch with retries, queue depth read a large negative number and
   never recovered. Root cause: it was incremented once per submission but decremented
   per task attempt, so retries unbalanced it. Fix: stop tracking it with a counter;
   read the broker's Redis list lengths at scrape time.

10. Startup enum race.
    Symptom: a `UniqueViolationError` traceback on every startup with two API workers.
    Root cause: both workers ran `create_all` and raced on the status enum type. Fix:
    a PostgreSQL advisory lock around schema creation.

11. Kubernetes worker crash-loop.
    Symptom: worker pods in CrashLoopBackOff. Root cause: the ConfigMap sets the
    multiprocess directory and the worker imports the metrics module at startup, which
    creates files there, but the worker Deployment had no volume for that path, unlike
    the API Deployment. Fix: add the same emptyDir volume and mount to the worker pod.

12. Hard-time-limit kills left jobs stuck running.
    Symptom: a job killed by the hard time limit stayed in running for up to an hour.
    Root cause: the hard limit sends SIGKILL to the worker child, so the task's own
    error-handling code never runs, and redelivery waited for the default one-hour
    visibility timeout. Fix: set the broker visibility timeout to 600 seconds. The soft
    time limit remains the graceful path that retries or dead-letters.

13. Image CVEs.
    Symptom: the base image reported 3 critical and 22 high OS-level CVEs. Fix: patch
    OS packages during the build, bump the vulnerable Python packages, drop pip's
    vendored copies, and set Trivy to report only fixable findings. Result: zero
    fixable critical or high vulnerabilities.

14. Type check passed locally but failed in CI.
    Symptom: mypy flagged the queue-depth collector only in CI. Root cause: CI used a
    newer, stricter mypy. Fix: make the collector a proper `Collector` subclass.

15. Migration failed lint.
    Symptom: `ruff` failed on the Alembic migration's `Union` type hints. Fix: modern
    union syntax, and add the migrations directory to the CI lint path.

Every fix above was verified by running the real stack or, where relevant, the
Kubernetes cluster, not by asserting it in a test alone.

---

## 7. Reliability and Failure Modes

What happens when things go wrong, and what the system guarantees:

- Worker crash mid-task. The message was not acked (late acking), so the broker
  redelivers it. If the whole worker process is lost, the message becomes visible
  again after the visibility timeout and is redelivered. Guarantee: at-least-once.
- Transient handler failure. The task retries with exponential backoff and jitter up
  to the job's max retries, then is dead-lettered.
- Dependency down at submit. If publishing the task fails, the request errors and the
  transaction is consistent; the job is not left half-created in an unknown state.
- Database or Redis unavailable. `/health/ready` returns 503 so Kubernetes stops
  routing traffic to that pod.
- Poison job that always fails. It exhausts retries and lands in the DLQ, where an
  operator can inspect and decide what to do.
- Duplicate execution. Because delivery is at-least-once, a job can run more than once
  in rare redelivery cases; handlers should be idempotent for production payloads.
  This is a deliberate, documented trade-off, not an oversight.

---

## 8. Observability

Metrics exposed at `/metrics`:

- `taskforge_jobs_submitted_total` (counter, by job type and priority)
- `taskforge_jobs_completed_total` (counter, by job type and terminal status)
- `taskforge_jobs_retry_total` (counter, by job type)
- `taskforge_job_duration_seconds` (histogram, by job type)
- `taskforge_queue_depth` (gauge, by queue, read from the broker)
- `taskforge_dlq_size` (gauge)
- `taskforge_active_workers` (gauge)

Standard HTTP request metrics (rate, latency, status codes) are added automatically.
The Grafana dashboard groups these into throughput, queue health, latency, failures
and retries, and HTTP endpoint rows. Logs are single-line JSON with a correlation id,
so one job is traceable end to end.

This maps onto two standard frameworks: the RED method (Rate, Errors, Duration) for
the request and job flow, and the USE method (Utilization, Saturation, Errors) for
worker and queue health.

---

## 9. Deployment, CI/CD, and Security

- Local: `docker compose up -d --build` brings up the seven services. Reset with
  `docker compose down -v`.
- Kubernetes: apply the manifests, which include probes wired to the health endpoints
  and a HorizontalPodAutoscaler on the API. The worker and API share a multiprocess
  metrics volume within their own pods.
- CI runs ruff, mypy, the pytest suite with a coverage floor, and a Docker build on
  every push. CD publishes the image to GHCR and scans it with Trivy.
- Security: the container runs as a non-root user, privileges are dropped with gosu
  after the one root-only setup step, CORS credentials are disabled when origins are a
  wildcard, and the image is patched to zero fixable critical or high CVEs.

---

## 10. Interview Question Bank

Anticipated questions and short, defensible answers.

- How do you guarantee a job is not lost if a worker crashes?
  Late acking keeps the message on the broker until the task actually finishes, and
  reject-on-worker-lost plus a broker visibility timeout ensure an ungracefully lost
  task is redelivered. The guarantee is at-least-once.

- Why at-least-once and not exactly-once?
  Exactly-once across a network is effectively impossible without heavy coordination.
  At-least-once plus idempotent handlers is the standard, pragmatic choice.

- Why two database drivers?
  The API is async and must not block its event loop, so it uses asyncpg. Celery's
  prefork pool is synchronous, so the worker uses psycopg2. Mixing them does not work.

- How does priority work, and why not one queue?
  Priority 1 to 10 maps to high, default, and low queues at submit time so a flood of
  low-priority work cannot starve urgent work.

- What happens when a handler keeps failing?
  It retries with exponential backoff and jitter up to its max retries, then is
  dead-lettered for inspection and replay.

- Why add jitter to backoff?
  Plain backoff makes all failed jobs retry at the same instants; jitter spreads them
  so a recovering dependency is not hit by a synchronized wave.

- How do metrics work when there are many processes?
  Every process writes to a shared multiprocess directory; the API merges them on
  scrape. Per-worker gauges use livesum and clean up on exit; queue depth is read
  live from the broker.

- How did you find the hardest bugs?
  By running the real stack, not just unit tests. For example, the metrics endpoint
  returned 500 because the liveness probe was leaving stray metric files, and a retried
  job lost its error because the reset committed after the worker had already
  reprocessed it. Both were fixed and re-verified live.

- How does autoscaling work?
  A HorizontalPodAutoscaler watches API CPU and scales the Deployment from 2 to 6 pods
  at 70 percent utilization. It was verified by driving load and observing the scale-up.

- How is the image kept secure?
  OS and Python packages are patched during the build, pip's vendored copies are
  removed, and Trivy scans for fixable critical and high CVEs, of which there are none.

- What would you improve next?
  Idempotency keys for handlers, a reconciler that fails jobs stuck running past the
  hard limit rather than waiting for redelivery, and rate limiting and authentication
  on the API for a public deployment.

---

## 11. Known Limitations and Honest Caveats

- The four handlers simulate work with a random failure rate; they are stand-ins for
  real integrations so retries and the DLQ can be demonstrated without external
  services.
- Delivery is at-least-once, so production handlers should be idempotent.
- Metrics state lives in a shared volume and is reset with `docker compose down -v`;
  a plain restart preserves it by design.
- The base image is debian-based, so some deferred, no-fix-available OS CVEs remain;
  Trivy is configured to report only fixable findings, of which there are none.
