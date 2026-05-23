"""
Manual facecam crop request flow (Phase 15).

When a source video has a confirmed facecam (has_facecam=True, facecam_unstable=False),
the operator is asked to supply the exact crop rectangle via Discord reply. This module
handles:
  1. request_manual_crop  — extract middle frame, upload to Drive, post Discord message.
  2. check_crop_requests  — poll Discord for replies; re-queue resolved sources as pending.
  3. parse_crop_coords    — extract (x, y, w, h) from a Discord reply string.
"""

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.approval import discord_approval
from src.config import Settings
from src.db.models import CropRequest, SourceVideo
from src.db.session import get_session_factory
from src.drive.client import DriveClient
from src.utils.ffmpeg import VideoInfo, run_ffmpeg

log = structlog.get_logger(__name__)

_REPLY_INSTRUCTIONS = (
    "Reply to **this message** with the facecam crop rectangle in pixels:\n"
    "`{\"x\": 100, \"y\": 50, \"w\": 480, \"h\": 270}`\n"
    "Open `tools/facecam_crop_tool.html` locally and load the frame URL to measure coordinates."
)


async def request_manual_crop(
    session: AsyncSession,
    source_video: SourceVideo,
    video_path: Path,
    info: VideoInfo,
    drive_client: DriveClient,
    settings: Settings,
) -> CropRequest:
    """
    Extract the mid-point frame from video_path, upload it to Drive (same folder as the
    source video), post a Discord message with the frame link and crop instructions, and
    persist a new CropRequest(status="pending"). Caller must commit the session.
    """
    mid_ts = min(info.duration_seconds * 0.50, max(info.duration_seconds - 0.5, 0.0))

    with tempfile.TemporaryDirectory() as tmp:
        frame_path = Path(tmp) / "crop_reference.jpg"
        await run_ffmpeg([
            "-ss", f"{mid_ts:.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "4",
            "-y",
            str(frame_path),
        ])

        parent_ids = await drive_client.get_parents(source_video.drive_file_id)
        parent_id = parent_ids[0] if parent_ids else settings.DRIVE_SHORTS_ROOT_FOLDER_ID

        frame_name = f"{source_video.name}_crop_ref.jpg"
        drive_file_id = await drive_client.upload_file(
            frame_path, frame_name, parent_id, mime_type="image/jpeg"
        )

    frame_url = await drive_client.make_public(drive_file_id)

    content = (
        f"\U0001f4f8 **Facecam crop required:** {source_video.name}\n"
        f"Frame (right-click → open in new tab, or use the crop tool): {frame_url}\n\n"
        f"{_REPLY_INSTRUCTIONS}\n\n"
        f"You have {settings.CROP_TIMEOUT_HOURS}h — after that auto-detection is used."
    )
    discord_message_id = await discord_approval.post_message(
        channel_id=settings.DISCORD_APPROVAL_CHANNEL_ID,
        bot_token=settings.DISCORD_BOT_TOKEN,
        content=content,
    )

    expires_at = datetime.now(tz=UTC) + timedelta(hours=settings.CROP_TIMEOUT_HOURS)
    crop_request = CropRequest(
        source_video_id=source_video.id,
        discord_message_id=discord_message_id,
        drive_frame_file_id=drive_file_id,
        status="pending",
        ref_width=info.width,
        ref_height=info.height,
        expires_at=expires_at,
    )
    session.add(crop_request)

    log.info(
        "crop_request_created",
        source_id=source_video.id,
        discord_message_id=discord_message_id,
        expires_at=expires_at.isoformat(),
    )
    return crop_request


def parse_crop_coords(reply_text: str) -> tuple[int, int, int, int] | None:
    """
    Extract {x, y, w, h} from a Discord reply string.
    Finds the first JSON object in the text. Returns (x, y, w, h) or None if invalid.
    """
    start = reply_text.find("{")
    end = reply_text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(reply_text[start:end])
    except json.JSONDecodeError:
        return None
    try:
        x = int(data["x"])
        y = int(data["y"])
        w = int(data["w"])
        h = int(data["h"])
    except (KeyError, ValueError, TypeError):
        return None
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        return None
    return x, y, w, h


async def check_crop_requests(settings: Settings) -> None:
    """
    Poll Discord replies for all waiting_for_crop sources. When the operator has replied
    with valid JSON coords (or the timeout has passed), mark the CropRequest resolved and
    reset the source to pending so the pipeline picks it up again.
    """
    now = datetime.now(tz=UTC)

    async with get_session_factory()() as session:
        result = await session.execute(
            select(SourceVideo, CropRequest)
            .join(CropRequest, CropRequest.source_video_id == SourceVideo.id)
            .where(SourceVideo.status == "waiting_for_crop")
            .where(CropRequest.status == "pending")
        )
        rows = list(result.all())

    for source_video, crop_req in rows:
        if crop_req.expires_at <= now:
            async with get_session_factory()() as session:
                await session.execute(
                    update(CropRequest)
                    .where(CropRequest.id == crop_req.id)
                    .values(status="timed_out")
                )
                await session.execute(
                    update(SourceVideo)
                    .where(SourceVideo.id == source_video.id)
                    .values(status="pending")
                )
                await session.commit()
            log.info("crop_request_timed_out", source_id=source_video.id)
            continue

        try:
            replies = await discord_approval.get_message_replies(
                channel_id=settings.DISCORD_APPROVAL_CHANNEL_ID,
                message_id=crop_req.discord_message_id,
                bot_token=settings.DISCORD_BOT_TOKEN,
            )
        except Exception:
            log.exception("crop_reply_poll_failed", source_id=source_video.id)
            continue

        coords = None
        for reply in replies:
            coords = parse_crop_coords(reply)
            if coords:
                break

        if coords:
            x, y, w, h = coords
            async with get_session_factory()() as session:
                await session.execute(
                    update(CropRequest)
                    .where(CropRequest.id == crop_req.id)
                    .values(status="provided", crop_x=x, crop_y=y, crop_w=w, crop_h=h)
                )
                await session.execute(
                    update(SourceVideo)
                    .where(SourceVideo.id == source_video.id)
                    .values(status="pending")
                )
                await session.commit()
            log.info(
                "crop_request_provided",
                source_id=source_video.id,
                x=x, y=y, w=w, h=h,
            )
