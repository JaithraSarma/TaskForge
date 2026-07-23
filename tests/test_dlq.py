"""Tests for the dead-letter-queue API endpoints."""

import uuid
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models import Job, JobStatus
from tests.conftest import test_session_factory as _test_session_factory


async def _create_dead_job(
    job_type: str = "email_notification",
    payload: dict | None = None,
    error: str = "ConnectionError: SMTP connection failed",
    retry_count: int = 4,
    max_retries: int = 3,
) -> Job:
    """Insert a dead Job row via the test async session factory."""
    job = Job(
        job_type=job_type,
        payload=payload if payload is not None else {"to": "test@example.com"},
        priority=5,
        max_retries=max_retries,
        status=JobStatus.DEAD,
        retry_count=retry_count,
        error=error,
    )
    async with _test_session_factory() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)
    return job


@pytest.fixture(autouse=True)
def mock_dlq_redis() -> Generator[MagicMock, None, None]:
    """Mock the module-level Redis client used by the DLQ router."""
    with patch("app.api.dlq.redis_client") as mock_redis:
        mock_redis.lrange.return_value = []
        mock_redis.lrem.return_value = None
        yield mock_redis


class TestDLQList:
    """Tests for GET /api/v1/dlq."""

    async def test_list_empty(self, client: TestClient) -> None:
        """Empty DLQ returns an empty list."""
        response = client.get("/api/v1/dlq")
        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == []
        assert data["total"] == 0

    async def test_list_with_dead_jobs(self, client: TestClient) -> None:
        """List reflects dead jobs in the database."""
        await _create_dead_job()
        await _create_dead_job(job_type="data_export")

        response = client.get("/api/v1/dlq")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["entries"]) == 2


class TestDLQInspect:
    """Tests for GET /api/v1/dlq/{id}."""

    async def test_inspect_existing(self, client: TestClient) -> None:
        """Inspecting an existing dead job returns its payload and error."""
        job = await _create_dead_job()

        response = client.get(f"/api/v1/dlq/{job.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(job.id)
        assert data["error"] == job.error
        assert data["payload"] == job.payload

    async def test_inspect_missing(self, client: TestClient) -> None:
        """Inspecting a non-existent DLQ entry returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/api/v1/dlq/{fake_id}")
        assert response.status_code == 404


class TestDLQRetry:
    """Tests for POST /api/v1/dlq/{id}/retry."""

    async def test_retry_existing(self, client: TestClient) -> None:
        """Retrying a dead job resets it to pending and returns 202."""
        job = await _create_dead_job()

        response = client.post(f"/api/v1/dlq/{job.id}/retry")
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "pending"

        job_resp = client.get(f"/api/v1/jobs/{job.id}")
        assert job_resp.status_code == 200
        job_data = job_resp.json()
        assert job_data["status"] == "pending"
        assert job_data["retry_count"] == 0

    async def test_retry_missing(self, client: TestClient) -> None:
        """Retrying a non-existent DLQ entry returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.post(f"/api/v1/dlq/{fake_id}/retry")
        assert response.status_code == 404


class TestDLQPurge:
    """Tests for DELETE /api/v1/dlq/{id}."""

    async def test_purge_existing(self, client: TestClient) -> None:
        """Purging a dead job removes it, and it's no longer inspectable."""
        job = await _create_dead_job()

        response = client.delete(f"/api/v1/dlq/{job.id}")
        assert response.status_code == 204

        inspect_resp = client.get(f"/api/v1/dlq/{job.id}")
        assert inspect_resp.status_code == 404

    async def test_purge_missing(self, client: TestClient) -> None:
        """Purging a non-existent DLQ entry returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.delete(f"/api/v1/dlq/{fake_id}")
        assert response.status_code == 404
