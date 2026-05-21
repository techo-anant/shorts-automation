"""add shorts table and wire CostEvent.short_id FK

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shorts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_video_id", sa.BigInteger(), nullable=False),
        sa.Column("moment_id", sa.BigInteger(), nullable=False),
        sa.Column("clip_type", sa.String(50), nullable=False),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("drive_file_id", sa.String(255), nullable=True),
        sa.Column("youtube_video_id", sa.String(255), nullable=True),
        sa.Column("cut_start_seconds", sa.Float(), nullable=False),
        sa.Column("cut_end_seconds", sa.Float(), nullable=False),
        sa.Column("format_recipe", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["source_video_id"], ["source_videos.id"]),
        sa.ForeignKeyConstraint(["moment_id"], ["moments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("moment_id", name="uq_shorts_moment_id"),
        sa.UniqueConstraint("source_video_id", "moment_id", name="uq_shorts_source_moment"),
    )
    op.create_index("ix_shorts_source_video_id", "shorts", ["source_video_id"])
    op.create_index("ix_shorts_status", "shorts", ["status"])

    # Wire the FK that has existed as a bare BigInteger since the initial schema
    op.create_foreign_key(
        "fk_cost_events_short_id",
        "cost_events",
        "shorts",
        ["short_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_cost_events_short_id", "cost_events", type_="foreignkey")
    op.drop_index("ix_shorts_status", table_name="shorts")
    op.drop_index("ix_shorts_source_video_id", table_name="shorts")
    op.drop_table("shorts")
