import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from twelvelabs import TwelveLabs

from src.config import Settings
from src.costs.record import record_marengo_index
from src.db.models import SourceVideo, VideoIndex
from src.utils.ffmpeg import VideoInfo

log = structlog.get_logger(__name__)

# If the TwelveLabs task does not become ready within this window, raise.
_INDEX_TIMEOUT_SECONDS = 25 * 60  # 25 minutes
_POLL_INTERVAL_SECONDS = 15


class MarengoIndexError(Exception):
    pass


async def get_or_create_index(
    session: AsyncSession,
    source_video: SourceVideo,
    video_path: Path,
    info: VideoInfo,
    settings: Settings,
) -> VideoIndex:
    """
    Idempotency-safe: returns the existing VideoIndex if this source was already
    indexed successfully and the index has not expired. Otherwise creates a new
    Marengo index, uploads the video, polls until ready, writes the VideoIndex row
    + CostEvent, then deletes the local file (TwelveLabs now holds its own copy).
    """
    now = datetime.now(tz=UTC)

    existing = await session.execute(
        select(VideoIndex)
        .where(VideoIndex.source_video_id == source_video.id)
        .where(VideoIndex.provider == "marengo")
        .where(VideoIndex.status == "ready")
        .limit(1)
    )
    existing_index = existing.scalar_one_or_none()

    if existing_index is not None:
        if existing_index.expires_at is not None and existing_index.expires_at <= now:
            # Already expired — mark it and fall through to create a fresh one
            log.info(
                "video_index_expired_inline",
                source_id=source_video.id,
                index_id=existing_index.index_id,
                expired_at=existing_index.expires_at.isoformat(),
            )
            existing_index.status = "expired"
            await session.flush()
        else:
            log.info(
                "marengo_index_reused",
                source_id=source_video.id,
                index_id=existing_index.index_id,
            )
            return existing_index

    # Create the VideoIndex row before touching TwelveLabs so a crash mid-upload
    # leaves a record we can inspect.
    video_index = VideoIndex(
        source_video_id=source_video.id,
        provider="marengo",
        index_id="",
        status="indexing",
    )
    session.add(video_index)
    await session.flush()

    try:
        index_id, expires_at = await asyncio.wait_for(
            asyncio.to_thread(_create_and_upload, source_video.name, video_path, settings),
            timeout=_INDEX_TIMEOUT_SECONDS,
        )
    except TimeoutError as err:
        video_index.status = "failed"
        await session.flush()
        raise MarengoIndexError(
            f"Marengo indexing timed out after {_INDEX_TIMEOUT_SECONDS}s "
            f"for source {source_video.id}"
        ) from err

    video_index.index_id = index_id
    video_index.expires_at = expires_at
    video_index.status = "ready"
    await session.flush()

    video_minutes = Decimal(str(round(info.duration_minutes, 4)))
    await record_marengo_index(
        session=session,
        source_video_id=source_video.id,
        video_minutes=video_minutes,
    )

    # TwelveLabs has its own copy — safe to remove the local file now
    video_path.unlink(missing_ok=True)
    log.info("local_video_deleted_after_index", source_id=source_video.id)

    return video_index


def _create_and_upload(
    video_name: str, video_path: Path, settings: Settings
) -> tuple[str, datetime | None]:
    """
    Synchronous — runs in a thread pool via asyncio.to_thread.
    Creates a Marengo 3.0 index, uploads the video, polls until ready, then fetches
    expires_at from the index metadata (not present in the create response).
    Returns (index_id, expires_at) where expires_at may be None on accounts without expiry.

    Verified against twelvelabs SDK 1.2.4 (2026-05).
    """
    client = TwelveLabs(api_key=settings.TWELVELABS_API_KEY)

    log.info("marengo_creating_index", video_name=video_name)
    index = client.indexes.create(
        index_name=f"shorts-{video_name[:80]}",
        models=[{"model_name": "marengo3.0", "model_options": ["visual", "audio"]}],
    )
    # SDK types index.id as str | None but it is always set on a successful create response.
    assert index.id is not None, "TwelveLabs returned None index.id after index creation"
    index_id: str = index.id
    log.info("marengo_index_created", index_id=index_id)

    def _on_upload_progress(p: object) -> None:
        log.info(
            "marengo_upload_progress",
            pct=round(getattr(p, "percentage", 0.0), 1),
            chunks=f"{getattr(p, 'completed_chunks', '?')}/{getattr(p, 'total_chunks', '?')}",
        )

    log.info("marengo_uploading_video", path=str(video_path))
    upload_result = client.multipart_upload.upload_file(  # type: ignore[attr-defined]
        video_path,
        filename=video_name,
        progress_callback=_on_upload_progress,
    )
    log.info("marengo_upload_complete", asset_id=upload_result.asset_id)

    task = client.tasks.create(index_id=index_id, video_url=upload_result.asset_url)
    log.info("marengo_task_created", task_id=task.id)

    def _on_progress(t: object) -> None:
        log.info(
            "marengo_index_progress",
            task_id=getattr(t, "id", "?"),
            status=getattr(t, "status", "?"),
        )

    final_task = client.tasks.wait_for_done(
        task.id, sleep_interval=_POLL_INTERVAL_SECONDS, callback=_on_progress
    )

    if final_task.status != "ready":
        raise MarengoIndexError(
            f"Marengo task {task.id} ended with unexpected status: {final_task.status}"
        )

    log.info("marengo_index_ready", index_id=index_id, video_id=final_task.video_id)

    # expires_at is not in the create response — must retrieve the index metadata
    index_meta = client.indexes.retrieve(index_id)
    expires_at: datetime | None = None
    raw_expires = getattr(index_meta, "expires_at", None)
    if raw_expires:
        expires_at = datetime.fromisoformat(raw_expires.replace("Z", "+00:00"))

    return index_id, expires_at
