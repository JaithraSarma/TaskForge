"""FastAPI application factory and entrypoint."""

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import text

from app.api import dlq, router
from app.config import get_settings
from app.database import async_session_factory, init_db
from app.logging_config import configure_logging, request_id_var
from app.schemas import HealthResponse, ReadinessResponse

settings = get_settings()
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: configure logging and initialize the database.

    Logging is (re)configured here as well as at import time so it takes effect
    after Uvicorn has installed its own logging, letting Uvicorn's records flow
    through the structured handler.
    """
    import os

    configure_logging()

    # Ensure the shared multiprocess directory exists. We deliberately do NOT
    # wipe it here: the API and the workers write to the same volume, so a wipe
    # on API startup would delete the workers' live metric state. A fresh slate
    # comes from recreating the volume (docker compose down -v).
    mp_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if mp_dir:
        os.makedirs(mp_dir, exist_ok=True)

    await init_db()

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
# Wildcard origins cannot be combined with allow_credentials=True per the CORS spec
# (browsers reject it) — allow_credentials must be False when allow_origins is "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Attach a correlation id to every request, propagating it via X-Request-ID."""
    incoming_id = request.headers.get("X-Request-ID")
    request_id = incoming_id or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_var.reset(token)


# -- Prometheus instrumentation --
import os

if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
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
        excluded_handlers=["/health", "/health/ready", "/metrics"],
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
        excluded_handlers=["/health", "/health/ready", "/metrics"],
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


@app.get("/health/ready", response_model=ReadinessResponse, tags=["system"])
async def readiness_check() -> ReadinessResponse | JSONResponse:
    """Readiness probe — verifies PostgreSQL and Redis are actually reachable."""
    checks: dict[str, str] = {}

    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        logging.getLogger("app.main").error("Readiness check: database unavailable: %s", e)
        checks["database"] = "unavailable"

    try:
        client = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            socket_connect_timeout=2,
        )
        try:
            await client.ping()
            checks["redis"] = "ok"
        finally:
            await client.aclose()
    except Exception as e:
        logging.getLogger("app.main").error("Readiness check: redis unavailable: %s", e)
        checks["redis"] = "unavailable"

    ready = all(v == "ok" for v in checks.values())
    response = ReadinessResponse(
        status="ready" if ready else "not_ready",
        checks=checks,
        version=settings.app_version,
        service=settings.app_name,
    )
    if ready:
        return response
    return JSONResponse(status_code=503, content=response.model_dump())
