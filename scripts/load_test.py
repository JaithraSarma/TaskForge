#!/usr/bin/env python3
"""
Load test — submit 5,000 concurrent jobs to validate throughput.

Uses asyncio + httpx for high-concurrency HTTP requests.
Reports submission throughput, completion stats, and latency percentiles.

Usage:
    python scripts/load_test.py
    python scripts/load_test.py --jobs 10000 --concurrency 200
    python scripts/load_test.py --base-url http://localhost:8000
"""

import argparse
import asyncio
import random
import statistics
import time
from datetime import datetime

import httpx

# Job type definitions with sample payloads
JOB_TEMPLATES = [
    {
        "job_type": "email_notification",
        "payload": lambda i: {
            "to": f"user{i}@loadtest.io",
            "subject": f"Load test email #{i}",
        },
    },
    {
        "job_type": "data_export",
        "payload": lambda i: {
            "format": random.choice(["csv", "json", "parquet"]),
            "records": random.randint(100, 5000),
        },
    },
    {
        "job_type": "image_resize",
        "payload": lambda i: {
            "url": f"https://example.com/img_{i}.jpg",
            "width": random.choice([800, 1200, 1920]),
            "height": random.choice([600, 800, 1080]),
        },
    },
    {
        "job_type": "webhook_delivery",
        "payload": lambda i: {
            "url": "https://httpbin.org/post",
            "event": "load_test.job",
            "sequence": i,
        },
    },
]


async def submit_single_job(
    client: httpx.AsyncClient,
    job_index: int,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    """Submit a single job, respecting the concurrency semaphore."""
    template = random.choice(JOB_TEMPLATES)
    job_data = {
        "job_type": template["job_type"],
        "payload": template["payload"](job_index),
        "priority": random.randint(1, 10),
        "max_retries": 3,
    }

    async with semaphore:
        try:
            start = time.monotonic()
            resp = await client.post("/api/v1/jobs", json=job_data)
            elapsed = time.monotonic() - start
            if resp.status_code == 202:
                result = resp.json()
                return {
                    "id": result["id"],
                    "job_type": template["job_type"],
                    "submit_time": elapsed,
                    "status": "submitted",
                }
            else:
                return {"status": "error", "code": resp.status_code, "submit_time": 0}
        except Exception as e:
            return {"status": "error", "error": str(e), "submit_time": 0}


async def submit_all_jobs(
    base_url: str,
    total_jobs: int,
    concurrency: int,
) -> list[dict]:
    """Submit all jobs with controlled concurrency."""
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        tasks = [submit_single_job(client, i, semaphore) for i in range(total_jobs)]
        results = await asyncio.gather(*tasks)

    return [r for r in results if r is not None]


async def poll_completion(
    base_url: str,
    job_ids: list[str],
    poll_interval: float = 5.0,
    timeout: float = 300.0,
) -> dict:
    """Poll for job completion and return final stats."""
    pending = set(job_ids)
    completed = {"succeeded": 0, "failed": 0, "dead": 0}
    start = time.monotonic()

    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        while pending and (time.monotonic() - start) < timeout:
            await asyncio.sleep(poll_interval)

            # Sample a batch of pending jobs to check
            sample_size = min(100, len(pending))
            sample = random.sample(list(pending), sample_size)

            for job_id in sample:
                try:
                    resp = await client.get(f"/api/v1/jobs/{job_id}")
                    if resp.status_code == 200:
                        data = resp.json()
                        status = data["status"]
                        if status in ("succeeded", "failed", "dead"):
                            completed[status] = completed.get(status, 0) + 1
                            pending.discard(job_id)
                except httpx.HTTPError:
                    pass

            elapsed = time.monotonic() - start
            done = sum(completed.values())
            total = done + len(pending)
            pct = (done / total * 100) if total > 0 else 0
            print(f"  ⏳ {done}/{total} completed ({pct:.1f}%) — {elapsed:.0f}s elapsed")

    completed["pending"] = len(pending)
    completed["total_time"] = time.monotonic() - start
    return completed


def print_report(
    submit_results: list[dict],
    completion_stats: dict,
    total_jobs: int,
    wall_time: float,
) -> None:
    """Print a formatted load test report."""
    submitted = [r for r in submit_results if r.get("status") == "submitted"]
    errors = [r for r in submit_results if r.get("status") == "error"]
    submit_times = [r["submit_time"] for r in submitted if r.get("submit_time")]

    print("\n" + "=" * 60)
    print("  📊 TASKFORGE LOAD TEST REPORT")
    print("=" * 60)

    print(f"\n  Total jobs requested:     {total_jobs:,}")
    print(f"  Successfully submitted:   {len(submitted):,}")
    print(f"  Submission errors:        {len(errors):,}")
    print(f"  Total wall time:          {wall_time:.1f}s")
    print(f"  Submission throughput:     {len(submitted) / wall_time:.0f} jobs/sec")

    if submit_times:
        print("\n  📈 Submission Latency:")
        print(f"     p50:  {statistics.median(submit_times) * 1000:.1f}ms")
        print(f"     p95:  {sorted(submit_times)[int(len(submit_times) * 0.95)] * 1000:.1f}ms")
        print(f"     p99:  {sorted(submit_times)[int(len(submit_times) * 0.99)] * 1000:.1f}ms")
        print(f"     max:  {max(submit_times) * 1000:.1f}ms")

    print("\n  📋 Completion Stats:")
    for status, count in completion_stats.items():
        if status != "total_time" and status != "pending":
            print(f"     {status:12s}: {count:,}")
    if completion_stats.get("pending", 0) > 0:
        print(f"     {'pending':12s}: {completion_stats['pending']:,} (still processing)")

    total_completed = completion_stats.get("succeeded", 0)
    total_failed = completion_stats.get("failed", 0) + completion_stats.get("dead", 0)
    if total_completed + total_failed > 0:
        success_rate = total_completed / (total_completed + total_failed) * 100
        print(f"\n  ✅ Success Rate: {success_rate:.1f}%")

    print("\n" + "=" * 60 + "\n")


async def run_load_test(base_url: str, total_jobs: int, concurrency: int) -> None:
    """Execute the full load test."""
    print("\n🚀 TaskForge Load Test")
    print(f"   Target:      {base_url}")
    print(f"   Jobs:        {total_jobs:,}")
    print(f"   Concurrency: {concurrency}")
    print(f"   Started:     {datetime.now().isoformat()}")

    # Phase 1: Submit all jobs
    print(f"\n📤 Phase 1: Submitting {total_jobs:,} jobs...")
    submit_start = time.monotonic()
    submit_results = await submit_all_jobs(base_url, total_jobs, concurrency)
    submit_wall = time.monotonic() - submit_start

    submitted = [r for r in submit_results if r.get("status") == "submitted"]
    print(
        f"   ✅ {len(submitted):,} submitted in {submit_wall:.1f}s ({len(submitted) / submit_wall:.0f} jobs/sec)"
    )

    # Phase 2: Poll for completion
    job_ids = [r["id"] for r in submitted]
    if job_ids:
        print("\n📥 Phase 2: Polling for completion...")
        completion_stats = await poll_completion(base_url, job_ids)
    else:
        completion_stats = {}

    # Report
    print_report(submit_results, completion_stats, total_jobs, submit_wall)


def main() -> None:
    parser = argparse.ArgumentParser(description="TaskForge load test")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--jobs", type=int, default=5000, help="Total jobs to submit")
    parser.add_argument("--concurrency", type=int, default=100, help="Max concurrent requests")
    args = parser.parse_args()

    asyncio.run(run_load_test(args.base_url, args.jobs, args.concurrency))


if __name__ == "__main__":
    main()
