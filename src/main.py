import asyncio
import contextlib
import logging
import signal
import sys

import structlog
from sqlalchemy import update

from src.config import get_settings
from src.db.models import SourceVideo
from src.db.session import get_session_factory
from src.health.server import start_health_server
from src.notifications.discord import send_message

log = structlog.get_logger(__name__)


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


async def poll_once() -> None:
    """Single poll tick — placeholder until Phase 2 wires up Drive + workflow."""
    log.debug("poll_tick")


async def polling_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        try:
            await poll_once()
        except Exception:
            log.exception("poll_tick_error")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=float(settings.POLL_INTERVAL_SECONDS))


async def main() -> None:
    configure_logging()
    settings = get_settings()

    log.info("service_starting", poll_interval=settings.POLL_INTERVAL_SECONDS)

    health_runner = await start_health_server(settings.PORT)
    log.info("health_server_started", port=settings.PORT)

    await reset_stuck_processing_rows()

    await send_message(settings.DISCORD_WEBHOOK_URL, "Shorts automation service started.")
    log.info("startup_discord_ping_sent")

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
        await polling_loop(stop_event)
    finally:
        await health_runner.cleanup()
        log.info("service_stopped")


if __name__ == "__main__":
    asyncio.run(main())
