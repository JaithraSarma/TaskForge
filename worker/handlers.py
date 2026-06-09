import logging
import random
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


def handle_email_notification(payload: dict) -> dict:
    """Simulate sending an email notification.

    ~10% failure rate to demonstrate retry logic.
    """
    recipient = payload.get("to", "unknown@example.com")
    subject = payload.get("subject", "No subject")

    logger.info("Sending email to %s: %s", recipient, subject)
    time.sleep(random.uniform(0.5, 2.0))  # simulate network latency

    # 10% chance of transient failure
    if random.random() < 0.10:
        raise ConnectionError(f"SMTP connection failed for {recipient}")

    return {
        "delivered": True,
        "recipient": recipient,
        "subject": subject,
        "smtp_response": "250 OK",
    }


def handle_data_export(payload: dict) -> dict:
    """Simulate a data export/ETL job.

    Heavier processing time, ~5% failure rate.
    """
    export_format = payload.get("format", "csv")
    record_count = payload.get("records", random.randint(100, 10000))

    logger.info("Exporting %d records as %s", record_count, export_format)
    time.sleep(random.uniform(1.0, 4.0))  # heavier processing

    if random.random() < 0.05:
        raise RuntimeError(f"Export failed: disk full after {record_count // 2} records")

    return {
        "format": export_format,
        "records_exported": record_count,
        "file_size_mb": round(record_count * 0.001, 2),
        "download_url": f"https://storage.taskforge.io/exports/{random.randint(1000, 9999)}.{export_format}",
    }


def handle_image_resize(payload: dict) -> dict:
    """Simulate image resizing/processing.

    ~8% failure rate (corrupt images, unsupported formats).
    """
    image_url = payload.get("url", "https://example.com/image.jpg")
    target_width = payload.get("width", 800)
    target_height = payload.get("height", 600)

    logger.info("Resizing image %s to %dx%d", image_url, target_width, target_height)
    time.sleep(random.uniform(0.8, 3.0))

    if random.random() < 0.08:
        raise ValueError(f"Unsupported image format in {image_url}")

    return {
        "original_url": image_url,
        "resized_url": f"https://cdn.taskforge.io/resized/{random.randint(1000, 9999)}.webp",
        "dimensions": {"width": target_width, "height": target_height},
        "compression_ratio": round(random.uniform(0.3, 0.7), 2),
    }


def handle_webhook_delivery(payload: dict) -> dict:
    """Simulate delivering a webhook to an external URL.

    ~12% failure rate (timeouts, 5xx responses).
    """
    target_url = payload.get("url", "https://httpbin.org/post")
    event_type = payload.get("event", "job.completed")

    logger.info("Delivering webhook %s to %s", event_type, target_url)
    time.sleep(random.uniform(0.3, 1.5))

    if random.random() < 0.12:
        status_code = random.choice([500, 502, 503, 504])
        raise ConnectionError(f"Webhook delivery failed: {target_url} returned HTTP {status_code}")

    return {
        "url": target_url,
        "event": event_type,
        "status_code": 200,
        "response_time_ms": round(random.uniform(50, 500), 1),
    }


# ---------------------------------------------------------------------------
# Handler registry — maps job_type string to its handler function
# ---------------------------------------------------------------------------

HANDLER_REGISTRY: dict[str, Callable] = {
    "email_notification": handle_email_notification,
    "data_export": handle_data_export,
    "image_resize": handle_image_resize,
    "webhook_delivery": handle_webhook_delivery,
}


def get_handler(job_type: str) -> Callable:
    """Look up a handler by job type.

    Raises KeyError if the job type is not registered.
    """
    handler = HANDLER_REGISTRY.get(job_type)
    if handler is None:
        raise KeyError(
            f"Unknown job type '{job_type}'. Registered types: {list(HANDLER_REGISTRY.keys())}"
        )
    return handler
