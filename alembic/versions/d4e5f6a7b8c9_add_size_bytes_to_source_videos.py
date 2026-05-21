"""add size_bytes to source_videos

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_videos",
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("source_videos", "size_bytes")
