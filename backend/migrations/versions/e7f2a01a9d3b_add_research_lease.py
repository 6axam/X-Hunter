"""add research run lease

Revision ID: e7f2a01a9d3b
Revises: d6a1e01f8b2c
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f2a01a9d3b"
down_revision: str | Sequence[str] | None = "d6a1e01f8b2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_sessions",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_sessions", "lease_expires_at")
