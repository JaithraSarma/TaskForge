"""FastAPI application factory and entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import dlq, router
from app.config import get_settings
from app.database import init_db
from app.schemas import HealthResponse

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — initialize DB on startup and load current metrics."""
    import glob
    import os

    # Clean up old multiprocess metrics files on startup
    mp_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if mp_dir:
        os.makedirs(mp_dir, exist_ok=True)
        for f in glob.glob(os.path.join(mp_dir, "*.db")):
            with suppress(OSError):
                os.remove(f)

    await init_db()

    # Initialize Prometheus gauges from database state
    try:
        from sqlalchemy import func, select

        from app.api.router import _priority_to_queue
        from app.database import async_session_factory
        from app.metrics import DLQ_SIZE, QUEUE_DEPTH
        from app.models import Job, JobStatus

        async with async_session_factory() as session:
            # DLQ Size
            dlq_count = await session.scalar(
                select(func.count()).select_from(Job).where(Job.status == JobStatus.DEAD)
            )
            DLQ_SIZE.set(dlq_count or 0)

            # Queue Depth
            result = await session.execute(
                select(Job.priority, func.count())
                .where(Job.status == JobStatus.PENDING)
                .group_by(Job.priority)
            )
            queue_counts = {"high": 0, "default": 0, "low": 0}
            for priority, count in result.all():
                q = _priority_to_queue(priority)
                queue_counts[q] += count
            for q, count in queue_counts.items():
                QUEUE_DEPTH.labels(queue_name=q).set(count)
    except Exception as e:
        import logging

        logging.getLogger("app.main").error("Failed to initialize metrics from database: %s", e)

    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Production-grade async background job processing system with "
        "dead-letter queue, Prometheus metrics, and full observability."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# -- Middleware --
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Prometheus instrumentation --
import os

if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
    from fastapi import Response
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        generate_latest,
        multiprocess,
    )

    # Instrument the app but do NOT expose the default registry
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        """Endpoint that collects and exposes multiprocess metrics."""
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        data = generate_latest(registry)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)
else:
    # Default single-process instrumentation
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# -- Routers --
app.include_router(router.router)
app.include_router(dlq.router)


# -- Health check --
@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check() -> HealthResponse:
    """Liveness probe for container orchestration."""
    return HealthResponse(
        status="healthy",
        version=settings.app_version,
        service=settings.app_name,
    )
