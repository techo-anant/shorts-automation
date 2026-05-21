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
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
    video_indexes: Mapped[list["VideoIndex"]] = relationship(back_populates="source_video")
    detection_runs: Mapped[list["MomentDetectionRun"]] = relationship(
        back_populates="source_video"
    )
    shorts: Mapped[list["Short"]] = relationship(back_populates="source_video")
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


class VideoIndex(Base):
    """Long-lived TwelveLabs index asset — created once per source video, reused across runs."""

    __tablename__ = "video_indexes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    # marengo | pegasus
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # TwelveLabs index_id — empty string while "indexing", populated when "ready"
    index_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    # indexing | ready | expired | failed
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    # RFC 3339 expiry from TwelveLabs — None if the provider doesn't expire this index
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="video_indexes")
    detection_runs: Mapped[list["MomentDetectionRun"]] = relationship(
        back_populates="video_index"
    )


class MomentDetectionRun(Base):
    __tablename__ = "moment_detection_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    video_index_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("video_indexes.id"), nullable=True
    )
    # marengo | gemini
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # pending | queried | failed
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    # Raw API response stored verbatim for debugging and bake-off analysis
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="detection_runs")
    video_index: Mapped[Optional["VideoIndex"]] = relationship(back_populates="detection_runs")
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
    # shorts | highlight_reel — set by the moments provider to distinguish query results
    clip_type: Mapped[str] = mapped_column(String(50), nullable=False, server_default="shorts")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped["MomentDetectionRun"] = relationship(back_populates="moments")
    short: Mapped[Optional["Short"]] = relationship(back_populates="moment")


class Short(Base):
    __tablename__ = "shorts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    moment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("moments.id"), nullable=False, unique=True
    )
    # shorts | highlight_reel — mirrors Moment.clip_type
    clip_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # pending | cutting | cut | pending_approval | approved | uploading | uploaded
    # | failed | rejected | skipped
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="pending")
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cut_start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    cut_end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    # Populated in Phase 8 when the FFmpeg recipe is selected
    format_recipe: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("source_video_id", "moment_id", name="uq_shorts_source_moment"),
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="shorts")
    moment: Mapped["Moment"] = relationship(back_populates="short")
    cost_events: Mapped[list["CostEvent"]] = relationship(back_populates="short")


class CostEvent(Base):
    """One row per priced API call. Never batch-write; write immediately after the call succeeds."""

    __tablename__ = "cost_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    short_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("shorts.id"), nullable=True
    )
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
    short: Mapped[Optional["Short"]] = relationship(back_populates="cost_events")
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
