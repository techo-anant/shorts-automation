from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SourceVideo(Base):
    __tablename__ = "source_videos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drive_file_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    # pending | processing | completed | completed_no_output | failed
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="pending")
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    format_profile: Mapped[Optional["FormatProfile"]] = relationship(
        back_populates="source_video", uselist=False
    )
    detection_runs: Mapped[list["MomentDetectionRun"]] = relationship(
        back_populates="source_video"
    )
    cost_events: Mapped[list["CostEvent"]] = relationship(back_populates="source_video")


class FormatProfile(Base):
    __tablename__ = "format_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False, unique=True
    )
    has_facecam: Mapped[bool] = mapped_column(Boolean, nullable=False)
    facecam_position: Mapped[str | None] = mapped_column(String(50), nullable=True)
    facecam_size_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_aspect_ratio: Mapped[str] = mapped_column(String(20), nullable=False)
    is_already_vertical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # True when facecam detection disagrees across sampled frames — falls back to category default
    facecam_unstable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="format_profile")


class MomentDetectionRun(Base):
    __tablename__ = "moment_detection_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    # pegasus | gemini
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # Pegasus index ID — persisted so we never re-index the same video
    index_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # pending | indexing | ready | queried | failed
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    # Raw API response stored verbatim for debugging and bake-off analysis
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="detection_runs")
    moments: Mapped[list["Moment"]] = relationship(back_populates="run")
    cost_events: Mapped[list["CostEvent"]] = relationship(back_populates="detection_run")


class Moment(Base):
    __tablename__ = "moments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("moment_detection_runs.id"), nullable=False
    )
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped["MomentDetectionRun"] = relationship(back_populates="moments")


class CostEvent(Base):
    """One row per priced API call. Never batch-write; write immediately after the call succeeds."""

    __tablename__ = "cost_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    # Nullable until Short model is introduced in v2
    short_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detection_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("moment_detection_runs.id"), nullable=True
    )
    # e.g. ingest.format_probe | indexer.pegasus_index | moments.pegasus_query
    step: Mapped[str] = mapped_column(String(100), nullable=False)
    # e.g. google | twelvelabs | youtube
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # e.g. gemini-2.5-flash | pegasus-1.2
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    # Raw units consumed (tokens, minutes, api_units — semantics depend on step)
    units: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit_cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    # Set when the provider later returns an actual billed amount; never overwrites estimate
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    # Includes pricing_version key so stale estimates are identifiable
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="cost_events")
    detection_run: Mapped[Optional["MomentDetectionRun"]] = relationship(
        back_populates="cost_events"
    )


class QuotaUsage(Base):
    """Daily quota tracker per provider (e.g. YouTube Data API units)."""

    __tablename__ = "quota_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    units_used: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("provider", "date", name="uq_quota_usage_provider_date"),
    )
