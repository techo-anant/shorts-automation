import json
from pathlib import Path
from typing import Any

import structlog
from google import genai
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.costs.record import record_gemini_select
from src.db.models import ClipSelection, GeminiRun, SegmentDescription, Short, SourceVideo
from src.errors import GeminiQuotaError, GeminiSelectionError
from src.utils.ffmpeg import VideoInfo

log = structlog.get_logger(__name__)

_GEMINI_QUOTA_SIGNALS = frozenset({
    "429",
    "resource_exhausted",
    "quota",
    "failed_precondition",  # 400: billing not enabled / free tier unavailable
    "billing",
})


def _is_gemini_quota_error(exc: Exception) -> bool:
    exc_str = str(exc).lower()
    return any(signal in exc_str for signal in _GEMINI_QUOTA_SIGNALS)

_GEMINI_MODEL = "gemini-2.5-flash"

_MIN_DURATION = 60.0    # seconds — YouTube Shorts floor
_MAX_DURATION = 180.0   # seconds — YouTube Shorts hard cap
_MIN_SANITY = 3.0       # absolute minimum to discard obviously broken clips

_PROMPT_VERSION = "gameplay-v1"


async def select_clips(
    segments: list[SegmentDescription],
    source_video: SourceVideo,
    info: VideoInfo,
    session: AsyncSession,
    settings: Settings,
    prompt_path: Path | None = None,
) -> list[Short]:
    """
    Uploads the source video to Gemini Files API, sends segment descriptions + selection prompt,
    parses the JSON response, validates clips, writes ClipSelection + GeminiRun rows, and
    creates one Short per selection.  Returns the Short rows (flushed, not yet committed).
    """
    prompt_template = _default_prompt_template()
    if prompt_path is not None and prompt_path.exists():
        prompt_template = prompt_path.read_text(encoding="utf-8")

    segment_json = _segments_to_json(segments)
    prompt_text = prompt_template.replace("{segment_descriptions_json}", segment_json)

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    gemini_run = GeminiRun(
        source_video_id=source_video.id,
        prompt_version=_PROMPT_VERSION,
        request_body={"segment_count": len(segments), "prompt_version": _PROMPT_VERSION},
        status="pending",
    )
    session.add(gemini_run)
    await session.flush()

    try:
        response = client.models.generate_content(
            model=_GEMINI_MODEL,
            contents=[prompt_text],
        )

        raw_text: str = response.text or ""
        usage = response.usage_metadata
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0

        parsed = _parse_response(raw_text)

        gemini_run.response_body = {"raw": raw_text[:8000], "clips_found": len(parsed)}
        gemini_run.status = "completed"
        await session.flush()

        await record_gemini_select(
            session=session,
            source_video_id=source_video.id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    except Exception as exc:
        gemini_run.status = "failed"
        gemini_run.error_class = type(exc).__name__
        gemini_run.error_message = str(exc)[:2000]
        await session.flush()
        if isinstance(exc, GeminiSelectionError):
            raise
        if _is_gemini_quota_error(exc):
            raise GeminiQuotaError(f"Gemini quota/billing issue during clip selection: {exc}") from exc
        raise GeminiSelectionError(f"Gemini clip selection failed: {exc}") from exc

    validated = _validate_clips(parsed, info.duration_seconds)
    log.info(
        "gemini_select_complete",
        source_id=source_video.id,
        clips_returned=len(parsed),
        clips_valid=len(validated),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    shorts: list[Short] = []
    for clip in validated:
        selection = ClipSelection(
            source_video_id=source_video.id,
            gemini_run_id=gemini_run.id,
            start_seconds=clip["start_seconds"],
            end_seconds=clip["end_seconds"],
            rationale=clip["rationale"],
            title=clip.get("title"),
            rank=clip["rank"],
            source_segment_ids=clip["source_segment_ids"],
        )
        session.add(selection)
        await session.flush()

        short = Short(
            source_video_id=source_video.id,
            clip_selection_id=selection.id,
            status="pending",
            cut_start_seconds=clip["start_seconds"],
            cut_end_seconds=clip["end_seconds"],
            gemini_rationale=clip["rationale"],
            gemini_rank=clip["rank"],
            title=clip.get("title"),
        )
        session.add(short)
        shorts.append(short)

    await session.flush()
    return shorts


def _parse_response(raw_text: str) -> list[dict[str, Any]]:
    """Extract JSON from Gemini response; return list of raw clip dicts."""
    text = raw_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiSelectionError(f"Gemini returned non-JSON: {exc}\n---\n{raw_text[:500]}") from exc

    if not isinstance(parsed, dict):
        raise GeminiSelectionError(f"Expected JSON object, got {type(parsed).__name__}")

    clips = parsed.get("clips", [])
    if not isinstance(clips, list):
        raise GeminiSelectionError("'clips' field is not a list")

    return clips


def _validate_clips(raw_clips: list[dict[str, Any]], source_duration: float) -> list[dict[str, Any]]:
    """
    Apply the rules from docs/plan.md Phase 8:
    - start < end
    - duration in [MIN_SANITY, MAX_DURATION]; drop if outside
    - trim to MAX_DURATION if over (and log)
    - drop if entirely outside source duration
    - rank clamped to [1, 10]
    - no two clips overlap (drop lower-rank one)
    Returns valid clips sorted by start_seconds.
    """
    candidates: list[dict[str, Any]] = []

    for raw in raw_clips:
        try:
            start = float(raw["start_seconds"])
            end = float(raw["end_seconds"])
        except (KeyError, TypeError, ValueError):
            log.warning("gemini_clip_bad_times", clip=raw)
            continue

        if start >= end:
            log.warning("gemini_clip_start_gte_end", start=start, end=end)
            continue

        duration = end - start
        if duration < _MIN_SANITY:
            log.warning("gemini_clip_too_short", start=start, end=end, duration=duration)
            continue

        if end > source_duration + 1.0:
            log.warning(
                "gemini_clip_beyond_source", end=end, source_duration=source_duration
            )
            end = source_duration
            duration = end - start

        if duration > _MAX_DURATION:
            log.warning("gemini_clip_trimmed", start=start, end=end, duration=duration)
            end = start + _MAX_DURATION
            duration = _MAX_DURATION

        if duration < _MIN_DURATION:
            log.warning("gemini_clip_under_minimum", start=start, end=end, duration=duration)
            continue

        try:
            rank = int(raw.get("rank", 5))
        except (TypeError, ValueError):
            rank = 5
        rank = max(1, min(10, rank))

        raw_title = raw.get("title")
        title = str(raw_title).strip() if raw_title else None

        candidates.append(
            {
                "start_seconds": start,
                "end_seconds": end,
                "rationale": str(raw.get("rationale", "")),
                "title": title,
                "rank": rank,
                "source_segment_ids": raw.get("source_segment_ids", []),
            }
        )

    # Greedy dedup: sort by rank desc, accept if no overlap with already-accepted
    candidates.sort(key=lambda c: c["rank"], reverse=True)
    accepted: list[dict[str, Any]] = []
    for clip in candidates:
        if any(
            clip["start_seconds"] < acc["end_seconds"]
            and clip["end_seconds"] > acc["start_seconds"]
            for acc in accepted
        ):
            log.info(
                "gemini_clip_dropped_overlap",
                start=clip["start_seconds"],
                end=clip["end_seconds"],
                rank=clip["rank"],
            )
            continue
        accepted.append(clip)

    return sorted(accepted, key=lambda c: c["start_seconds"])


def _segments_to_json(segments: list[SegmentDescription]) -> str:
    return json.dumps(
        [
            {
                "id": seg.id,
                "start_seconds": seg.start_seconds,
                "end_seconds": seg.end_seconds,
                "description": seg.description,
                "announcements": seg.announcements,
                "events": seg.events,
                "entities": seg.entities,
            }
            for seg in segments
        ],
        indent=2,
    )


def _default_prompt_template() -> str:
    return (
        "You are selecting YouTube Shorts clips from a gameplay video.\n\n"
        "The video has been analyzed in 10-second windows. "
        "Below is the full window list as JSON:\n\n"
        "{segment_descriptions_json}\n\n"
        "Select 1-5 clips that would perform well as YouTube Shorts "
        "(target 60-180 seconds per clip). For each clip:\n"
        "- Merge consecutive windows that form a coherent high-action sequence\n"
        "- Prioritize: multiple kills, ace announcements, clutch situations, "
        "spike plants or defuses, impressive ability usage\n"
        "- Avoid: menus, loading screens, intro/outro, pure rotation with no action\n\n"
        "Timestamp rules:\n"
        "- start_seconds and end_seconds do NOT need to align with the 10-second window "
        "boundaries — pick the precise second where the action starts and ends\n"
        "- If a powerful sequence is about to begin (first kill incoming, ability about to "
        "pop, spike about to detonate), pad start_seconds back by 1–2 seconds so the viewer "
        "has a beat to focus before the action lands\n"
        "- end_seconds must land at a natural conclusion — one of: all enemies in the sequence "
        "are eliminated, an ace or round-win announcement plays, the spike explodes or is "
        "defused, or a clear pause follows the last kill. Do not end mid-fight or at an "
        "arbitrary second. Pad end_seconds forward by 1–2 seconds past that conclusion so "
        "the moment lands cleanly before the cut.\n\n"
        "For each clip also write a YouTube Shorts title following these rules:\n"
        "- 40-70 characters total, never exceed 100\n"
        "- Front-load the hook in the first 40 characters\n"
        "- Use exactly 1-2 relevant emojis (one at the start preferred); "
        "good choices: 🔥 💀 😱 🤯 🎯 ⚡ 🧠\n"
        "- Use a curiosity-gap pattern: "
        "POV: ... | 'I can't believe...' | stakes ('1v5 with 2hp') | "
        "outcome-tease ('Watch what happens') | result-first\n"
        "- Include specific names from the clip: agent, weapon, map location, "
        "player name if known\n"
        "- Never include #Shorts or any hashtag in the title\n"
        "- Prefer declarative statements over questions\n"
        "- Only promise what the clip actually delivers\n\n"
        "Return ONLY valid JSON with no markdown fences:\n"
        '{"clips": [{"start_seconds": <n>, "end_seconds": <n>, '
        '"rationale": "<why this clip>", '
        '"title": "<YouTube Shorts title>", '
        '"rank": <1-10>, '
        '"source_segment_ids": [<ids>]}], '
        '"no_clips_reason": "<only if clips empty>"}\n\n'
        "Rules: start < end, duration 60-180s, no overlaps, rank integer 1-10."
    )
