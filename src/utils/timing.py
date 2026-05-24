"""
Pipeline step timing — async context manager that records a StepTiming row.

Usage:
    async with record_step_timing(source_video_id, "pegasus_describe"):
        result = await describe_video(...)

Write failures are swallowed with a warning so timing never breaks the pipeline.
"""

from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import StepTiming
from src.db.session import get_session_factory

log = structlog.get_logger(__name__)

# Source-level steps shown first in the summary (in pipeline order)
_SOURCE_STEPS = [
    "drive_download",
    "ffprobe",
    "oversized_preprocess",
    "frame_extraction",
    "gemini_format_probe",
    "pegasus_index",
    "pegasus_describe",
    "gemini_clip_selection",
]

# Per-short steps — grouped with a clip count suffix
_SHORT_STEPS = [
    "ffmpeg_cut",
    "drive_upload_preview",
    "drive_preview_ready",
    "discord_post_approval",
    "approval_wait",
    "youtube_upload",
]


@asynccontextmanager
async def record_step_timing(
    source_video_id: int,
    step_name: str,
    short_id: int | None = None,
) -> AsyncGenerator[None, None]:
    """
    Async context manager that times the wrapped block and writes a StepTiming row.
    succeeded=True when the block completes normally, False if it raises.
    The exception is always re-raised regardless of timing write success.
    """
    started_at = datetime.now(tz=UTC)
    succeeded = False
    try:
        yield
        succeeded = True
    finally:
        ended_at = datetime.now(tz=UTC)
        duration_seconds = (ended_at - started_at).total_seconds()
        try:
            async with get_session_factory()() as session:
                session.add(
                    StepTiming(
                        source_video_id=source_video_id,
                        short_id=short_id,
                        step_name=step_name,
                        started_at=started_at,
                        ended_at=ended_at,
                        duration_seconds=duration_seconds,
                        succeeded=succeeded,
                    )
                )
                await session.commit()
        except Exception:
            log.warning(
                "step_timing_write_failed",
                step=step_name,
                source_id=source_video_id,
            )


async def get_step_timings(session: AsyncSession, source_video_id: int) -> list[StepTiming]:
    """Return all StepTiming rows for a source video, ordered by start time."""
    result = await session.execute(
        select(StepTiming)
        .where(StepTiming.source_video_id == source_video_id)
        .order_by(StepTiming.started_at)
    )
    return list(result.scalars().all())


def format_timing_summary(timings: list[StepTiming]) -> str:
    """Format a compact timing block for the Discord success summary."""
    if not timings:
        return ""

    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for t in timings:
        totals[t.step_name] += t.duration_seconds
        counts[t.step_name] += 1

    lines = ["⏱ **Step timings:**"]
    shown: set[str] = set()

    for step in _SOURCE_STEPS:
        if step in totals:
            lines.append(f"  `{step}`: {totals[step]:.1f}s")
            shown.add(step)

    for step in _SHORT_STEPS:
        if step in totals:
            n = counts[step]
            suffix = f" ×{n}" if n > 1 else ""
            lines.append(f"  `{step}`: {totals[step]:.1f}s{suffix}")
            shown.add(step)

    # Any unexpected steps not in the known lists
    for step, total in totals.items():
        if step not in shown:
            lines.append(f"  `{step}`: {total:.1f}s")

    grand_total = sum(totals.values())
    lines.append(f"  **Total: {grand_total:.0f}s**")
    return "\n".join(lines)
