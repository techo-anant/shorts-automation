import asyncio
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from twelvelabs import TooManyRequestsError, TwelveLabs

from src.config import Settings
from src.db.models import SourceVideo, VideoIndex
from src.errors import PegasusIndexError, PegasusQuotaError, PegasusVideoError

log = structlog.get_logger(__name__)

# TwelveLabs error codes that indicate quota / billing issues (not retryable today).
_TL_QUOTA_CODES = frozenset({
    "usage_limit_exceeded",
    "api_allowed_for_paid_plan",
    "token_limit_exceeded",
    "too_many_requests",
})

# TwelveLabs error codes that mean the video file itself is invalid or unsupported.
_TL_VIDEO_CODES = frozenset({
    "video_resolution_too_low",
    "video_resolution_too_high",
    "video_resolution_invalid_aspect_ratio",
    "video_file_broken",
    "video_file_live",
    "video_filesize_too_large",
    "video_duration_too_short",
    "video_duration_too_long",
    "media_url_unsupported_format",
    "media_url_file_broken",
    "media_filesize_too_large",
})

# Multipart upload is synchronous in the SDK; 30 min covers very large files.
_UPLOAD_TIMEOUT_SECONDS = 30 * 60


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
        raise PegasusIndexError(
            f"Pegasus upload timed out after {_UPLOAD_TIMEOUT_SECONDS}s "
            f"for source {source_video.id}"
        ) from err

    video_index.index_id = asset_id
    video_index.status = "ready"
    await session.flush()

    video_path.unlink(missing_ok=True)
    log.info("local_video_deleted_after_upload", source_id=source_video.id)

    return video_index


def _classify_upload_error(exc: Exception) -> PegasusIndexError:
    """Map a raw TwelveLabs SDK exception from the upload step to the correct typed error."""
    err_str = str(exc)
    for code in _TL_QUOTA_CODES:
        if code in err_str:
            return PegasusQuotaError(f"TwelveLabs quota/billing issue during upload: {exc}")
    for code in _TL_VIDEO_CODES:
        if code in err_str:
            return PegasusVideoError(f"Video rejected by TwelveLabs: {exc}")
    return PegasusIndexError(f"Pegasus upload failed: {exc}")


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
    try:
        result = client.multipart_upload.upload_file(  # type: ignore[attr-defined]
            video_path,
            filename=video_name,
            progress_callback=_on_progress,
        )
    except TooManyRequestsError as err:
        raise PegasusQuotaError(f"TwelveLabs rate limit during upload: {err}") from err
    except Exception as err:
        raise _classify_upload_error(err) from err
    log.info("pegasus_upload_complete", asset_id=result.asset_id)
    return str(result.asset_id)
