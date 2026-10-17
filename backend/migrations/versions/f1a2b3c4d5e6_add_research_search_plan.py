"""add durable AI search plans

Revision ID: f1a2b3c4d5e6
Revises: e7f2a01a9d3b
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e7f2a01a9d3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_sessions",
        sa.Column(
            "rejected_before_analysis_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "research_sessions",
        sa.Column("collected_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "research_sessions",
        sa.Column("search_plan", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "research_sessions",
        sa.Column("executed_searches", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "research_sessions",
        sa.Column(
            "planning_estimated_cost_usd", sa.Numeric(14, 6), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "research_sessions",
        sa.Column(
            "planning_actual_cost_usd", sa.Numeric(14, 6), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "research_sessions",
        sa.Column("planning_input_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "research_sessions",
        sa.Column("planning_output_tokens", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    for column in (
        "planning_output_tokens",
        "planning_input_tokens",
        "planning_actual_cost_usd",
        "planning_estimated_cost_usd",
        "executed_searches",
        "search_plan",
        "collected_count",
        "rejected_before_analysis_count",
    ):
        op.drop_column("research_sessions", column)
