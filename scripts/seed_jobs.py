#!/usr/bin/env python3
"""
Seed script — submit one job of each registered type.

Run this on first boot so the Grafana dashboard has live data immediately
without needing the full 5k-job load test.

Usage:
    python scripts/seed_jobs.py
    python scripts/seed_jobs.py --base-url http://localhost:8000
"""

import argparse
import sys
import time

import httpx

# One sample payload per job type
SEED_JOBS = [
    {
        "job_type": "email_notification",
        "payload": {
            "to": "demo@taskforge.io",
            "subject": "Welcome to TaskForge",
            "body": "Your async job processing system is live!",
        },
        "priority": 2,  # high
        "max_retries": 3,
    },
    {
        "job_type": "data_export",
        "payload": {
            "format": "csv",
            "records": 500,
            "dataset": "sample_users",
        },
        "priority": 5,  # default
        "max_retries": 3,
    },
    {
        "job_type": "image_resize",
        "payload": {
            "url": "https://example.com/hero-banner.jpg",
            "width": 1200,
            "height": 630,
        },
        "priority": 5,  # default
        "max_retries": 3,
    },
    {
        "job_type": "webhook_delivery",
        "payload": {
            "url": "https://httpbin.org/post",
            "event": "job.seed_test",
            "data": {"test": True},
        },
        "priority": 8,  # low
        "max_retries": 3,
    },
]


def submit_jobs(base_url: str) -> list[dict]:
    """Submit seed jobs and return the responses."""
    submitted = []
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        for job_data in SEED_JOBS:
            try:
                resp = client.post("/api/v1/jobs", json=job_data)
                resp.raise_for_status()
                result = resp.json()
                submitted.append(result)
                print(f"  ✅ {job_data['job_type']:25s} → {result['id']}")
            except httpx.HTTPError as e:
                print(f"  ❌ {job_data['job_type']:25s} → FAILED: {e}")
    return submitted


def poll_status(base_url: str, job_ids: list[str], timeout: int = 60) -> None:
    """Poll for job completion status."""
    pending = set(job_ids)
    start = time.time()

    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        while pending and (time.time() - start) < timeout:
            time.sleep(2)
            for job_id in list(pending):
                try:
                    resp = client.get(f"/api/v1/jobs/{job_id}")
                    if resp.status_code == 200:
                        data = resp.json()
                        status = data["status"]
                        if status in ("succeeded", "failed", "dead"):
                            emoji = "✅" if status == "succeeded" else "💀"
                            print(f"  {emoji} {data['job_type']:25s} → {status}")
                            pending.discard(job_id)
                except httpx.HTTPError:
                    pass

    if pending:
        print(f"\n  ⚠️  {len(pending)} jobs still processing after {timeout}s timeout")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed TaskForge with one job per type")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="TaskForge API base URL (default: http://localhost:8000)",
    )
    args = parser.parse_args()

    print(f"\n🌱 Seeding TaskForge at {args.base_url}\n")
    print("Submitting jobs:")
    submitted = submit_jobs(args.base_url)

    if not submitted:
        print("\n❌ No jobs submitted. Is the API running?")
        sys.exit(1)

    job_ids = [j["id"] for j in submitted]
    print(f"\n⏳ Waiting for {len(job_ids)} jobs to complete...\n")
    poll_status(args.base_url, job_ids)

    print("\n🎉 Seed complete! Check Grafana at http://localhost:3000\n")


if __name__ == "__main__":
    main()
