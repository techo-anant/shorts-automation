from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.db.models import SegmentDescription, Short, SourceVideo, VideoIndex
from src.moments.gemini_select import select_clips
from src.moments.pegasus_describe import describe_video
from src.utils.ffmpeg import VideoInfo

log = structlog.get_logger(__name__)

_PROMPTS_ROOT = Path(__file__).parent.parent.parent / "prompts"


async def run_clip_selection(
    video_index: VideoIndex,
    source_video: SourceVideo,
    info: VideoInfo,
    session: AsyncSession,
    settings: Settings,
) -> list[Short]:
    """
    Full clip-selection pipeline for one source video:
      1. Pegasus analyze_async → SegmentDescription rows
      2. Gemini clip selection → ClipSelection + Short rows

    Returns the Short rows (flushed, caller must commit).
    Raises on any API failure — caller handles the exception boundary.
    """
    category = source_video.category

    pegasus_prompt = _PROMPTS_ROOT / "pegasus" / f"{category}.txt"
    gemini_prompt = _PROMPTS_ROOT / "gemini" / f"{category}.txt"

    # Fall back to gameplay prompt for unknown categories
    if not pegasus_prompt.exists():
        log.info("pegasus_prompt_fallback", category=category)
        pegasus_prompt = _PROMPTS_ROOT / "pegasus" / "gameplay.txt"
    if not gemini_prompt.exists():
        log.info("gemini_prompt_fallback", category=category)
        gemini_prompt = _PROMPTS_ROOT / "gemini" / "gameplay.txt"

    segments: list[SegmentDescription] = await describe_video(
        asset_id=video_index.index_id,
        source_video=source_video,
        info=info,
        session=session,
        settings=settings,
        prompt_path=pegasus_prompt,
    )

    if not segments:
        log.info("pegasus_no_segments", source_id=source_video.id)
        return []

    shorts: list[Short] = await select_clips(
        segments=segments,
        source_video=source_video,
        info=info,
        session=session,
        settings=settings,
        prompt_path=gemini_prompt,
    )

    log.info(
        "clip_selection_done",
        source_id=source_video.id,
        segments=len(segments),
        shorts=len(shorts),
    )
    return shorts
