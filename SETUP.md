# TaskForge — Setup Runbook

Step-by-step instructions to get TaskForge running locally, verify it's actually working end to end, and tear it down. Each step says what to run and what output means it worked.

---

## 1. Prerequisites

| Tool | Check | Needed for |
|---|---|---|
| Docker Desktop (or Docker Engine + Compose v2) | `docker compose version` → v2.20+ | Running the full stack |
| Python 3.10+ | `python --version` | Local (non-Docker) dev, scripts, tests |
| `make` (optional) | `make --version` | Shortcuts for the commands below — everything also works without it |
| `kubectl` + a local cluster (optional) | `kubectl version --client` | Section 8 only |

If `docker compose version` fails, install/start Docker Desktop first — nothing else in this guide works without it.

---

## 2. Clone and configure

```bash
git clone https://github.com/JaithraSarma/TaskForge.git
cd TaskForge
cp .env.example .env
```

The defaults in `.env.example` are self-consistent for local Docker Compose use (same Postgres/Redis credentials the compose file expects). You don't need to edit anything to get a working stack — only change values if you're changing ports, credentials, or retry behavior.

---

## 3. Bring the stack up

```bash
docker compose up -d --build
```

First build takes a few minutes (installs `libpq-dev`, `gcc`, and the Python dependencies). Then check status:

```bash
docker compose ps
```

Expected — all seven services `Up`, and the ones with healthchecks (`db`, `redis`, `api`, `worker`) reporting `healthy`:

```
NAME                    IMAGE                    STATUS
taskforge-api           taskforge:latest         Up (healthy)
taskforge-worker        taskforge:latest         Up (healthy)
taskforge-db            postgres:16-alpine       Up (healthy)
taskforge-redis         redis:7-alpine           Up (healthy)
taskforge-flower        mher/flower:2.0          Up
taskforge-prometheus    prom/prometheus:latest   Up
taskforge-grafana       grafana/grafana:latest   Up
```

If `taskforge-api` isn't healthy yet, give it another 10-15s (its healthcheck has a `start_period` of 15s) and re-run `docker compose ps`. If it stays unhealthy, jump to Troubleshooting (§10).

---

## 4. Verify the API

### Liveness

```bash
curl http://localhost:8000/health
```

```json
{"status":"healthy","version":"1.0.0","service":"TaskForge"}
```

This only proves the process is up — it doesn't check dependencies.

### Readiness

```bash
curl http://localhost:8000/health/ready
```

```json
{
  "status": "ready",
  "checks": {"database": "ok", "redis": "ok"},
  "version": "1.0.0",
  "service": "TaskForge"
}
```

If either dependency is unreachable, this returns HTTP `503` with the corresponding check set to `"unavailable"`.

### Submit a job

```bash
curl -i -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "email_notification",
    "payload": {"to": "you@example.com", "subject": "First job"},
    "priority": 2,
    "max_retries": 5
  }'
```

Expected: `HTTP/1.1 202 Accepted` with a body like:

```json
{
  "id": "5c9d3b7a-1234-4a9b-8e10-abcdef123456",
  "status": "pending",
  "message": "Job submitted successfully"
}
```

Save the `id` and fetch it back a few times over the next couple of seconds:

```bash
curl http://localhost:8000/api/v1/jobs/<id>
```

You'll see `status` move through the lifecycle: `pending` → `running` → `succeeded` (or, on the ~10% simulated failure path, back to `pending` for a retry, eventually `succeeded` or `dead` after `max_retries` is exhausted). A succeeded job's response includes a populated `result` object; a dead job has `error` populated with the exception and traceback (truncated to 2000 chars).

### Trigger and inspect a DLQ entry

Every handler has a randomized failure rate, so you *can* wait for a natural failure — but for a deterministic demo, submit a job with an unregistered `job_type` and `max_retries: 0`. `worker/handlers.py` raises `KeyError` immediately for unknown types, and with zero retries allowed the job goes straight to `dead`:

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_type": "not_a_real_type", "payload": {}, "max_retries": 0}'
```

Then check the DLQ:

```bash
curl http://localhost:8000/api/v1/dlq
```

```json
{
  "entries": [
    {
      "id": "...",
      "job_type": "not_a_real_type",
      "payload": {},
      "error": "KeyError: \"Unknown job type 'not_a_real_type'. ...",
      "retry_count": 1,
      "max_retries": 0,
      "created_at": "...",
      "completed_at": "..."
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

Retry it (resets state, re-enqueues) or purge it permanently:

```bash
curl -X POST http://localhost:8000/api/v1/dlq/<id>/retry
curl -X DELETE http://localhost:8000/api/v1/dlq/<id>
```

---

## 5. Watch it work

Seed a few jobs first so there's something to look at:

```bash
pip install httpx
python scripts/seed_jobs.py
```

- **Flower** — http://localhost:5555 — live view of the Celery workers, active/processed task counts, and per-task detail (args, runtime, result).
- **Grafana** — http://localhost:3000 (`admin` / `admin`) → **Dashboards → TaskForge — Job Processing Dashboard**. Five rows: **Throughput** (submitted/completed per sec, success rate), **Queue Health** (queue depth per priority, DLQ size, active workers), **Latency** (duration percentiles overall and p95 by job type), **Failures & Retries** (failure rate, retry rate, totals by status), **HTTP Endpoint Metrics** (request rate/latency for the API itself).
- **Prometheus** — http://localhost:9090 → **Status → Targets** should show the `taskforge-api` job as `UP`. Try a query like `taskforge_jobs_submitted_total` in the expression browser.

---

## 6. Run tests, lint, and typecheck locally

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

pip install -r requirements.txt
pip install aiosqlite pytest-cov   # test-only deps, not in requirements.txt
```

```bash
make test        # or: pytest tests/ -q
```

The suite runs against SQLite (async via `aiosqlite`, sync via the stdlib driver) and an in-memory Celery broker — no Docker services required. Expected shape:

```
.....................................                                    [100%]
37 passed, 1 warning in 6.04s
```

(exact count/timing will drift as tests are added; the important part is `N passed` with no failures)

```bash
make lint        # ruff check app/ worker/ tests/ scripts/ migrations/
```
```
All checks passed!
```

```bash
make typecheck    # mypy app/ worker/ --ignore-missing-imports
```
```
Success: no issues found in ... source files
```

To run the same lint and type checks automatically on every commit, install the git hooks once:

```bash
pip install pre-commit
pre-commit install
make precommit    # optional: run all hooks against the whole tree now
```

---

## 7. Load test

With the Docker stack still running:

```bash
# smoke test
python scripts/load_test.py --jobs 50 --concurrency 10

# heavier run
python scripts/load_test.py --jobs 500 --concurrency 50   # == make load-test
```

It submits jobs concurrently via `asyncio`/`httpx`, then polls for completion and prints a report:

```
============================================================
  TASKFORGE LOAD TEST REPORT
============================================================

  Total jobs requested:     500
  Successfully submitted:   500
  Submission errors:        0
  Total wall time:          <n>s
  Submission throughput:    <n> jobs/sec

  Submission Latency:
     p50:  <n>ms
     p95:  <n>ms
     p99:  <n>ms
     max:  <n>ms

  Completion Stats:
     succeeded   : <n>
     dead        : <n>

  Success Rate: <n>%
============================================================
```

What to look for: submission throughput should stay roughly flat as concurrency increases (the API is just doing a validate + INSERT + enqueue); a success rate below ~85-90% suggests you're seeing more than the handlers' baseline simulated failure rate (5-12% per type) and worth checking worker logs. Watch Grafana's Throughput and Queue Health rows while it runs — queue depth should rise during Phase 1 (submission) and drain during Phase 2 (workers catching up).

---

## 8. Kubernetes (optional)

Requires a local cluster (kind or minikube) with `kubectl` pointed at it.

```bash
docker build -t taskforge:latest .

kind load docker-image taskforge:latest
# or: minikube image load taskforge:latest

kubectl apply -f k8s/
kubectl -n taskforge get pods -w
```

Wait for all pods to reach `Running`/`1/1 Ready` (Postgres's `StatefulSet` and the API's readiness probe both take a few seconds to go green). Then:

```bash
kubectl -n taskforge port-forward svc/taskforge-api 8000:8000
curl http://localhost:8000/health/ready
```

Same `{"status": "ready", ...}` shape as §4. See [k8s/README.md](k8s/README.md) for the HPA, the worker's exec-based liveness probe (`celery inspect ping`), and the caveats around the committed demo `Secret`.

Teardown:

```bash
kubectl delete -f k8s/
```

---

## 9. Teardown

```bash
make down                 # docker compose down
docker compose down -v    # also remove volumes (full reset — drops Postgres data)
```

---

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `taskforge-api` never reports healthy | Postgres or Redis healthcheck hasn't passed yet — `docker compose logs taskforge-db taskforge-redis`. The API container also `sleep 3`s before starting as a cheap buffer; if it's still failing after that, check `docker compose logs taskforge-api` for a connection error. |
| Job submission returns 500 | Redis is unreachable — `celery_app.send_task` raises inside the request, and since the DB write and the enqueue happen in the same request transaction, the job row is rolled back too (it won't show up half-submitted). Check `docker compose logs taskforge-redis`. |
| Jobs stuck in `pending` forever | Worker isn't consuming — `docker compose logs taskforge-worker`. Confirm it's subscribed to the right queues (`--queues=high,default,low`) and that `CELERY_BROKER_URL` matches what the API is publishing to. |
| Port already in use (8000/5432/6379/3000/9090/5555) | Something else on the host is bound to it. Stop that process, or edit the `ports:` mapping in `docker-compose.yml` — left side is the host port. |
| Grafana dashboard has no data | Nothing's been submitted yet — run `python scripts/seed_jobs.py`, or check Prometheus's Targets page to confirm it's actually scraping `taskforge-api`. |
| Metrics look wrong/stale after a restart, or `/metrics` errors | `PROMETHEUS_MULTIPROC_DIR` (`/prometheus_multiproc`) is a shared volume between the API and worker containers — if it's missing or not writable, multiprocess metric aggregation breaks. The API cleans stale `.db` files from it on startup; if you're running outside Docker, make sure the env var points at a directory that exists and is writable *before* the API and worker processes start. |
| `docker compose up` fails building the image | Usually a stale `pip` cache or a `requirements.txt` change — `docker compose build --no-cache taskforge-api`. |

