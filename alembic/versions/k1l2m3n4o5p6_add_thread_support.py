"""Add Discord thread support to source_videos and approval_requests

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-05-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k1l2m3n4o5p6"
down_revision: str | None = "j0k1l2m3n4o5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_videos",
        sa.Column("discord_thread_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "source_videos",
        sa.Column("discord_summary_message_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "approval_requests",
        sa.Column("discord_thread_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("approval_requests", "discord_thread_id")
    op.drop_column("source_videos", "discord_summary_message_id")
    op.drop_column("source_videos", "discord_thread_id")
