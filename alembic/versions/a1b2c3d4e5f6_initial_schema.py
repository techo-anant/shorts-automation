"""initial schema

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-05-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_videos",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("drive_file_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drive_file_id", name="uq_source_videos_drive_file_id"),
    )
    op.create_index("ix_source_videos_status", "source_videos", ["status"])

    op.create_table(
        "format_profiles",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_video_id", sa.BigInteger(), nullable=False),
        sa.Column("has_facecam", sa.Boolean(), nullable=False),
        sa.Column("facecam_position", sa.String(50), nullable=True),
        sa.Column("facecam_size_pct", sa.Float(), nullable=True),
        sa.Column("source_aspect_ratio", sa.String(20), nullable=False),
        sa.Column("is_already_vertical", sa.Boolean(), nullable=False),
        sa.Column("facecam_unstable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_video_id"], ["source_videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_video_id", name="uq_format_profiles_source_video_id"),
    )

    op.create_table(
        "moment_detection_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_video_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("index_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("raw_response", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_video_id"], ["source_videos.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_moment_detection_runs_source_video_id",
        "moment_detection_runs",
        ["source_video_id"],
    )

    op.create_table(
        "moments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["moment_detection_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "cost_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_video_id", sa.BigInteger(), nullable=False),
        sa.Column("short_id", sa.BigInteger(), nullable=True),
        sa.Column("detection_run_id", sa.BigInteger(), nullable=True),
        sa.Column("step", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("units", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit_cost_usd", sa.Numeric(20, 10), nullable=False),
        sa.Column("total_cost_usd", sa.Numeric(20, 10), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(20, 10), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_video_id"], ["source_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["detection_run_id"], ["moment_detection_runs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_events_source_video_id", "cost_events", ["source_video_id"])

    op.create_table(
        "quota_usage",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("units_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "date", name="uq_quota_usage_provider_date"),
    )


def downgrade() -> None:
    op.drop_table("quota_usage")
    op.drop_index("ix_cost_events_source_video_id", table_name="cost_events")
    op.drop_table("cost_events")
    op.drop_table("moments")
    op.drop_index(
        "ix_moment_detection_runs_source_video_id", table_name="moment_detection_runs"
    )
    op.drop_table("moment_detection_runs")
    op.drop_table("format_profiles")
    op.drop_index("ix_source_videos_status", table_name="source_videos")
    op.drop_table("source_videos")
