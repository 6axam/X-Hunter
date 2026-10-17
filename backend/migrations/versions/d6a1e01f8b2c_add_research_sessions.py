"""add resumable AI research sessions

Revision ID: d6a1e01f8b2c
Revises: b96c40c1fc69
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6a1e01f8b2c"
down_revision: str | Sequence[str] | None = "b96c40c1fc69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_sessions",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("marketplaces", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("max_items", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("compact_memory", sa.JSON(), nullable=False),
        sa.Column("budget_usd", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_sessions_owner_id", "research_sessions", ["owner_id"])
    op.create_index("ix_research_sessions_status", "research_sessions", ["status"])
    op.create_index("ix_research_owner_status", "research_sessions", ["owner_id", "status", "updated_at"])
    op.create_table(
        "research_items",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("recommendation", sa.String(length=32), nullable=True),
        sa.Column("listing_snapshot", sa.JSON(), nullable=False),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("actual_cost_usd", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["research_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "listing_id"),
    )
    op.create_index("ix_research_items_session_id", "research_items", ["session_id"])
    op.create_index("ix_research_items_listing_id", "research_items", ["listing_id"])
    op.create_index("ix_research_items_status", "research_items", ["status"])
    op.create_index("ix_research_items_session_rank", "research_items", ["session_id", "score", "created_at"])
    op.create_table(
        "research_events",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["research_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_events_session_id", "research_events", ["session_id"])
    op.create_index("ix_research_events_kind", "research_events", ["kind"])
    op.create_index("ix_research_events_session_created", "research_events", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_table("research_events")
    op.drop_table("research_items")
    op.drop_table("research_sessions")
