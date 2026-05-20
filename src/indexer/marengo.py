import asyncio
from decimal import Decimal
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from twelvelabs import TwelveLabs

from src.config import Settings
from src.costs.record import record_marengo_index
from src.db.models import MomentDetectionRun, SourceVideo
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
) -> MomentDetectionRun:
    """
    Idempotency-safe: returns the existing MomentDetectionRun if this source
    was already indexed successfully. Otherwise creates a Marengo index, uploads
    the video, polls until ready, writes the run row + CostEvent, then deletes
    the local file (TwelveLabs now holds its own copy).
    """
    existing = await session.execute(
        select(MomentDetectionRun)
        .where(MomentDetectionRun.source_video_id == source_video.id)
        .where(MomentDetectionRun.provider == "marengo")
        .where(MomentDetectionRun.index_id.is_not(None))
        .where(MomentDetectionRun.status.in_(["ready", "queried"]))
        .limit(1)
    )
    existing_run = existing.scalar_one_or_none()
    if existing_run is not None:
        log.info(
            "marengo_index_reused",
            source_id=source_video.id,
            index_id=existing_run.index_id,
        )
        return existing_run

    # Create the run row before touching TwelveLabs so a crash mid-upload
    # leaves a record we can inspect.
    run = MomentDetectionRun(
        source_video_id=source_video.id,
        provider="marengo",
        status="indexing",
    )
    session.add(run)
    await session.flush()

    try:
        index_id = await asyncio.wait_for(
            asyncio.to_thread(_create_and_upload, source_video.name, video_path, settings),
            timeout=_INDEX_TIMEOUT_SECONDS,
        )
    except TimeoutError as err:
        run.status = "failed"
        await session.flush()
        raise MarengoIndexError(
            f"Marengo indexing timed out after {_INDEX_TIMEOUT_SECONDS}s "
            f"for source {source_video.id}"
        ) from err

    run.index_id = index_id
    run.status = "ready"
    await session.flush()

    video_minutes = Decimal(str(round(info.duration_minutes, 4)))
    await record_marengo_index(
        session=session,
        source_video_id=source_video.id,
        detection_run_id=run.id,
        video_minutes=video_minutes,
    )

    # TwelveLabs has its own copy — safe to remove the local file now
    video_path.unlink(missing_ok=True)
    log.info("local_video_deleted_after_index", source_id=source_video.id)

    return run


def _create_and_upload(video_name: str, video_path: Path, settings: Settings) -> str:
    """
    Synchronous — runs in a thread pool via asyncio.to_thread.
    Creates a Marengo 3.0 index, uploads the video, and polls until ready.
    Returns the TwelveLabs index_id.

    NOTE: Verify SDK method signatures against the installed twelvelabs package
    version before the first production run.
    """
    client = TwelveLabs(api_key=settings.TWELVELABS_API_KEY)

    log.info("marengo_creating_index", video_name=video_name)
    index = client.index.create(
        name=f"shorts-{video_name[:80]}",
        models=[{"name": "marengo3.0", "options": ["visual", "audio"]}],
    )
    log.info("marengo_index_created", index_id=index.id)

    log.info("marengo_uploading_video", path=str(video_path))
    task = client.task.create(index_id=index.id, file=str(video_path))
    log.info("marengo_task_created", task_id=task.id)

    def _on_progress(task) -> None:  # type: ignore[no-untyped-def]
        log.info("marengo_index_progress", task_id=task.id, status=task.status)

    task.wait_for_done(sleep_interval=_POLL_INTERVAL_SECONDS, callback=_on_progress)

    if task.status != "ready":
        raise MarengoIndexError(
            f"Marengo task {task.id} ended with unexpected status: {task.status}"
        )

    log.info("marengo_index_ready", index_id=index.id, video_id=task.video_id)
    return index.id  # type: ignore[no-any-return]
