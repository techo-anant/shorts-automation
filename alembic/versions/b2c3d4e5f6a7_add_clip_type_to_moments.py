"""add clip_type to moments

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # shorts | highlight_reel — distinguishes the two Marengo search queries
    op.add_column(
        "moments",
        sa.Column(
            "clip_type",
            sa.String(50),
            nullable=False,
            server_default="shorts",
        ),
    )
    op.create_index("ix_moments_clip_type", "moments", ["clip_type"])


def downgrade() -> None:
    op.drop_index("ix_moments_clip_type", table_name="moments")
    op.drop_column("moments", "clip_type")
