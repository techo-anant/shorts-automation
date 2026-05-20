import asyncio
import contextlib
import logging
import signal
import sys

import structlog
from sqlalchemy import select, update

from src.config import Settings, get_settings
from src.db.models import SourceVideo
from src.db.session import get_session_factory
from src.drive.client import DriveClient
from src.health.server import start_health_server
from src.notifications.discord import send_message
from src.workflow.processor import process_source_video

log = structlog.get_logger(__name__)

_KNOWN_CATEGORIES = frozenset({"gameplay", "tutorial", "podcast"})


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
        await session.commit()
    if reset_ids:
        log.info("reset_stuck_processing_rows", ids=reset_ids)


async def poll_once(drive_client: DriveClient, settings: Settings) -> None:
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
