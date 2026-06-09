"""Tests for Celery task execution, retry logic, and handler registry."""

from unittest.mock import MagicMock, patch

import pytest

from worker.handlers import (
    HANDLER_REGISTRY,
    get_handler,
    handle_data_export,
    handle_email_notification,
    handle_image_resize,
    handle_webhook_delivery,
)


class TestHandlerRegistry:
    """Tests for the job handler registry."""

    def test_all_handlers_registered(self) -> None:
        """All expected job types are in the registry."""
        expected_types = {"email_notification", "data_export", "image_resize", "webhook_delivery"}
        assert set(HANDLER_REGISTRY.keys()) == expected_types

    def test_get_handler_valid(self) -> None:
        """get_handler returns the correct handler for valid types."""
        handler = get_handler("email_notification")
        assert handler is handle_email_notification

    def test_get_handler_invalid(self) -> None:
        """get_handler raises KeyError for unknown job types."""
        with pytest.raises(KeyError, match="Unknown job type"):
            get_handler("nonexistent_handler")


class TestEmailHandler:
    """Tests for the email notification handler."""

    @patch("worker.handlers.random.random", return_value=0.5)  # no failure
    @patch("worker.handlers.time.sleep")  # skip sleep
    def test_email_success(self, mock_sleep: MagicMock, mock_random: MagicMock) -> None:
        """Email handler returns delivery confirmation."""
        result = handle_email_notification(
            {
                "to": "test@example.com",
                "subject": "Hello",
            }
        )
        assert result["delivered"] is True
        assert result["recipient"] == "test@example.com"
        assert result["smtp_response"] == "250 OK"

    @patch("worker.handlers.random.random", return_value=0.01)  # force failure
    @patch("worker.handlers.time.sleep")
    def test_email_transient_failure(self, mock_sleep: MagicMock, mock_random: MagicMock) -> None:
        """Email handler raises ConnectionError on transient failure."""
        with pytest.raises(ConnectionError, match="SMTP connection failed"):
            handle_email_notification({"to": "fail@example.com"})


class TestDataExportHandler:
    """Tests for the data export handler."""

    @patch("worker.handlers.random.random", return_value=0.5)
    @patch("worker.handlers.time.sleep")
    def test_export_success(self, mock_sleep: MagicMock, mock_random: MagicMock) -> None:
        """Data export returns file metadata."""
        result = handle_data_export({"format": "csv", "records": 1000})
        assert result["format"] == "csv"
        assert result["records_exported"] == 1000
        assert "download_url" in result


class TestImageResizeHandler:
    """Tests for the image resize handler."""

    @patch("worker.handlers.random.random", return_value=0.5)
    @patch("worker.handlers.time.sleep")
    def test_resize_success(self, mock_sleep: MagicMock, mock_random: MagicMock) -> None:
        """Image resize returns new dimensions and URL."""
        result = handle_image_resize(
            {
                "url": "https://example.com/img.jpg",
                "width": 800,
                "height": 600,
            }
        )
        assert result["dimensions"]["width"] == 800
        assert "resized_url" in result


class TestWebhookHandler:
    """Tests for the webhook delivery handler."""

    @patch("worker.handlers.random.random", return_value=0.5)
    @patch("worker.handlers.time.sleep")
    def test_webhook_success(self, mock_sleep: MagicMock, mock_random: MagicMock) -> None:
        """Webhook delivery returns success status."""
        result = handle_webhook_delivery(
            {
                "url": "https://httpbin.org/post",
                "event": "test.event",
            }
        )
        assert result["status_code"] == 200
        assert result["event"] == "test.event"

    @patch("worker.handlers.random.random", return_value=0.01)
    @patch("worker.handlers.time.sleep")
    def test_webhook_failure(self, mock_sleep: MagicMock, mock_random: MagicMock) -> None:
        """Webhook delivery raises on HTTP failure."""
        with pytest.raises(ConnectionError, match="Webhook delivery failed"):
            handle_webhook_delivery({"url": "https://fail.example.com"})


# ---------------------------------------------------------------------------
# Celery Task Tests (process_job)
# ---------------------------------------------------------------------------

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Job, JobStatus
from worker.tasks import process_job

test_sync_engine = create_engine("sqlite:///./test.db")
TestSyncSession = sessionmaker(bind=test_sync_engine)


class TestProcessJobTask:
    """Tests for the celery task process_job."""

    @patch("worker.tasks.redis_client")
    def test_process_job_success(self, mock_redis: MagicMock) -> None:
        """Job executes successfully and status is updated to SUCCEEDED."""
        session = TestSyncSession()
        job = Job(
            job_type="email_notification",
            payload={"to": "test@example.com", "subject": "Hello"},
            priority=5,
            max_retries=3,
            status=JobStatus.PENDING,
        )
        session.add(job)
        session.commit()
        job_uuid = job.id
        job_id = str(job_uuid)
        session.close()

        with patch("worker.tasks.get_handler") as mock_get_handler:
            mock_handler = MagicMock(return_value={"delivered": True})
            mock_get_handler.return_value = mock_handler

            result = process_job(job_id)

            assert result["status"] == "succeeded"
            mock_handler.assert_called_once_with({"to": "test@example.com", "subject": "Hello"})

        session = TestSyncSession()
        updated_job = session.query(Job).filter(Job.id == job_uuid).first()
        assert updated_job is not None
        assert updated_job.status == JobStatus.SUCCEEDED
        assert updated_job.result == {"delivered": True}
        assert updated_job.started_at is not None
        assert updated_job.completed_at is not None
        session.close()

    @patch("worker.tasks.redis_client")
    def test_process_job_retry(self, mock_redis: MagicMock) -> None:
        """Job fails, retry is scheduled, and retry_count is incremented."""
        session = TestSyncSession()
        job = Job(
            job_type="email_notification",
            payload={"to": "test@example.com"},
            priority=5,
            max_retries=3,
            status=JobStatus.PENDING,
            retry_count=0,
        )
        session.add(job)
        session.commit()
        job_uuid = job.id
        job_id = str(job_uuid)
        session.close()

        with (
            patch("worker.tasks.get_handler") as mock_get_handler,
            patch("worker.tasks.process_job.retry") as mock_retry,
        ):
            mock_handler = MagicMock(side_effect=ConnectionError("SMTP error"))
            mock_get_handler.return_value = mock_handler
            mock_retry.side_effect = Exception("CeleryRetry")

            with pytest.raises(Exception, match="CeleryRetry"):
                process_job(job_id)

        session = TestSyncSession()
        updated_job = session.query(Job).filter(Job.id == job_uuid).first()
        assert updated_job is not None
        assert updated_job.status == JobStatus.PENDING
        assert updated_job.retry_count == 1
        assert updated_job.error is not None
        assert "ConnectionError: SMTP error" in updated_job.error
        session.close()

    @patch("worker.tasks.redis_client")
    def test_process_job_dlq(self, mock_redis: MagicMock) -> None:
        """Job fails past max_retries and is routed to the DLQ (status DEAD)."""
        session = TestSyncSession()
        job = Job(
            job_type="email_notification",
            payload={"to": "test@example.com"},
            priority=5,
            max_retries=3,
            status=JobStatus.PENDING,
            retry_count=3,
        )
        session.add(job)
        session.commit()
        job_uuid = job.id
        job_id = str(job_uuid)
        session.close()

        with patch("worker.tasks.get_handler") as mock_get_handler:
            mock_handler = MagicMock(side_effect=ConnectionError("SMTP error"))
            mock_get_handler.return_value = mock_handler

            result = process_job(job_id)
            assert result["status"] == "dead"
            assert "SMTP error" in result["error"]

        session = TestSyncSession()
        updated_job = session.query(Job).filter(Job.id == job_uuid).first()
        assert updated_job is not None
        assert updated_job.status == JobStatus.DEAD
        assert updated_job.retry_count == 4
        assert updated_job.error is not None
        assert "ConnectionError" in updated_job.error
        session.close()

        mock_redis.lpush.assert_called_once()
