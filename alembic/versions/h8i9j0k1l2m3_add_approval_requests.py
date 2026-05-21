"""Add approval_requests table

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-05-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h8i9j0k1l2m3"
down_revision: str | None = "g7h8i9j0k1l2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_video_id", sa.BigInteger(), nullable=False),
        sa.Column("short_id", sa.BigInteger(), nullable=False),
        sa.Column("discord_message_id", sa.String(255), nullable=False),
        sa.Column("drive_preview_file_id", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(50), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_video_id"], ["source_videos.id"]),
        sa.ForeignKeyConstraint(["short_id"], ["shorts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("short_id", name="uq_approval_requests_short_id"),
    )


def downgrade() -> None:
    op.drop_table("approval_requests")
