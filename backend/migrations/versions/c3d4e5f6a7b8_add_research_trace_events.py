"""add durable research diagnostic trace events

Revision ID: c3d4e5f6a7b8
Revises: a2b4c6d8e0f1
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "a2b4c6d8e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_sessions",
        sa.Column("trace_event_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "research_sessions",
        sa.Column("trace_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "research_sessions",
        sa.Column("trace_payload_bytes", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "research_trace_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("round_id", sa.String(length=80), nullable=True),
        sa.Column("query_id", sa.String(length=160), nullable=True),
        sa.Column("listing_id", sa.String(length=160), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["research_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "sequence"),
    )
    op.create_index(
        "ix_research_trace_events_session_id",
        "research_trace_events",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_research_trace_session_time",
        "research_trace_events",
        ["session_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_research_trace_retention",
        "research_trace_events",
        ["occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_research_trace_events_stage",
        "research_trace_events",
        ["stage"],
        unique=False,
    )
    op.create_index(
        "ix_research_trace_events_event_type",
        "research_trace_events",
        ["event_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("research_trace_events")
    op.drop_column("research_sessions", "trace_payload_bytes")
    op.drop_column("research_sessions", "trace_truncated")
    op.drop_column("research_sessions", "trace_event_count")
