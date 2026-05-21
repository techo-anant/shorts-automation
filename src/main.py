import asyncio
import contextlib
import logging
import signal
import sys
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update

from src.approval.gate import check_pending_approvals, upload_approved_shorts
from src.config import Settings, get_settings
from src.db.models import Short, SourceVideo, VideoIndex
from src.db.session import get_session_factory
from src.drive.client import DriveClient
from src.health.server import start_health_server
from src.notifications.discord import send_message
from src.workflow.processor import process_source_video

log = structlog.get_logger(__name__)

_KNOWN_CATEGORIES = frozenset({"gameplay", "tutorial", "podcast"})
_INDEX_EXPIRY_WARN_DAYS = 7


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if sys.stdout.isatty()
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.ExceptionRenderer(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


async def sweep_expired_indexes(settings: Settings) -> None:
    """
    Run each poll cycle. Marks VideoIndex rows as 'expired' if their expiry has passed,
    and sends a Discord warning for indexes expiring within the next 7 days.
    """
    now = datetime.now(tz=UTC)
    warn_threshold = now + timedelta(days=_INDEX_EXPIRY_WARN_DAYS)

    async with get_session_factory()() as session:
        result = await session.execute(
            select(VideoIndex)
            .where(VideoIndex.status == "ready")
            .where(VideoIndex.expires_at.is_not(None))
        )
        indexes = result.scalars().all()

        for idx in indexes:
            assert idx.expires_at is not None  # guaranteed by the WHERE clause above
            if idx.expires_at <= now:
                idx.status = "expired"
                log.info(
                    "video_index_marked_expired",
                    source_id=idx.source_video_id,
                    index_id=idx.index_id,
                )
            elif idx.expires_at <= warn_threshold:
                log.warning(
                    "video_index_expiring_soon",
                    source_id=idx.source_video_id,
                    index_id=idx.index_id,
                    expires_at=idx.expires_at.isoformat(),
                )
                await send_message(
                    settings.DISCORD_WEBHOOK_URL,
                    f"Warning: TwelveLabs index `{idx.index_id}` (source {idx.source_video_id})"
                    f" expires {idx.expires_at.date()} — within {_INDEX_EXPIRY_WARN_DAYS} days.",
                )

        await session.commit()


async def reset_stuck_processing_rows() -> None:
    """On startup, reset any rows left in processing from a previous crash."""
    async with get_session_factory()() as session:
        result = await session.execute(
            update(SourceVideo)
            .where(SourceVideo.status == "processing")
            .values(status="pending")
            .returning(SourceVideo.id)
        )
        reset_ids = [row[0] for row in result.fetchall()]

        # Shorts stuck mid-cut or mid-upload go back to pending so Phase 8/10 retry them.
        # Shorts in pending_approval stay — the operator's reaction is durable state.
        # Shorts in approved stay — they'll be picked up by Phase 10 on the next tick.
        short_result = await session.execute(
            update(Short)
            .where(Short.status.in_(["cutting", "uploading"]))
            .values(status="pending")
            .returning(Short.id)
        )
        reset_short_ids = [row[0] for row in short_result.fetchall()]

        await session.commit()

    if reset_ids:
        log.info("reset_stuck_source_videos", ids=reset_ids)
    if reset_short_ids:
        log.info("reset_stuck_shorts", ids=reset_short_ids)


async def poll_once(drive_client: DriveClient, settings: Settings) -> None:
    await sweep_expired_indexes(settings)
    await check_pending_approvals(settings)
    await upload_approved_shorts(drive_client, settings)

    # Discover to-process/ under the configured root folder
    root_folders = await drive_client.list_subfolders(settings.DRIVE_SHORTS_ROOT_FOLDER_ID)
    to_process = next((f for f in root_folders if f.name == "to-process"), None)
    if to_process is None:
        log.warning("to_process_folder_missing", root_id=settings.DRIVE_SHORTS_ROOT_FOLDER_ID)
        return

    category_folders = await drive_client.list_subfolders(to_process.id)
    new_count = 0

    async with get_session_factory()() as session:
        for cat in category_folders:
            category = cat.name
            if category not in _KNOWN_CATEGORIES:
                log.warning("unknown_category_folder", name=category)
                await send_message(
                    settings.DISCORD_WEBHOOK_URL,
                    f"Warning: unknown Drive folder `{category}` in to-process/ — treating as `generic`.",
                )
                category = "generic"

            videos = await drive_client.list_videos_in(cat.id)
            for video in videos:
                existing = await session.execute(
                    select(SourceVideo).where(SourceVideo.drive_file_id == video.id)
                )
                if existing.scalar_one_or_none() is not None:
                    continue

                session.add(
                    SourceVideo(
                        drive_file_id=video.id,
                        name=video.name,
                        category=category,
                        status="pending",
                        size_bytes=video.size_bytes,
                    )
                )
                new_count += 1
                log.info(
                    "source_video_enqueued",
                    file_id=video.id,
                    name=video.name,
                    category=category,
                )

        if new_count:
            await session.commit()

    # Pick up the oldest pending row and hand it to the workflow
    async with get_session_factory()() as session:
        result = await session.execute(
            select(SourceVideo)
            .where(SourceVideo.status == "pending")
            .order_by(SourceVideo.created_at)
            .limit(1)
        )
        pending = result.scalar_one_or_none()
        if pending is None:
            return

        await session.execute(
            update(SourceVideo).where(SourceVideo.id == pending.id).values(status="processing")
        )
        await session.commit()

    await process_source_video(pending, drive_client, settings)


async def polling_loop(
    stop_event: asyncio.Event, drive_client: DriveClient, settings: Settings
) -> None:
    while not stop_event.is_set():
        try:
            await poll_once(drive_client, settings)
        except Exception:
            log.exception("poll_tick_error")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                stop_event.wait(), timeout=float(settings.POLL_INTERVAL_SECONDS)
            )


async def main() -> None:
    configure_logging()
    settings = get_settings()

    log.info("service_starting", poll_interval=settings.POLL_INTERVAL_SECONDS)

    health_runner = await start_health_server(settings.PORT)
    log.info("health_server_started", port=settings.PORT)

    await reset_stuck_processing_rows()

    await send_message(settings.DISCORD_WEBHOOK_URL, "Shorts automation service started.")
    log.info("startup_discord_ping_sent")

    drive_client = DriveClient(settings)

    stop_event = asyncio.Event()
    event_loop = asyncio.get_running_loop()
    try:
        # add_signal_handler is Unix-only; local Windows dev falls back to KeyboardInterrupt
        for sig in (signal.SIGTERM, signal.SIGINT):
            event_loop.add_signal_handler(sig, stop_event.set)
    except (NotImplementedError, AttributeError):
        pass

    log.info("polling_loop_starting")
    try:
        await polling_loop(stop_event, drive_client, settings)
    finally:
        await health_runner.cleanup()
        log.info("service_stopped")


if __name__ == "__main__":
    asyncio.run(main())
