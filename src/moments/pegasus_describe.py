import asyncio
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from twelvelabs import TwelveLabs
from twelvelabs.types import (
    AsyncResponseFormat,
    SegmentDefinition,
    SegmentField,
    VideoContext_AssetId,
)

from src.config import Settings
from src.costs.record import record_pegasus_describe
from src.db.models import SegmentDescription, SourceVideo
from src.utils.ffmpeg import VideoInfo

log = structlog.get_logger(__name__)

_ANALYZE_TIMEOUT_SECONDS = 30 * 60
_POLL_INTERVAL_SECONDS = 15
_MAX_OUTPUT_TOKENS = 65536

# Prompt template is the segment_definitions description; fields are fixed per the agreed schema.
_SEGMENT_DESCRIPTION = (
    "Describe this video in 15-second windows from start to finish. "
    "Cover the entire video with no gaps. For each window describe what is happening — "
    "including gameplay events, audio announcements, and what is visible on screen."
)


class PegasusDescribeError(Exception):
    pass


async def describe_video(
    asset_id: str,
    source_video: SourceVideo,
    info: VideoInfo,
    session: AsyncSession,
    settings: Settings,
    prompt_path: Path | None = None,
) -> list[SegmentDescription]:
    """
    Calls Pegasus analyze_async on the given asset, polls until complete, parses the
    15-second window output, and persists SegmentDescription rows.  Returns the saved rows.
    """
    description_text = _SEGMENT_DESCRIPTION
    if prompt_path is not None and prompt_path.exists():
        description_text = prompt_path.read_text(encoding="utf-8").strip()

    result_data, output_tokens = await asyncio.wait_for(
        asyncio.to_thread(_call_analyze, asset_id, description_text, settings),
        timeout=_ANALYZE_TIMEOUT_SECONDS,
    )

    windows = _parse_windows(result_data)
    log.info(
        "pegasus_describe_complete",
        source_id=source_video.id,
        asset_id=asset_id,
        windows=len(windows),
        output_tokens=output_tokens,
    )

    rows: list[SegmentDescription] = []
    for w in windows:
        row = SegmentDescription(
            source_video_id=source_video.id,
            start_seconds=w["start_seconds"],
            end_seconds=w["end_seconds"],
            description=w["description"],
            announcements=w.get("announcements"),
            events=w.get("events"),
            entities=w.get("entities"),
        )
        session.add(row)
        rows.append(row)

    await session.flush()

    video_minutes = Decimal(str(round(info.duration_minutes, 4)))
    await record_pegasus_describe(
        session=session,
        source_video_id=source_video.id,
        video_minutes=video_minutes,
        output_tokens=output_tokens,
    )

    return rows


def _call_analyze(
    asset_id: str, description_text: str, settings: Settings
) -> tuple[str, int]:
    """
    Synchronous — runs in a thread pool.
    Returns (raw_data_json_string, output_tokens).
    """
    client = TwelveLabs(api_key=settings.TWELVELABS_API_KEY)

    task = client.analyze_async.tasks.create(
        model_name="pegasus1.5",
        video=VideoContext_AssetId(asset_id=asset_id),
        analysis_mode="time_based_metadata",
        response_format=AsyncResponseFormat(
            type="segment_definitions",
            segment_definitions=[
                SegmentDefinition(
                    id="segment_windows",
                    description=description_text,
                    fields=[
                        SegmentField(
                            name="start",
                            type="string",
                            description="Start time of the 15-second window in seconds.",
                        ),
                        SegmentField(
                            name="end",
                            type="string",
                            description="End time of the 15-second window in seconds.",
                        ),
                        SegmentField(
                            name="description",
                            type="string",
                            description=(
                                "One-to-two sentence description of what is happening in this "
                                "window including gameplay events and audio. Be specific — name "
                                "map locations, agent abilities, and outcomes. "
                                "Do not repeat generic phrases."
                            ),
                        ),
                        SegmentField(
                            name="announcements",
                            type="string",
                            description=(
                                "Comma-separated list of any in-game audio announcements or "
                                "on-screen callouts heard or seen in this window. Examples: "
                                "'Ace', 'Spike planted', 'Spike defused', 'Round won', "
                                "'Round lost', 'Clutch', 'First blood', 'Flawless', 'Thrifty', "
                                "'Comeback'. Write 'None' if no announcements occur."
                            ),
                        ),
                        SegmentField(
                            name="events",
                            type="string",
                            description=(
                                "Comma-separated list of discrete events that occur in this "
                                "window. Examples: 'Kill: Reyna eliminated Brimstone', "
                                "'Player died', 'Ability used: Dismiss', 'Spike planted', "
                                "'Rotation only'. If nothing notable happens write 'Rotation only'."
                            ),
                        ),
                        SegmentField(
                            name="entities",
                            type="string",
                            description=(
                                "Comma-separated list of specific visual entities visible on "
                                "screen: agent names, weapons, UI elements (kill feed, scoreboard, "
                                "minimap, round timer), overlays (webcam, health bar)."
                            ),
                        ),
                    ],
                )
            ],
        ),
        temperature=0.2,
        max_tokens=_MAX_OUTPUT_TOKENS,
    )

    task_id: str = task.id  # type: ignore[attr-defined]
    log.info("pegasus_analyze_task_created", task_id=task_id, asset_id=asset_id)

    deadline = time.monotonic() + _ANALYZE_TIMEOUT_SECONDS
    while True:
        current = client.analyze_async.tasks.retrieve(task_id)
        status = getattr(current, "status", "unknown")
        log.info("pegasus_analyze_poll", task_id=task_id, status=status)

        if status == "ready":
            result = current.result
            if result is None:
                raise PegasusDescribeError(f"Pegasus task {task_id} ready but result is None")
            data: str = result.data
            usage = result.usage
            output_tokens: int = usage.output_tokens if usage is not None else 0
            return data, output_tokens

        if status == "failed":
            raise PegasusDescribeError(f"Pegasus analyze task {task_id} failed")

        if time.monotonic() >= deadline:
            raise PegasusDescribeError(
                f"Pegasus analyze task {task_id} timed out after {_ANALYZE_TIMEOUT_SECONDS}s"
            )

        time.sleep(_POLL_INTERVAL_SECONDS)


def _parse_windows(raw_data: str) -> list[dict[str, Any]]:
    """
    Parses the JSON string returned by Pegasus into a list of normalized window dicts.
    Each dict has: start_seconds, end_seconds, description, announcements, events, entities.
    """
    try:
        parsed = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise PegasusDescribeError(f"Pegasus returned invalid JSON: {exc}") from exc

    # Result is {"segment_windows": [...]} where each item has start_time, end_time, metadata
    raw_windows = parsed.get("segment_windows", [])
    if not raw_windows:
        log.warning("pegasus_describe_no_windows", raw_keys=list(parsed.keys()))
        return []

    windows: list[dict[str, Any]] = []
    for item in raw_windows:
        meta = item.get("metadata", {})
        try:
            start = float(meta.get("start", item.get("start_time", 0)))
            end = float(meta.get("end", item.get("end_time", 0)))
        except (TypeError, ValueError):
            log.warning("pegasus_window_bad_times", item=item)
            continue

        windows.append(
            {
                "start_seconds": start,
                "end_seconds": end,
                "description": str(meta.get("description", "")),
                "announcements": meta.get("announcements") or None,
                "events": meta.get("events") or None,
                "entities": meta.get("entities") or None,
            }
        )

    return windows
