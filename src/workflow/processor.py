import tempfile
from decimal import Decimal
from pathlib import Path

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.costs.record import get_source_total_cost
from src.db.models import SourceVideo
from src.db.session import get_session_factory
from src.drive.client import DriveClient
from src.ingest.format_probe import run_format_probe
from src.notifications.discord import send_error, send_message
from src.utils.ffmpeg import probe_video

log = structlog.get_logger(__name__)


class CostBudgetExceeded(Exception):
    def __init__(self, source_video_id: int, total_usd: Decimal) -> None:
        self.source_video_id = source_video_id
        self.total_usd = total_usd
        super().__init__(f"Cost hard-stop for source {source_video_id}: ${total_usd:.4f}")


async def check_cost_budget(
    session: AsyncSession, source_video_id: int, settings: Settings
) -> None:
    """Raise CostBudgetExceeded if this source has hit the hard-stop threshold."""
    total = await get_source_total_cost(session, source_video_id)
    if total >= settings.COST_HARD_STOP_USD:
        raise CostBudgetExceeded(source_video_id, total)


async def _mark_status(
    source_video_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    async with get_session_factory()() as session:
        values: dict = {"status": status, "local_path": None}
        if error_message is not None:
            values["error_message"] = error_message
        await session.execute(
            update(SourceVideo).where(SourceVideo.id == source_video_id).values(**values)
        )
        await session.commit()


async def _move_to_failed(
    source_video: SourceVideo,
    drive_client: DriveClient,
    settings: Settings,
) -> None:
    """Best-effort move of source file to failed/<category>/. Logs but does not raise."""
    try:
        failed_root = await drive_client.get_or_create_folder(
            "failed", settings.DRIVE_SHORTS_ROOT_FOLDER_ID
        )
        failed_folder = await drive_client.get_or_create_folder(
            source_video.category, failed_root
        )
        parents = await drive_client.get_parents(source_video.drive_file_id)
        current_parent = parents[0] if parents else ""
        await drive_client.move(source_video.drive_file_id, failed_folder, current_parent)
    except Exception:
        log.exception("failed_folder_move_error", source_id=source_video.id)


async def process_source_video(
    source_video: SourceVideo,
    drive_client: DriveClient,
    settings: Settings,
) -> None:
    log.info(
        "processing_source_video",
        source_id=source_video.id,
        name=source_video.name,
        category=source_video.category,
    )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / source_video.name

            await drive_client.download(source_video.drive_file_id, local_path)

            async with get_session_factory()() as session:
                await session.execute(
                    update(SourceVideo)
                    .where(SourceVideo.id == source_video.id)
                    .values(local_path=str(local_path))
                )
                await session.commit()

            # Format probe — first priced step
            info = await probe_video(local_path)
            async with get_session_factory()() as probe_session:
                await check_cost_budget(probe_session, source_video.id, settings)
                await run_format_probe(
                    session=probe_session,
                    source_video=source_video,
                    video_path=local_path,
                    info=info,
                    settings=settings,
                )
                await probe_session.commit()

            # --- Phase 4: Pegasus index + query goes here ---

            processed_root = await drive_client.get_or_create_folder(
                "processed", settings.DRIVE_SHORTS_ROOT_FOLDER_ID
            )
            category_folder = await drive_client.get_or_create_folder(
                source_video.category, processed_root
            )
            parents = await drive_client.get_parents(source_video.drive_file_id)
            current_parent = parents[0] if parents else ""
            await drive_client.move(source_video.drive_file_id, category_folder, current_parent)

            # local_path deleted automatically when TemporaryDirectory exits

        await _mark_status(source_video.id, "completed")
        log.info("source_video_completed", source_id=source_video.id, name=source_video.name)

        if settings.DISCORD_SUCCESS_PINGS:
            await send_message(
                settings.DISCORD_WEBHOOK_URL,
                f"Completed: **{source_video.name}** [{source_video.category}] → processed/",
            )

    except CostBudgetExceeded as exc:
        log.error(
            "cost_budget_exceeded",
            source_id=source_video.id,
            total_usd=str(exc.total_usd),
        )
        await _mark_status(source_video.id, "failed", error_message=str(exc))
        await send_error(
            settings.DISCORD_WEBHOOK_URL,
            title=f"Cost hard-stop: {source_video.name}",
            description=(
                f"Total ${exc.total_usd:.4f} hit hard-stop"
                f" (limit: ${settings.COST_HARD_STOP_USD}). Source moved to failed/."
            ),
        )
        await _move_to_failed(source_video, drive_client, settings)

    except Exception as exc:
        log.exception("source_video_failed", source_id=source_video.id, name=source_video.name)
        await _mark_status(source_video.id, "failed", error_message=repr(exc))
        await send_error(
            settings.DISCORD_WEBHOOK_URL,
            title=f"Pipeline error: {source_video.name}",
            description=f"{type(exc).__name__}: {exc}",
        )
        await _move_to_failed(source_video, drive_client, settings)
