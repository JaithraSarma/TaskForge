# =============================================================================
# TaskForge — Multi-stage Dockerfile
# Single image used for both the API server and Celery workers (different CMD)
# =============================================================================

# -- Stage 1: Base with dependencies --
FROM python:3.10-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies (psycopg2 needs libpq)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
        curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# -- Stage 2: Application --
FROM base AS app

# Create non-root user
RUN groupadd -r taskforge && \
    useradd -r -g taskforge -d /app -s /sbin/nologin taskforge

WORKDIR /app

# Copy application code
COPY app/ ./app/
COPY worker/ ./worker/
COPY migrations/ ./migrations/
COPY alembic.ini .
COPY pyproject.toml .

# Change ownership
RUN chown -R taskforge:taskforge /app

USER taskforge

# Health check for the API service
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

# Default CMD runs the API — override for workers
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
