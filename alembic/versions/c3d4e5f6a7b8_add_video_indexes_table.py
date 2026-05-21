"""add video_indexes table and migrate index_id from moment_detection_runs

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_indexes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_video_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("index_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["source_video_id"], ["source_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_video_indexes_source_video_id", "video_indexes", ["source_video_id"])

    # Backfill: each moment_detection_run row that has an index_id becomes a VideoIndex row
    op.execute(
        """
        INSERT INTO video_indexes (source_video_id, provider, index_id, status, created_at)
        SELECT source_video_id, provider, index_id,
               CASE WHEN status IN ('ready', 'queried') THEN 'ready' ELSE status END,
               created_at
        FROM moment_detection_runs
        WHERE index_id IS NOT NULL AND index_id != ''
        """
    )

    # Add video_index_id FK column (nullable — legacy rows will have NULL)
    op.add_column(
        "moment_detection_runs",
        sa.Column("video_index_id", sa.BigInteger(), nullable=True),
    )

    # Link existing runs to their backfilled video_indexes rows
    op.execute(
        """
        UPDATE moment_detection_runs mdr
        SET video_index_id = vi.id
        FROM video_indexes vi
        WHERE mdr.source_video_id = vi.source_video_id
          AND mdr.provider = vi.provider
          AND mdr.index_id = vi.index_id
        """
    )

    op.create_foreign_key(
        "fk_moment_detection_runs_video_index_id",
        "moment_detection_runs",
        "video_indexes",
        ["video_index_id"],
        ["id"],
    )

    op.drop_column("moment_detection_runs", "index_id")


def downgrade() -> None:
    op.add_column(
        "moment_detection_runs",
        sa.Column("index_id", sa.String(255), nullable=True),
    )

    # Restore index_id from the linked video_indexes row
    op.execute(
        """
        UPDATE moment_detection_runs mdr
        SET index_id = vi.index_id
        FROM video_indexes vi
        WHERE mdr.video_index_id = vi.id
        """
    )

    op.drop_constraint(
        "fk_moment_detection_runs_video_index_id", "moment_detection_runs", type_="foreignkey"
    )
    op.drop_column("moment_detection_runs", "video_index_id")
    op.drop_index("ix_video_indexes_source_video_id", table_name="video_indexes")
    op.drop_table("video_indexes")
