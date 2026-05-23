from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
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
    # pending | processing | waiting_for_crop | completed | completed_no_output | failed
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="pending")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when a per-source Discord thread is created at the start of the cut phase
    discord_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The "Processing…" placeholder message that gets edited to the success summary
    discord_summary_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
    segment_descriptions: Mapped[list["SegmentDescription"]] = relationship(
        back_populates="source_video"
    )
    gemini_runs: Mapped[list["GeminiRun"]] = relationship(back_populates="source_video")
    clip_selections: Mapped[list["ClipSelection"]] = relationship(back_populates="source_video")
    shorts: Mapped[list["Short"]] = relationship(back_populates="source_video")
    approval_requests: Mapped[list["ApprovalRequest"]] = relationship(back_populates="source_video")
    crop_request: Mapped[Optional["CropRequest"]] = relationship(
        back_populates="source_video", uselist=False
    )
    cost_events: Mapped[list["CostEvent"]] = relationship(back_populates="source_video")
    step_timings: Mapped[list["StepTiming"]] = relationship(back_populates="source_video")


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
    """Long-lived TwelveLabs asset — created once per source video, reused across runs."""

    __tablename__ = "video_indexes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    # pegasus (active) | marengo (legacy v1)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # TwelveLabs asset_id for pegasus; index_id for legacy marengo rows
    index_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    # indexing | ready | expired | failed
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="video_indexes")
    detection_runs: Mapped[list["MomentDetectionRun"]] = relationship(
        back_populates="video_index"
    )


class MomentDetectionRun(Base):
    """Legacy v1 table — kept for historical data, not used in v2 pipeline."""

    __tablename__ = "moment_detection_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    video_index_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("video_indexes.id"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="detection_runs")
    video_index: Mapped[Optional["VideoIndex"]] = relationship(back_populates="detection_runs")
    moments: Mapped[list["Moment"]] = relationship(back_populates="run")
    cost_events: Mapped[list["CostEvent"]] = relationship(back_populates="detection_run")


class Moment(Base):
    """Legacy v1 table — kept for historical data, not used in v2 pipeline."""

    __tablename__ = "moments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("moment_detection_runs.id"), nullable=False
    )
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    clip_type: Mapped[str] = mapped_column(String(50), nullable=False, server_default="shorts")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped["MomentDetectionRun"] = relationship(back_populates="moments")


class GeminiRun(Base):
    """Stores the full Gemini clip-selection request and response for debugging and prompt tuning."""

    __tablename__ = "gemini_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    # Stored without the video bytes — segment descriptions + prompt text only
    request_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # pending | completed | failed
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    error_class: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="gemini_runs")
    clip_selections: Mapped[list["ClipSelection"]] = relationship(back_populates="gemini_run")


class SegmentDescription(Base):
    """One row per 10-second window from the Pegasus analyze call."""

    __tablename__ = "segment_descriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    announcements: Mapped[str | None] = mapped_column(Text, nullable=True)
    events: Mapped[str | None] = mapped_column(Text, nullable=True)
    entities: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="segment_descriptions")


class ClipSelection(Base):
    """One row per clip chosen by Gemini — each becomes exactly one Short."""

    __tablename__ = "clip_selections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    gemini_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("gemini_runs.id"), nullable=False
    )
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    # IDs of SegmentDescription rows merged into this clip
    source_segment_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="clip_selections")
    gemini_run: Mapped["GeminiRun"] = relationship(back_populates="clip_selections")
    short: Mapped[Optional["Short"]] = relationship(back_populates="clip_selection")


class Short(Base):
    __tablename__ = "shorts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    clip_selection_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("clip_selections.id"), nullable=True, unique=True
    )
    # pending | cutting | cut | pending_approval | approved | uploading | uploaded
    # | failed | rejected | skipped
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="pending")
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cut_start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    cut_end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    format_recipe: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Denormalized from ClipSelection for fast access without a join
    gemini_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    gemini_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="shorts")
    clip_selection: Mapped[Optional["ClipSelection"]] = relationship(back_populates="short")
    approval_request: Mapped[Optional["ApprovalRequest"]] = relationship(back_populates="short")
    cost_events: Mapped[list["CostEvent"]] = relationship(back_populates="short")
    step_timings: Mapped[list["StepTiming"]] = relationship(back_populates="short")


class ApprovalRequest(Base):
    """One row per Short sent to Discord for operator approval."""

    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    short_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shorts.id"), nullable=False, unique=True
    )
    discord_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Thread ID when approval messages live inside a per-source thread (null for direct-channel posts)
    discord_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Drive file ID of the preview MP4 in pending-approval/
    drive_preview_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # approved | rejected | rejected_timeout | null (pending)
    decision: Mapped[str | None] = mapped_column(String(50), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="approval_requests")
    short: Mapped["Short"] = relationship(back_populates="approval_request")


class CropRequest(Base):
    """Per-source-video operator crop input — created when facecam is detected."""

    __tablename__ = "crop_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False, unique=True
    )
    discord_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Drive file ID of the reference JPEG frame
    drive_frame_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # pending | provided | timed_out
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="pending")
    # Crop rectangle in pixels on the reference frame (null until operator replies)
    crop_x: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crop_y: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crop_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crop_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Resolution of the frame the operator measured on (for proportional scaling)
    ref_width: Mapped[int] = mapped_column(Integer, nullable=False)
    ref_height: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="crop_request")


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
    # e.g. ingest.format_probe | indexer.pegasus_upload | moments.pegasus_describe | moments.gemini_select
    step: Mapped[str] = mapped_column(String(100), nullable=False)
    # e.g. google | twelvelabs | youtube
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # e.g. gemini-2.5-flash | pegasus-1.5
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    units: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit_cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="cost_events")
    short: Mapped[Optional["Short"]] = relationship(back_populates="cost_events")
    detection_run: Mapped[Optional["MomentDetectionRun"]] = relationship(
        back_populates="cost_events"
    )


class StepTiming(Base):
    """One row per pipeline step per run. Timing is best-effort — write failures are non-fatal."""

    __tablename__ = "step_timings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_videos.id"), nullable=False
    )
    # Null for source-level steps; set for per-short steps (ffmpeg_cut, drive_upload_preview, etc.)
    short_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("shorts.id"), nullable=True
    )
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="step_timings")
    short: Mapped[Optional["Short"]] = relationship(back_populates="step_timings")


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
