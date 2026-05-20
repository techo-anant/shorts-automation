import asyncio
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from twelvelabs import TwelveLabs

from src.config import Settings
from src.costs.record import record_marengo_query
from src.db.models import Moment, MomentDetectionRun

log = structlog.get_logger(__name__)

# Search queries sent to Marengo for each clip type
_SHORTS_QUERY = (
    "exciting high-energy gameplay moment, clutch play, impressive skill, action-packed sequence"
)
_HIGHLIGHT_QUERY = (
    "key gameplay moment, important event, highlight worthy, significant game situation"
)

_SHORTS_MAX_DURATION = 180.0  # seconds — hard cap for YouTube Shorts
_MIN_CLIP_DURATION = 5.0      # seconds — drop clips shorter than this
_MIN_GAP_SECONDS = 5.0        # seconds — minimum gap between accepted clips of the same type


@dataclass
class _Clip:
    start: float
    end: float
    score: float
    confidence: str


class MarengoMomentsImpl:
    async def find_moments(
        self,
        index_id: str,
        source_video_id: int,
        run_id: int,
        session: AsyncSession,
        settings: Settings,
    ) -> list[Moment]:
        client = TwelveLabs(api_key=settings.TWELVELABS_API_KEY)

        # Run both search queries concurrently — each is a blocking SDK call
        shorts_raw, highlight_raw = await asyncio.gather(
            asyncio.to_thread(_search, client, index_id, _SHORTS_QUERY),
            asyncio.to_thread(_search, client, index_id, _HIGHLIGHT_QUERY),
        )

        await record_marengo_query(
            session=session,
            source_video_id=source_video_id,
            detection_run_id=run_id,
            query_type="shorts",
        )
        await record_marengo_query(
            session=session,
            source_video_id=source_video_id,
            detection_run_id=run_id,
            query_type="highlight_reel",
        )

        shorts_clips = _enforce_constraints(shorts_raw, max_duration=_SHORTS_MAX_DURATION)
        highlight_clips = _enforce_constraints(highlight_raw, max_duration=None)

        log.info(
            "moments_found",
            source_video_id=source_video_id,
            shorts_found=len(shorts_clips),
            shorts_dropped=len(shorts_raw) - len(shorts_clips),
            highlight_found=len(highlight_clips),
            highlight_dropped=len(highlight_raw) - len(highlight_clips),
        )

        moments: list[Moment] = []
        for clip in shorts_clips:
            m = Moment(
                run_id=run_id,
                start_seconds=clip.start,
                end_seconds=clip.end,
                clip_type="shorts",
                reason=f"score={clip.score:.1f} confidence={clip.confidence}",
            )
            session.add(m)
            moments.append(m)

        for clip in highlight_clips:
            m = Moment(
                run_id=run_id,
                start_seconds=clip.start,
                end_seconds=clip.end,
                clip_type="highlight_reel",
                reason=f"score={clip.score:.1f} confidence={clip.confidence}",
            )
            session.add(m)
            moments.append(m)

        await session.flush()

        # Store raw API responses verbatim for bake-off analysis
        run = await session.get(MomentDetectionRun, run_id)
        if run is not None:
            run.status = "queried"
            run.raw_response = {
                "shorts": [
                    {"start": c.start, "end": c.end, "score": c.score, "confidence": c.confidence}
                    for c in shorts_raw
                ],
                "highlight_reel": [
                    {"start": c.start, "end": c.end, "score": c.score, "confidence": c.confidence}
                    for c in highlight_raw
                ],
            }

        return moments


def _search(client: TwelveLabs, index_id: str, query: str) -> list[_Clip]:
    """
    Synchronous — runs in a thread pool via asyncio.to_thread.

    NOTE: Verify SDK method signatures against the installed twelvelabs package
    version before the first production run.
    """
    results = client.search.query(
        index_id=index_id,
        query_text=query,
        options=["visual", "audio"],
        threshold="medium",
    )
    return [
        _Clip(
            start=float(clip.start),
            end=float(clip.end),
            score=float(clip.score),
            confidence=str(getattr(clip, "confidence", "medium")),
        )
        for clip in results.data
    ]


def _enforce_constraints(clips: list[_Clip], max_duration: float | None) -> list[_Clip]:
    """
    Filter and deduplicate clips:
    - Drop clips shorter than _MIN_CLIP_DURATION
    - Drop clips longer than max_duration (if set)
    - Among remaining, greedily accept highest-scoring clips that don't
      overlap and maintain at least _MIN_GAP_SECONDS from already-accepted clips
    Results are returned sorted by start time.
    """
    candidates = sorted(clips, key=lambda c: c.score, reverse=True)
    accepted: list[_Clip] = []

    for clip in candidates:
        duration = clip.end - clip.start

        if duration < _MIN_CLIP_DURATION:
            log.info("clip_dropped_too_short", start=clip.start, end=clip.end, duration=duration)
            continue

        if max_duration is not None and duration > max_duration:
            log.info("clip_dropped_too_long", start=clip.start, end=clip.end, duration=duration)
            continue

        if _conflicts_with_accepted(clip, accepted):
            log.info("clip_dropped_conflict", start=clip.start, end=clip.end, score=clip.score)
            continue

        accepted.append(clip)

    return sorted(accepted, key=lambda c: c.start)


def _conflicts_with_accepted(clip: _Clip, accepted: list[_Clip]) -> bool:
    for acc in accepted:
        # Overlap
        if clip.start < acc.end and clip.end > acc.start:
            return True
        # Too close (gap check)
        gap = clip.start - acc.end if clip.start >= acc.end else acc.start - clip.end
        if gap < _MIN_GAP_SECONDS:
            return True
    return False
