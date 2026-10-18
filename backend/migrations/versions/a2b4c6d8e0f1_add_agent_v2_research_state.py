"""add durable Agent v2 research state

Revision ID: a2b4c6d8e0f1
Revises: f1a2b3c4d5e6
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b4c6d8e0f1"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        ("technical_reasoning", sa.JSON(), "'{}'"),
        ("product_hypotheses", sa.JSON(), "'[]'"),
        ("research_rounds", sa.JSON(), "'[]'"),
        ("supervisor_decisions", sa.JSON(), "'[]'"),
        ("market_statistics", sa.JSON(), "'{}'"),
        ("final_comparison", sa.JSON(), "'{}'"),
        ("stage_usage", sa.JSON(), "'{}'"),
    )
    for name, kind, default in columns:
        op.add_column(
            "research_sessions",
            sa.Column(name, kind, nullable=False, server_default=sa.text(default)),
        )
    op.add_column(
        "research_sessions",
        sa.Column("current_round", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    for name in (
        "current_round",
        "stage_usage",
        "final_comparison",
        "market_statistics",
        "supervisor_decisions",
        "research_rounds",
        "product_hypotheses",
        "technical_reasoning",
    ):
        op.drop_column("research_sessions", name)
