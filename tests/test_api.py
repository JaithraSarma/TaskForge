"""Tests for the FastAPI job submission and management endpoints."""

import uuid

from fastapi.testclient import TestClient


class TestJobSubmission:
    """Tests for POST /api/v1/jobs."""

    def test_submit_job_success(self, client: TestClient) -> None:
        """Submitting a valid job returns 202 with job ID."""
        response = client.post(
            "/api/v1/jobs",
            json={
                "job_type": "email_notification",
                "payload": {"to": "test@example.com"},
                "priority": 5,
                "max_retries": 3,
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert "id" in data
        assert data["status"] == "pending"
        assert data["message"] == "Job submitted successfully"

    def test_submit_job_default_values(self, client: TestClient) -> None:
        """Job submission uses default priority and max_retries."""
        response = client.post(
            "/api/v1/jobs",
            json={"job_type": "data_export", "payload": {}},
        )
        assert response.status_code == 202

    def test_submit_job_invalid_type_empty(self, client: TestClient) -> None:
        """Empty job_type is rejected."""
        response = client.post(
            "/api/v1/jobs",
            json={"job_type": "", "payload": {}},
        )
        assert response.status_code == 422

    def test_submit_job_invalid_priority(self, client: TestClient) -> None:
        """Priority outside 1-10 is rejected."""
        response = client.post(
            "/api/v1/jobs",
            json={"job_type": "test", "payload": {}, "priority": 15},
        )
        assert response.status_code == 422

    def test_submit_job_high_priority_queue(self, client: TestClient) -> None:
        """Priority 1-3 routes to 'high' queue."""
        response = client.post(
            "/api/v1/jobs",
            json={"job_type": "email_notification", "payload": {}, "priority": 1},
        )
        assert response.status_code == 202


class TestJobRetrieval:
    """Tests for GET /api/v1/jobs and GET /api/v1/jobs/{id}."""

    def test_get_job_by_id(self, client: TestClient) -> None:
        """Retrieve a submitted job by its ID."""
        # Submit first
        submit_resp = client.post(
            "/api/v1/jobs",
            json={"job_type": "data_export", "payload": {"format": "csv"}},
        )
        job_id = submit_resp.json()["id"]

        # Retrieve
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id
        assert data["job_type"] == "data_export"
        assert data["status"] == "pending"

    def test_get_job_not_found(self, client: TestClient) -> None:
        """Requesting a non-existent job returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/api/v1/jobs/{fake_id}")
        assert response.status_code == 404

    def test_list_jobs_empty(self, client: TestClient) -> None:
        """Empty database returns empty list."""
        response = client.get("/api/v1/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["jobs"] == []
        assert data["total"] == 0

    def test_list_jobs_with_data(self, client: TestClient) -> None:
        """List shows submitted jobs."""
        # Submit 3 jobs
        for jt in ["email_notification", "data_export", "image_resize"]:
            client.post("/api/v1/jobs", json={"job_type": jt, "payload": {}})

        response = client.get("/api/v1/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["jobs"]) == 3

    def test_list_jobs_pagination(self, client: TestClient) -> None:
        """Pagination returns correct page size."""
        for i in range(5):
            client.post("/api/v1/jobs", json={"job_type": f"test_{i}", "payload": {}})

        response = client.get("/api/v1/jobs?page=1&page_size=2")
        data = response.json()
        assert len(data["jobs"]) == 2
        assert data["total"] == 5
        assert data["total_pages"] == 3

    def test_list_jobs_filter_by_status(self, client: TestClient) -> None:
        """Filtering by status works."""
        client.post("/api/v1/jobs", json={"job_type": "test", "payload": {}})
        response = client.get("/api/v1/jobs?status=pending")
        assert response.status_code == 200
        data = response.json()
        assert all(j["status"] == "pending" for j in data["jobs"])

    def test_list_jobs_invalid_status(self, client: TestClient) -> None:
        """Invalid status filter returns 400."""
        response = client.get("/api/v1/jobs?status=invalid")
        assert response.status_code == 400


class TestJobCancellation:
    """Tests for DELETE /api/v1/jobs/{id}."""

    def test_cancel_pending_job(self, client: TestClient) -> None:
        """Cancelling a pending job returns 204."""
        submit_resp = client.post(
            "/api/v1/jobs",
            json={"job_type": "test", "payload": {}},
        )
        job_id = submit_resp.json()["id"]

        response = client.delete(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/api/v1/jobs/{job_id}")
        assert get_resp.status_code == 404

    def test_cancel_nonexistent_job(self, client: TestClient) -> None:
        """Cancelling a non-existent job returns 404."""
        fake_id = str(uuid.uuid4())
        response = client.delete(f"/api/v1/jobs/{fake_id}")
        assert response.status_code == 404


class TestHealthCheck:
    """Tests for GET /health."""

    def test_health_check(self, client: TestClient) -> None:
        """Health endpoint returns 200 with service info."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "TaskForge"
