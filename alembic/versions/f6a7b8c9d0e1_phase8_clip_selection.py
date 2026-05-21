"""Phase 8: add segment_descriptions, gemini_runs, clip_selections; migrate Short to clip_selection_id

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gemini_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_video_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("request_body", postgresql.JSONB(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error_class", sa.String(200), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_video_id"], ["source_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "segment_descriptions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_video_id", sa.BigInteger(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("announcements", sa.Text(), nullable=True),
        sa.Column("events", sa.Text(), nullable=True),
        sa.Column("entities", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_video_id"], ["source_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "clip_selections",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_video_id", sa.BigInteger(), nullable=False),
        sa.Column("gemini_run_id", sa.BigInteger(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("source_segment_ids", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_video_id"], ["source_videos.id"]),
        sa.ForeignKeyConstraint(["gemini_run_id"], ["gemini_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Migrate shorts: drop moment_id, add clip_selection_id + gemini columns
    op.drop_constraint("uq_shorts_source_moment", "shorts", type_="unique")
    op.drop_constraint("shorts_moment_id_key", "shorts", type_="unique")
    op.drop_constraint("shorts_moment_id_fkey", "shorts", type_="foreignkey")
    op.drop_column("shorts", "moment_id")
    op.drop_column("shorts", "clip_type")

    op.add_column(
        "shorts",
        sa.Column("clip_selection_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "shorts",
        sa.Column("gemini_rationale", sa.Text(), nullable=True),
    )
    op.add_column(
        "shorts",
        sa.Column("gemini_rank", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "shorts_clip_selection_id_fkey",
        "shorts",
        "clip_selections",
        ["clip_selection_id"],
        ["id"],
    )
    op.create_unique_constraint("uq_shorts_clip_selection", "shorts", ["clip_selection_id"])


def downgrade() -> None:
    op.drop_constraint("uq_shorts_clip_selection", "shorts", type_="unique")
    op.drop_constraint("shorts_clip_selection_id_fkey", "shorts", type_="foreignkey")
    op.drop_column("shorts", "gemini_rank")
    op.drop_column("shorts", "gemini_rationale")
    op.drop_column("shorts", "clip_selection_id")

    op.add_column("shorts", sa.Column("clip_type", sa.String(50), nullable=True))
    op.add_column("shorts", sa.Column("moment_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "shorts_moment_id_fkey", "shorts", "moments", ["moment_id"], ["id"]
    )
    op.create_unique_constraint("shorts_moment_id_key", "shorts", ["moment_id"])
    op.create_unique_constraint(
        "uq_shorts_source_moment", "shorts", ["source_video_id", "moment_id"]
    )

    op.drop_table("clip_selections")
    op.drop_table("segment_descriptions")
    op.drop_table("gemini_runs")
