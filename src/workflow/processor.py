import tempfile
from decimal import Decimal
from pathlib import Path

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.approval.gate import submit_for_approval
from src.config import Settings
from src.costs.record import get_cost_breakdown, get_source_total_cost
from src.db.models import FormatProfile, Short, SourceVideo, VideoIndex
from src.db.session import get_session_factory
from src.drive.client import DriveClient
from src.ffmpeg.cut import cut_short
from src.ffmpeg.recipes import select_recipe
from src.indexer.pegasus import get_or_upload_asset
from src.ingest.format_probe import FormatProbeResult, run_format_probe
from src.moments.pipeline import run_clip_selection
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
    total = await get_source_total_cost(session, source_video_id)
    if total >= settings.COST_HARD_STOP_USD:
        raise CostBudgetExceeded(source_video_id, total)


async def _mark_status(
    source_video_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    async with get_session_factory()() as session:
        values: dict[str, object] = {"status": status, "local_path": None}
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


def _build_success_summary(
    source_video: SourceVideo,
    format_result: FormatProbeResult,
    shorts: list[Short],
    total_cost: Decimal,
    breakdown: dict[str, Decimal],
) -> str:
    facecam_str = "yes" if format_result.has_facecam else "no"
    if format_result.facecam_unstable:
        facecam_str += " (unstable)"

    probe_cost = breakdown.get("ingest.format_probe", Decimal("0"))
    describe_cost = breakdown.get("moments.pegasus_describe", Decimal("0"))
    select_cost = breakdown.get("moments.gemini_select", Decimal("0"))

    top_clips = sorted(
        [s for s in shorts if s.gemini_rank is not None],
        key=lambda s: s.gemini_rank or 0,
        reverse=True,
    )[:3]
    clips_summary = "\n".join(
        f"  Rank {s.gemini_rank}/10 [{s.cut_start_seconds:.0f}s–{s.cut_end_seconds:.0f}s]: "
        f"{s.gemini_rationale or '—'}"
        for s in top_clips
    )

    pending_approval = sum(1 for s in shorts if s.status == "pending_approval")

    lines = [
        f"**{source_video.name}** [{source_video.category}]",
        f"Facecam: {facecam_str} | Aspect: {format_result.source_aspect_ratio}",
        f"Shorts selected: {len(shorts)} | Pending approval: {pending_approval}",
        clips_summary,
        f"Cost: ${total_cost:.4f}  "
        f"(probe: ${probe_cost:.4f} | describe: ${describe_cost:.4f} | "
        f"select: ${select_cost:.4f})",
    ]
    return "\n".join(line for line in lines if line)


async def _cut_all_shorts(
    shorts: list[Short],
    source_video: SourceVideo,
    drive_client: DriveClient,
    tmpdir: str,
    settings: Settings,
) -> None:
    """
    Re-download the source video from Drive and cut every Short in the list.
    Updates each Short's status and local_path in DB.
    Source file is deleted after all cuts complete.
    """
    if not shorts:
        return

    local_path = Path(tmpdir) / source_video.name
    await drive_client.download(
        source_video.drive_file_id,
        local_path,
        expected_size=source_video.size_bytes,
    )

    async with get_session_factory()() as session:
        profile: FormatProfile | None = (
            await session.execute(
                select(FormatProfile).where(
                    FormatProfile.source_video_id == source_video.id
                )
            )
        ).scalar_one_or_none()

    if profile is None:
        log.warning(
            "cut_skipped_no_profile",
            source_id=source_video.id,
        )
        local_path.unlink(missing_ok=True)
        return

    recipe = select_recipe(source_video.category, profile, settings)
    info = await probe_video(local_path)
    log.info(
        "cutting_shorts",
        source_id=source_video.id,
        recipe=recipe.name,
        count=len(shorts),
    )

    for short in shorts:
        output_path = Path(tmpdir) / f"short_{short.id}.mp4"
        try:
            await cut_short(
                source_path=local_path,
                output_path=output_path,
                start_seconds=short.cut_start_seconds,
                end_seconds=short.cut_end_seconds,
                recipe=recipe,
                info=info,
            )
            short.format_recipe = recipe.name
            log.info("short_cut_complete", short_id=short.id, output=str(output_path))

            # Submit for approval: upload to Drive, post Discord message
            async with get_session_factory()() as session:
                await session.execute(
                    update(Short)
                    .where(Short.id == short.id)
                    .values(status="cut", format_recipe=recipe.name)
                )
                await submit_for_approval(
                    short=short,
                    source_video=source_video,
                    local_path=output_path,
                    drive_client=drive_client,
                    session=session,
                    settings=settings,
                )
                await session.commit()

        except Exception:
            log.exception("short_cut_failed", short_id=short.id)
            async with get_session_factory()() as session:
                await session.execute(
                    update(Short).where(Short.id == short.id).values(status="failed")
                )
                await session.commit()

    local_path.unlink(missing_ok=True)


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

    format_result: FormatProbeResult | None = None
    shorts: list[Short] = []

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / source_video.name

            await drive_client.download(
                source_video.drive_file_id,
                local_path,
                expected_size=source_video.size_bytes,
            )

            async with get_session_factory()() as session:
                await session.execute(
                    update(SourceVideo)
                    .where(SourceVideo.id == source_video.id)
                    .values(local_path=str(local_path))
                )
                await session.commit()

            # --- Format probe (Gemini) ---
            info = await probe_video(local_path)
            async with get_session_factory()() as session:
                await check_cost_budget(session, source_video.id, settings)
                format_result = await run_format_probe(
                    session=session,
                    source_video=source_video,
                    video_path=local_path,
                    info=info,
                    settings=settings,
                )
                await session.commit()

            # --- Pegasus upload (deletes local file after upload confirmed) ---
            async with get_session_factory()() as session:
                await check_cost_budget(session, source_video.id, settings)
                video_index: VideoIndex = await get_or_upload_asset(
                    session=session,
                    source_video=source_video,
                    video_path=local_path,
                    settings=settings,
                )
                await session.commit()

            # local_path deleted by uploader; TemporaryDirectory cleans empty dir on exit.

            # --- Clip selection (Pegasus describe + Gemini select) ---
            async with get_session_factory()() as session:
                await check_cost_budget(session, source_video.id, settings)
                shorts = await run_clip_selection(
                    video_index=video_index,
                    video_path=local_path,
                    source_video=source_video,
                    info=info,
                    session=session,
                    settings=settings,
                )
                await session.commit()

        # --- FFmpeg cut + reformat → approval gate ---
        if shorts:
            with tempfile.TemporaryDirectory() as cut_tmpdir:
                await _cut_all_shorts(
                    shorts=shorts,
                    source_video=source_video,
                    drive_client=drive_client,
                    tmpdir=cut_tmpdir,
                    settings=settings,
                )

        # --- Move source file in Drive ---
        if shorts:
            dest_folder_name = "processed"
            final_status = "completed"
        else:
            dest_folder_name = "no-shorts-found"
            final_status = "completed_no_output"

        dest_root = await drive_client.get_or_create_folder(
            dest_folder_name, settings.DRIVE_SHORTS_ROOT_FOLDER_ID
        )
        dest_folder = await drive_client.get_or_create_folder(
            source_video.category, dest_root
        )
        parents = await drive_client.get_parents(source_video.drive_file_id)
        current_parent = parents[0] if parents else ""
        await drive_client.move(source_video.drive_file_id, dest_folder, current_parent)

        await _mark_status(source_video.id, final_status)
        log.info(
            "source_video_completed",
            source_id=source_video.id,
            name=source_video.name,
            status=final_status,
            shorts_count=len(shorts),
        )

        # --- Discord summary ---
        async with get_session_factory()() as session:
            total_cost = await get_source_total_cost(session, source_video.id)
            breakdown = await get_cost_breakdown(session, source_video.id)

        if format_result is not None:
            summary = _build_success_summary(
                source_video, format_result, shorts, total_cost, breakdown
            )
            await send_message(settings.DISCORD_WEBHOOK_URL, summary)

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
