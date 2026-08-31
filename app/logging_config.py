"""Structured JSON logging with request/job correlation IDs.

Provides a contextvar-backed request id that is threaded through both the
FastAPI request lifecycle and Celery task execution, and a JSON formatter
that emits one log record per line for easy ingestion by log aggregators.
"""

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

from app.config import get_settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_HANDLER_TAG = "taskforge_configured_handler"


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """Return a JSON string representation of the log record.

        Never raises: if a field cannot be serialized, it falls back to str().
        """
        try:
            payload = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "request_id": request_id_var.get(),
            }
            if record.exc_info:
                payload["exc_info"] = self.formatException(record.exc_info)
            return json.dumps(payload, default=str)
        except Exception:
            return json.dumps(
                {
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    "level": "ERROR",
                    "logger": "app.logging_config",
                    "message": "Failed to format log record",
                    "request_id": "-",
                }
            )


class _RequestIdFilter(logging.Filter):
    """Attaches the current correlation id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Set record.request_id from the contextvar and always allow the record through."""
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Configure the root logger for structured logging.

    Idempotent: safe to call multiple times (e.g. once per API/worker
    process init) without accumulating duplicate handlers.
    """
    settings = get_settings()
    resolved_level = level or settings.log_level
    resolved_fmt = fmt or settings.log_format

    root = logging.getLogger()

    # Remove any handler previously attached by this function to avoid
    # duplicate log lines on repeated calls.
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    setattr(handler, _HANDLER_TAG, True)
    handler.addFilter(_RequestIdFilter())

    if resolved_fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s")
        )

    root.addHandler(handler)
    root.setLevel(resolved_level)

    # Uvicorn installs its own handlers on these loggers, which would bypass the
    # structured handler above. Clear them and let the records propagate to the
    # root logger so API output is JSON-formatted like the rest of the system.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        third_party = logging.getLogger(name)
        third_party.handlers = []
        third_party.propagate = True
