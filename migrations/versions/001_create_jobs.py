"""001 — Create jobs table.

Revision ID: 001_create_jobs
Create Date: 2026-06-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001_create_jobs"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Enum type for job status
job_status_enum = postgresql.ENUM(
    "pending", "running", "succeeded", "failed", "dead",
    name="job_status",
    create_type=False,
)


def upgrade() -> None:
    """Create the jobs table and associated indexes."""
    # Create enum type first
    job_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "status",
            job_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create indexes
    op.create_index("ix_jobs_job_type", "jobs", ["job_type"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_index("ix_jobs_status_type", "jobs", ["status", "job_type"])


def downgrade() -> None:
    """Drop the jobs table."""
    op.drop_index("ix_jobs_status_type")
    op.drop_index("ix_jobs_created_at")
    op.drop_index("ix_jobs_status")
    op.drop_index("ix_jobs_job_type")
    op.drop_table("jobs")
    job_status_enum.drop(op.get_bind(), checkfirst=True)
