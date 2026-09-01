"""Regression tests for app.database.init_db concurrency safety."""

import pytest
from sqlalchemy import inspect

from app.database import engine, init_db


@pytest.mark.asyncio
async def test_init_db_is_idempotent() -> None:
    """init_db() must not raise when called twice in a row.

    This guards against the PostgreSQL advisory-lock regression where two
    Uvicorn workers race on creating the job_status enum type. SQLite has no
    advisory locks, so this exercises the dialect guard and create_all's own
    checkfirst behavior.
    """
    await init_db()
    await init_db()

    async with engine.connect() as conn:
        table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

    assert "jobs" in table_names
