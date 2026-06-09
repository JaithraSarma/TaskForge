# TaskForge — Setup Guide

## Prerequisites

- **Docker** ≥ 24.0 and **Docker Compose** ≥ 2.20
- **Python** ≥ 3.10 (for local development / running scripts)
- **Git**

---

## 1. Clone & Configure

```bash
git clone https://github.com/your-org/taskforge.git
cd taskforge

# Copy the example environment file
cp .env.example .env
```

Edit `.env` if you need to change defaults (ports, credentials, retry config):

```env
# PostgreSQL
POSTGRES_USER=taskforge
POSTGRES_PASSWORD=taskforge_secret
POSTGRES_DB=taskforge

# Redis
REDIS_HOST=taskforge-redis
REDIS_PORT=6379

# Celery
CELERY_BROKER_URL=redis://taskforge-redis:6379/0
CELERY_RESULT_BACKEND=redis://taskforge-redis:6379/1

# Job processing
MAX_RETRIES=5
RETRY_BASE_DELAY=2
```

---

## 2. Start with Docker Compose (Recommended)

```bash
# Build and start all 7 services
docker compose up -d --build

# Watch logs in real-time
docker compose logs -f

# Check service health
docker compose ps
```

### Services Started

| Service              | Port  | Purpose                    |
|---------------------|-------|----------------------------|
| taskforge-api       | 8000  | FastAPI REST API           |
| taskforge-worker    | —     | Celery worker (4 processes)|
| taskforge-redis     | 6379  | Message broker + DLQ       |
| taskforge-db        | 5432  | PostgreSQL job store       |
| taskforge-prometheus| 9090  | Metrics collection         |
| taskforge-grafana   | 3000  | Observability dashboard    |
| taskforge-flower    | 5555  | Celery monitoring UI       |

### Verify Everything is Healthy

```bash
# API health check
curl http://localhost:8000/health

# Expected:
# {"status":"healthy","version":"1.0.0","service":"TaskForge"}
```

---

## 3. Seed Initial Data

After the stack is up, seed the system with one of each job type so Grafana immediately shows data:

```bash
pip install httpx
python scripts/seed_jobs.py
```

---

## 4. Access Dashboards

| Dashboard   | URL                           | Credentials   |
|------------|-------------------------------|---------------|
| API Docs   | http://localhost:8000/docs     | —             |
| ReDoc      | http://localhost:8000/redoc    | —             |
| Grafana    | http://localhost:3000          | admin / admin |
| Prometheus | http://localhost:9090          | —             |
| Flower     | http://localhost:5555          | —             |

The Grafana dashboard is auto-provisioned. Navigate to **Dashboards → TaskForge — Job Processing Dashboard**.

---

## 5. Local Development (Without Docker)

For developing the API or worker locally:

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
.venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt
pip install aiosqlite  # for local SQLite testing

# Start Redis and PostgreSQL (via Docker)
docker compose up -d taskforge-redis taskforge-db

# Run the API
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# In another terminal — run the Celery worker
celery -A worker.celery_app:celery_app worker \
  --loglevel=info --concurrency=2 --queues=high,default,low
```

---

## 6. Running Tests

```bash
# Install test dependencies
pip install aiosqlite pytest-cov

# Run all tests
pytest tests/ -v --tb=short

# Run with coverage
pytest tests/ -v --cov=app --cov=worker --cov-report=term-missing

# Run specific test file
pytest tests/test_api.py -v
pytest tests/test_tasks.py -v
```

---

## 7. Running the Load Test

```bash
# Full 5,000-job test
python scripts/load_test.py --jobs 5000 --concurrency 100

# Quick smoke test (50 jobs)
python scripts/load_test.py --jobs 50 --concurrency 10

# Custom endpoint
python scripts/load_test.py --base-url http://your-server:8000 --jobs 1000
```

The load test reports:
- Submission throughput (jobs/sec)
- Latency percentiles (p50, p95, p99)
- Completion stats (succeeded, failed, dead)
- Success rate

---

## 8. Database Migrations

```bash
# Run migrations (inside Docker)
docker compose exec taskforge-api alembic upgrade head

# Create a new migration
docker compose exec taskforge-api alembic revision --autogenerate -m "description"

# Rollback
docker compose exec taskforge-api alembic downgrade -1
```

Note: The application auto-creates tables on first boot via `init_db()`. Alembic is for production schema evolution.

---

## 9. Stopping & Cleanup

```bash
# Stop all services
docker compose down

# Stop and remove volumes (full reset)
docker compose down -v

# Rebuild after code changes
docker compose up -d --build
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| API returns 500 on job submit | Check Redis is healthy: `docker compose logs taskforge-redis` |
| Jobs stuck in "pending" | Check worker is running: `docker compose logs taskforge-worker` |
| Grafana shows no data | Run `python scripts/seed_jobs.py` to create initial metrics |
| Database connection refused | Wait for PG health check: `docker compose ps` should show "healthy" |
| Worker OOM killed | Reduce concurrency in `docker-compose.yml` (default: 4) |
