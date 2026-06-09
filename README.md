# TaskForge

> Production-grade async background job processing system built with FastAPI, Celery, Redis, PostgreSQL, and full observability via Prometheus + Grafana.

[![CI](https://github.com/JaithraSarma/TaskForge/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/JaithraSarma/TaskForge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  REST Client │────▶│  FastAPI API  │────▶│ Redis Broker │
└─────────────┘     └──────┬───────┘     └──────┬───────┘
                           │                     │
                    ┌──────▼───────┐     ┌──────▼───────┐
                    │  PostgreSQL   │◀────│ Celery Worker│
                    │ (Job Tracker) │     │   (1..N)     │
                    └──────────────┘     └──────┬───────┘
                                                │
                    ┌──────────────┐     ┌──────▼───────┐
                    │   Grafana     │◀────│  Prometheus   │
                    │ (Dashboard)   │     │  (Metrics)    │
                    └──────────────┘     └──────────────┘
```

### Request Flow

1. Client submits job via `POST /api/v1/jobs` with type, payload, priority
2. FastAPI validates, creates a `Job` record in PostgreSQL (`status=pending`)
3. Task is published to Redis via Celery with priority-based queue routing
4. Celery worker dequeues, sets `status=running`, dispatches to handler
5. **Success** → `status=succeeded`, result stored in PostgreSQL
6. **Failure** → exponential backoff retry (2^attempt × base_delay + jitter)
7. **Permanent failure** → `status=dead`, pushed to Dead-Letter Queue (DLQ)
8. Prometheus scrapes `/metrics` from the API; Grafana renders live dashboard

---

## Features

- **Job Submission API** — RESTful endpoints for submit, list, get, cancel
- **Priority Queues** — 3-tier routing (high / default / low) to avoid starvation
- **Retry with Exponential Backoff** — configurable max retries, jitter for thundering herd protection
- **Dead-Letter Queue (DLQ)** — inspect, retry, and purge permanently failed jobs
- **PostgreSQL Job Tracking** — full lifecycle persistence (pending → running → succeeded/failed/dead)
- **Prometheus Metrics** — job throughput, failure rate, queue depth, retry count, latency percentiles
- **Grafana Dashboard** — 4-row live dashboard auto-provisioned on boot
- **Flower** — real-time Celery worker monitoring UI
- **Docker Compose** — full 7-service local stack with health checks
- **GitHub Actions CI** — lint, type check, test, Docker build
- **Load Test** — 5,000 concurrent jobs via asyncio + httpx

---

## Quick Start

```bash
# Clone
git clone https://github.com/your-org/taskforge.git
cd taskforge

# Copy environment file
cp .env.example .env

# Start the full stack
docker compose up -d --build

# Verify services
docker compose ps

# Seed initial data for Grafana
pip install httpx
python scripts/seed_jobs.py
```

### Access Points

| Service     | URL                          |
|-------------|------------------------------|
| API Docs    | http://localhost:8000/docs    |
| Grafana     | http://localhost:3000         |
| Prometheus  | http://localhost:9090         |
| Flower      | http://localhost:5555         |

**Grafana Login:** admin / admin

---

## API Reference

### Jobs

| Method   | Endpoint                | Description                    |
|----------|------------------------|--------------------------------|
| `POST`   | `/api/v1/jobs`         | Submit a new job               |
| `GET`    | `/api/v1/jobs`         | List jobs (paginated, filtered)|
| `GET`    | `/api/v1/jobs/{id}`    | Get job details                |
| `DELETE` | `/api/v1/jobs/{id}`    | Cancel a pending job           |

### Dead-Letter Queue

| Method   | Endpoint                     | Description                 |
|----------|-----------------------------|-----------------------------|
| `GET`    | `/api/v1/dlq`              | List DLQ entries            |
| `GET`    | `/api/v1/dlq/{id}`         | Inspect DLQ entry           |
| `POST`   | `/api/v1/dlq/{id}/retry`   | Re-enqueue a dead job       |
| `DELETE` | `/api/v1/dlq/{id}`         | Purge a DLQ entry           |

### System

| Method | Endpoint    | Description      |
|--------|------------|------------------|
| `GET`  | `/health`  | Liveness probe   |
| `GET`  | `/metrics` | Prometheus metrics|

### Example: Submit a Job

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "email_notification",
    "payload": {"to": "user@example.com", "subject": "Hello"},
    "priority": 2,
    "max_retries": 5
  }'
```

Response:
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending",
  "message": "Job submitted successfully"
}
```

---

## Job Types

| Type                 | Description                | Simulated Failure Rate |
|---------------------|----------------------------|----------------------|
| `email_notification`| Send email via SMTP         | ~10%                 |
| `data_export`       | Export data to CSV/JSON     | ~5%                  |
| `image_resize`      | Resize/compress images      | ~8%                  |
| `webhook_delivery`  | Deliver HTTP callbacks      | ~12%                 |

---

## Failure Scenario Walkthrough

### Scenario 1: Transient Failure → Retry → Success

```
Job submitted → Worker picks up → Handler throws ConnectionError
  → Celery retries in 4s (2^1 × 2 + jitter)
  → Second attempt: another failure
  → Celery retries in 8s (2^2 × 2 + jitter)
  → Third attempt: SUCCESS ✅
  → Status: succeeded, result stored
```

### Scenario 2: Permanent Failure → DLQ

```
Job submitted → 5 consecutive failures
  → Retry 1: 4s delay
  → Retry 2: 8s delay
  → Retry 3: 16s delay
  → Retry 4: 32s delay
  → Retry 5: 64s delay → STILL FAILS
  → Status: dead 💀
  → Pushed to Redis DLQ
  → Visible at GET /api/v1/dlq
  → Can be retried via POST /api/v1/dlq/{id}/retry
```

### Scenario 3: Worker Crash Mid-Execution

```
Worker dequeues task → starts processing → OOM kill / SIGKILL
  → Because acks_late=True, the message is NOT acknowledged
  → Redis visibility timeout expires (default 1hr)
  → Message re-delivered to another worker
  → task_reject_on_worker_lost=True ensures proper rejection
  → Job resumes processing ✅
```

### Scenario 4: Redis Broker Unavailable

```
Redis goes down → API cannot enqueue tasks
  → API returns HTTP 500 (Celery send_task fails)
  → Jobs remain in PostgreSQL with status=pending
  → When Redis recovers, pending jobs can be re-submitted
  → No data loss — PostgreSQL is the source of truth
```

### Scenario 5: PostgreSQL Unavailable

```
PostgreSQL goes down → Workers cannot update status
  → Worker catches DB exception, task is retried
  → acks_late=True means unacknowledged tasks stay in Redis
  → When PostgreSQL recovers, status updates resume
  → No duplicate processing — job ID is idempotent key
```

---

## Load Testing

```bash
# Full 5k load test
python scripts/load_test.py --jobs 5000 --concurrency 100

# Quick smoke test
python scripts/load_test.py --jobs 50 --concurrency 10

# First-boot seed (one of each type)
python scripts/seed_jobs.py
```

---

## Project Structure

```
taskforge/
├── .github/workflows/ci.yml    # GitHub Actions CI
├── app/                         # FastAPI service
│   ├── api/                     # REST endpoints
│   │   ├── router.py            # Job CRUD
│   │   └── dlq.py               # DLQ management
│   ├── config.py                # Environment config
│   ├── database.py              # Async SQLAlchemy
│   ├── main.py                  # App factory
│   ├── metrics.py               # Prometheus metrics
│   ├── models.py                # ORM models
│   └── schemas.py               # Pydantic schemas
├── worker/                      # Celery workers
│   ├── celery_app.py            # App config (acks_late!)
│   ├── handlers.py              # Job type handlers
│   ├── signals.py               # Lifecycle signals
│   └── tasks.py                 # Task definitions
├── migrations/                  # Alembic migrations
├── monitoring/                  # Prometheus + Grafana
├── scripts/                     # Load test + seed
├── tests/                       # pytest suite
├── docker-compose.yml           # 7-service stack
├── Dockerfile                   # Multi-stage build
└── README.md                    # You are here
```

---

## License

MIT — see [LICENSE](LICENSE).


<!-- Verified Python 3.10+ PEP 585/604 compliance -->


<!-- Verified Python 3.10+ PEP 585/604 compliance -->


<!-- Verified Python 3.10+ PEP 585/604 compliance -->
