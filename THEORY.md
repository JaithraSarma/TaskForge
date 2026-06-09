# TaskForge — Theoretical Guide to Asynchronous Job Processing

This document outlines the architectural patterns, design decisions, and system engineering principles behind **TaskForge**. It serves as a comprehensive theoretical reference for background processing, reliability engineering, and system observability.

---

## 1. Why Asynchronous Job Processing?

In modern web applications, the primary thread of the web API must remain non-blocking to maximize throughput and deliver low-latency responses to clients. Long-running or resource-intensive tasks, if executed synchronously, lead to:
- **Thread Starvation**: The server depletes its worker thread pool, causing requests to queue up and leading to high API latencies or `504 Gateway Timeout` errors.
- **Cascading Failures**: If downstream systems (e.g., SMTP servers, external webhooks) are slow or offline, the API latency increases proportionally, propagating instability upstream.
- **Poor User Experience**: Users wait indefinitely for pages to load while the server does background processing.

### The Decoupled Architecture

By adopting an asynchronous background job model, TaskForge separates the **Ingestion Layer** from the **Execution Layer**:

```
+---------------+              +---------------+              +---------------+
|  FastAPI API  | --Enqueue--> |  Redis Queue  | --Dequeue--> | Celery Worker |
|  (Ingestion)  |              |   (Broker)    |              |  (Execution)  |
+---------------+              +---------------+              +---------------+
```

1. **Low Ingestion Latency**: The API performs light validation, writes a record to PostgreSQL (status: `pending`), enqueues a light message in Redis, and immediately returns a `202 Accepted` response with a tracking ID.
2. **Horizontal Scalability**: The number of API instances can scale independently of the number of execution workers, aligning resources with load.
3. **Fault Isolation**: If the worker pool crashes under memory pressure, the API remains fully functional, continuing to queue up tasks in Redis.

---

## 2. Message Broker Patterns: Pub/Sub vs. Work Queues

TaskForge utilizes **Redis** as a Message Broker. In message-oriented middleware, two core patterns dominate:

| Pattern | Mechanism | Use Case in TaskForge |
|---|---|---|
| **Publish/Subscribe** | A message is broadcast to all active subscribers (one-to-many). | Event notifications, log auditing. Not used for direct task dispatching. |
| **Work Queue (Point-to-Point)** | A message is consumed by exactly one worker (one-to-one). | Core task dispatching. Redis lists (`LPUSH` / `RPOPLPUSH`) act as point-to-point work queues. |

### Why Redis?
For background job systems, the broker must balance speed and durability:
- **RabbitMQ**: Advanced routing (AMQP), robust acknowledgments, but higher operational complexity.
- **Redis**: Extremely fast (in-memory), low latency, and supports durable queuing via Redis streams or persistence features (AOF/RDB). TaskForge uses Redis due to its simplicity, performance, and standard usage in the Celery ecosystem.

---

## 3. Celery Internals & Production Reliability

Celery is a highly customizable task queue library. To make TaskForge production-grade, we configure specific reliability parameters:

### 3.1. Late Acknowledgment (`task_acks_late=True`)
- **Default Behavior**: Celery acknowledges the task (deletes the message from Redis) *before* executing it. If the worker process is killed mid-task (e.g., via OOM killer or host crash), the task is lost forever.
- **TaskForge Behavior**: We set `task_acks_late=True`. The message is acknowledged only *after* the task completes execution. If the worker crashes, the broker detects the disconnection and makes the message visible to other workers for re-delivery.

### 3.2. Worker Lost Rejection (`task_reject_on_worker_lost=True`)
When a worker process crashes abruptly (e.g., due to a segmentation fault or `SIGKILL`), Celery will automatically reject the task, causing the broker to re-queue it. This prevents tasks from hanging indefinitely in the pre-fetched state.

### 3.3. Prefetch Multiplier (`worker_prefetch_multiplier=1`)
- **Prefetching**: Workers fetch multiple messages from the broker into local memory to minimize network roundtrips.
- **The Danger**: If one worker prefetches 20 long-running jobs (e.g., image resizing) while other workers are idle, it creates a processing bottleneck (head-of-line blocking).
- **TaskForge Behavior**: Set to `1`. Each worker process fetches exactly one task at a time, ensuring fair distribution across workers.

---

## 4. Resiliency & Advanced Retry Strategies

Background processing systems must assume that downstream network calls (databases, SMTP servers, APIs) will fail. TaskForge implements **exponential backoff with jitter** to handle transient failures gracefully.

### 4.1. Exponential Backoff
Instead of retrying immediately, the delay increases exponentially with each retry attempt:

$$\text{delay} = 2^{\text{retry\_count}} \times \text{base\_delay}$$

This prevents overwhelming a failing downstream service (often referred to as the **Thundering Herd Problem**).

### 4.2. Adding Jitter
If 100 tasks fail at the exact same moment, exponential backoff alone will cause all 100 tasks to retry at the exact same next interval. We add **random jitter**:

$$\text{delay} = (2^{\text{retry\_count}} \times \text{base\_delay}) + \text{random\_uniform}(0, 1)$$

Jitter spreads out the retry attempts over a window, flattening the load spike.

```
Without Jitter:   | |                     | |                     | |
With Jitter:      | . . | . . | . . . | . . . . | . . . . . | . . . . . . . |
```

### 4.3. Circuit Breaker Pattern (Concept)
If a downstream service is down completely, workers should fail fast rather than trying and waiting for timeouts. While not implemented directly in this version's handlers, a production handler wraps outbound calls in a Circuit Breaker that transitions to an `OPEN` state after $N$ failures, immediately rejecting tasks until the service recovers.

---

## 5. Dead-Letter Queues (DLQ)

When a task fails repeatedly and exceeds `max_retries`, it transitions from a **transient failure** to a **poison pill** (permanent failure). 

### 5.1. Purpose of DLQ
Leaving poison pills in the main work queue wastes resources and can block valid tasks. Pushing them to a **Dead-Letter Queue (DLQ)**:
- Isolates broken tasks from healthy traffic.
- Preserves the task state (payload, error traceback, metadata) for inspection.
- Prevents database bloating.

### 5.2. Inspection & Replay
In TaskForge, the DLQ is stored in a separate Redis database (`db=2`) under the key `taskforge-dlq` and tracked in PostgreSQL with `status="dead"`.
- **Inspection**: Administrators inspect the failure reasons using the API endpoint `/api/v1/dlq`.
- **Replay (Retry)**: Once the underlying bug or configuration error is fixed, the administrator invokes the `/api/v1/dlq/{id}/retry` endpoint to push the job back into the active routing queue.

---

## 6. Observability: RED vs. USE Methods

Observability is the cornerstone of production readiness. TaskForge exposes Prometheus metrics structured around the **RED Method** (for requests) and **USE Method** (for infrastructure):

### 6.1. RED Method (API & Jobs)
- **Rate**: Number of jobs submitted per second (`taskforge_jobs_submitted_total`).
- **Errors**: Number of jobs entering the terminal `failed`/`dead` state (`taskforge_jobs_completed_total{status="dead"}`).
- **Duration**: Quantiles (p50, p95, p99) of job execution times (`taskforge_job_duration_seconds`).

### 6.2. USE Method (Worker Health & Queues)
- **Utilization**: Percentage of active worker threads (`taskforge_active_workers`).
- **Saturation**: Queue depths indicating system lag (`taskforge_queue_depth`).
- **Errors**: Downstream worker exceptions and connection timeouts.

---

## 7. Database Design & Indexing

The `jobs` table in PostgreSQL is the source of truth for job states. Since it is highly transactional, write and read queries must be optimized.

### 7.1. Use of JSONB
- **payload**: Stored as `JSONB`, allowing arbitrary payload structures (emails, webhook URLs, export metadata) without altering schemas.
- **result**: Stored as `JSONB` to capture output metadata (file sizes, status codes) dynamically.

### 7.2. Composite Indexing
For efficient admin dashboard loading, we apply index optimizations:
1. `ix_jobs_created_at`: For paginated queries sorting by ingestion time.
2. `ix_jobs_status_type`: A composite index on `(status, job_type)`. This is highly beneficial because the admin dashboard frequently queries jobs filtered by status (e.g., showing only `dead` jobs in the DLQ list).

---

## 8. Scaling Strategies

As TaskForge handles larger volumes, the bottlenecks shift. Here is how to scale the system:

### 8.1. Queue Sharding & Priority Routing
TaskForge splits jobs into three queues: `high`, `default`, and `low`.
- **Worker Allocation**: Dedicated worker processes are assigned to specific queues (e.g., 8 workers for `high`, 2 for `low`). This prevents low-priority, long-running export tasks from starving time-sensitive email notifications.
- **Starvation Avoidance**: Workers subscribing to multiple queues prioritize high-priority tasks using Celery routing configurations.

### 8.2. Horizontal Autoscaling (HPA)
In Kubernetes, workers are autoscaled horizontally using **KEDA (Kubernetes Event-driven Autoscaling)** based on the queue depth in Redis:

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: celery-worker-scaler
spec:
  scaleTargetRef:
    name: taskforge-worker
  minReplicaCount: 1
  maxReplicaCount: 20
  triggers:
  - type: redis
    metadata:
      address: taskforge-redis:6379
      listName: default
      listLength: "50" # scale up when list length exceeds 50
```

---

## 9. Alternative Technologies

| Technology | Pros | Cons | Best Fit |
|---|---|---|---|
| **Celery** (Python) | Rich features, priority queues, massive ecosystem, built-in retry. | High complexity, requires AMQP/Redis broker. | Complex workflows, multi-priority jobs, Python stacks. |
| **RQ** (Python) | Lightweight, built entirely on Redis, very simple. | No native subtasks, lacks advanced routing, no non-Redis brokers. | Simple, single-priority Python tasks. |
| **Dramatiq** (Python) | Simpler thread model, lower memory footprint, retry-first. | Smaller ecosystem than Celery, no built-in canvas/workflows. | Modern Python stacks prioritizing simplicity. |
| **AWS SQS + Lambda** | Fully serverless, zero maintenance, highly resilient. | Vendor lock-in, latency overhead, timeout limits (15 mins). | Cloud-native AWS environments. |
