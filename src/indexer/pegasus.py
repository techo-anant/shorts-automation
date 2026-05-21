import asyncio
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from twelvelabs import TwelveLabs

from src.config import Settings
from src.db.models import SourceVideo, VideoIndex

log = structlog.get_logger(__name__)

# Multipart upload is synchronous in the SDK; 30 min covers very large files.
_UPLOAD_TIMEOUT_SECONDS = 30 * 60


class PegasusUploadError(Exception):
    pass


async def get_or_upload_asset(
    session: AsyncSession,
    source_video: SourceVideo,
    video_path: Path,
    settings: Settings,
) -> VideoIndex:
    """
    Idempotency-safe: returns an existing ready VideoIndex for this source video if one exists.
    Otherwise uploads via TwelveLabs multipart, stores the asset_id in a new VideoIndex row,
    then deletes the local file (TwelveLabs retains its own copy).

    The asset_id stored in VideoIndex.index_id is passed to analyze_async in pegasus_describe.py.
    No cost event here — the cost is on the analyze call, not the upload.
    """
    now = datetime.now(tz=UTC)

    existing = await session.execute(
        select(VideoIndex)
        .where(VideoIndex.source_video_id == source_video.id)
        .where(VideoIndex.provider == "pegasus")
        .where(VideoIndex.status == "ready")
        .limit(1)
    )
    existing_index = existing.scalar_one_or_none()

    if existing_index is not None:
        if existing_index.expires_at is not None and existing_index.expires_at <= now:
            log.info(
                "pegasus_asset_expired_inline",
                source_id=source_video.id,
                asset_id=existing_index.index_id,
            )
            existing_index.status = "expired"
            await session.flush()
        else:
            log.info(
                "pegasus_asset_reused",
                source_id=source_video.id,
                asset_id=existing_index.index_id,
            )
            return existing_index

    video_index = VideoIndex(
        source_video_id=source_video.id,
        provider="pegasus",
        index_id="",
        status="indexing",
    )
    session.add(video_index)
    await session.flush()

    try:
        asset_id = await asyncio.wait_for(
            asyncio.to_thread(_upload, source_video.name, video_path, settings),
            timeout=_UPLOAD_TIMEOUT_SECONDS,
        )
    except TimeoutError as err:
        video_index.status = "failed"
        await session.flush()
        raise PegasusUploadError(
            f"Pegasus upload timed out after {_UPLOAD_TIMEOUT_SECONDS}s "
            f"for source {source_video.id}"
        ) from err

    video_index.index_id = asset_id
    video_index.status = "ready"
    await session.flush()

    return video_index


def _upload(video_name: str, video_path: Path, settings: Settings) -> str:
    """
    Synchronous — runs in a thread pool via asyncio.to_thread.
    Uploads the video via TwelveLabs multipart upload and returns the asset_id.
    """
    client = TwelveLabs(api_key=settings.TWELVELABS_API_KEY)

    def _on_progress(p: object) -> None:
        log.info(
            "pegasus_upload_progress",
            pct=round(getattr(p, "percentage", 0.0), 1),
            chunks=f"{getattr(p, 'completed_chunks', '?')}/{getattr(p, 'total_chunks', '?')}",
        )

    log.info("pegasus_uploading_video", path=str(video_path))
    result = client.multipart_upload.upload_file(  # type: ignore[attr-defined]
        video_path,
        filename=video_name,
        progress_callback=_on_progress,
    )
    log.info("pegasus_upload_complete", asset_id=result.asset_id)
    return str(result.asset_id)
