"""Add crop_requests table

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-05-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i9j0k1l2m3n4"
down_revision: str | None = "h8i9j0k1l2m3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crop_requests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_video_id", sa.BigInteger(), nullable=False),
        sa.Column("discord_message_id", sa.String(255), nullable=False),
        sa.Column("drive_frame_file_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), server_default="pending", nullable=False),
        sa.Column("crop_x", sa.Integer(), nullable=True),
        sa.Column("crop_y", sa.Integer(), nullable=True),
        sa.Column("crop_w", sa.Integer(), nullable=True),
        sa.Column("crop_h", sa.Integer(), nullable=True),
        sa.Column("ref_width", sa.Integer(), nullable=False),
        sa.Column("ref_height", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_video_id"], ["source_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_video_id", name="uq_crop_requests_source_video_id"),
    )


def downgrade() -> None:
    op.drop_table("crop_requests")
