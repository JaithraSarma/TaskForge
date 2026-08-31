"""Tests for structured JSON logging and request correlation ids."""

import json
import logging

from fastapi.testclient import TestClient

from app.logging_config import JsonFormatter, request_id_var


class TestJsonFormatter:
    """Tests for JsonFormatter."""

    def test_produces_valid_json_with_expected_keys(self) -> None:
        """A formatted record is valid JSON containing the required keys."""
        token = request_id_var.set("req-123")
        try:
            record = logging.LogRecord(
                name="app.test",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg="hello %s",
                args=("world",),
                exc_info=None,
            )
            formatted = JsonFormatter().format(record)
            data = json.loads(formatted)

            assert data["timestamp"]
            assert data["level"] == "INFO"
            assert data["logger"] == "app.test"
            assert data["message"] == "hello world"
            assert data["request_id"] == "req-123"
        finally:
            request_id_var.reset(token)

    def test_includes_exc_info_when_exception_logged(self) -> None:
        """exc_info is included in the JSON payload when logging an exception."""
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="app.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )
            formatted = JsonFormatter().format(record)
            data = json.loads(formatted)

            assert "exc_info" in data
            assert "ValueError: boom" in data["exc_info"]


class TestRequestIdMiddleware:
    """Tests for the FastAPI request-id middleware."""

    def test_health_response_has_request_id_header(self, client: TestClient) -> None:
        """A plain GET to /health returns a generated X-Request-ID header."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID")

    def test_incoming_request_id_is_echoed(self, client: TestClient) -> None:
        """When the client sends X-Request-ID, the response echoes the same value."""
        response = client.get("/health", headers={"X-Request-ID": "abc123"})
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID") == "abc123"
