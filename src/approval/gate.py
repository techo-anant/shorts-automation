"""
Approval gate orchestration:
  - submit_for_approval: upload cut MP4 to Drive, post Discord message, create ApprovalRequest row
  - check_pending_approvals: poll Discord reactions, update Short/ApprovalRequest statuses
  - upload_approved_shorts: upload YouTube-ready approved Shorts
"""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.approval.discord_approval import (
    add_approval_reactions,
    get_decision,
    post_message,
    update_message,
)
from src.config import Settings
from src.costs.record import record_youtube_upload
from src.db.models import ApprovalRequest, Short, SourceVideo, StepTiming
from src.db.session import get_session_factory
from src.drive.client import DriveClient
from src.errors import YouTubeQuotaError, YouTubeUploadError
from src.notifications.discord import send_error
from src.utils.timing import record_step_timing
from src.youtube.quota import check_and_reserve_upload_quota
from src.youtube.upload import upload_short

log = structlog.get_logger(__name__)

_APPROVE_EMOJI = "👍"
_REJECT_EMOJI = "👎"


def _approval_message(short: Short, source_video: SourceVideo, preview_url: str) -> str:
    duration = short.cut_end_seconds - short.cut_start_seconds
    rank = short.gemini_rank or "?"
    rationale = short.gemini_rationale or "—"
    title = short.title or source_video.name
    return (
        f"🎬 **{title}**\n"
        f"Rank {rank}/10 | {duration:.0f}s "
        f"[{short.cut_start_seconds:.0f}s – {short.cut_end_seconds:.0f}s]\n"
        f"💬 _{rationale}_\n"
        f"📎 [Preview on Drive]({preview_url})\n\n"
        f"React {_APPROVE_EMOJI} to approve · {_REJECT_EMOJI} to reject"
    )


async def submit_for_approval(
    short: Short,
    source_video: SourceVideo,
    local_path: Path,
    drive_client: DriveClient,
    session: AsyncSession,
    settings: Settings,
) -> ApprovalRequest:
    """
    Upload the cut MP4 to Drive pending-approval/, post a Discord message,
    pre-add reactions, and create an ApprovalRequest row.
    """
    # Upload to Drive pending-approval/<category>/
    pending_root = await drive_client.get_or_create_folder(
        "pending-approval", settings.DRIVE_SHORTS_ROOT_FOLDER_ID
    )
    pending_folder = await drive_client.get_or_create_folder(
        source_video.category, pending_root
    )
    clip_name = f"{source_video.name}_clip_{short.id}.mp4"
    async with record_step_timing(source_video.id, "drive_upload_preview", short_id=short.id):
        drive_file_id = await drive_client.upload_file(local_path, clip_name, pending_folder)
        preview_url = await drive_client.make_public(drive_file_id)

    # Post Discord approval message
    content = _approval_message(short, source_video, preview_url)
    async with record_step_timing(source_video.id, "discord_post_approval", short_id=short.id):
        message_id = await post_message(
            settings.DISCORD_APPROVAL_CHANNEL_ID, settings.DISCORD_BOT_TOKEN, content
        )
        await add_approval_reactions(
            settings.DISCORD_APPROVAL_CHANNEL_ID, message_id, settings.DISCORD_BOT_TOKEN
        )

    expires_at = datetime.now(UTC) + timedelta(hours=settings.APPROVAL_TIMEOUT_HOURS)
    approval = ApprovalRequest(
        source_video_id=source_video.id,
        short_id=short.id,
        discord_message_id=message_id,
        drive_preview_file_id=drive_file_id,
        expires_at=expires_at,
    )
    session.add(approval)

    await session.execute(
        update(Short).where(Short.id == short.id).values(
            status="pending_approval",
            drive_file_id=drive_file_id,
        )
    )

    log.info(
        "approval_submitted",
        short_id=short.id,
        message_id=message_id,
        expires_at=expires_at.isoformat(),
    )
    return approval


async def check_pending_approvals(settings: Settings) -> None:
    """
    Poll Discord for reactions on all pending ApprovalRequest rows.
    Updates Short and ApprovalRequest statuses. Runs every poll cycle.
    """
    now = datetime.now(UTC)

    async with get_session_factory()() as session:
        result = await session.execute(
            select(ApprovalRequest).where(ApprovalRequest.decision.is_(None))
        )
        pending = result.scalars().all()

    for approval in pending:
        # Timeout check first
        if approval.expires_at <= now:
            await _apply_decision(approval, "rejected_timeout", settings)
            log.info("approval_timed_out", short_id=approval.short_id)
            continue

        try:
            decision = await get_decision(
                settings.DISCORD_APPROVAL_CHANNEL_ID,
                approval.discord_message_id,
                settings.DISCORD_BOT_TOKEN,
            )
        except Exception:
            log.exception("discord_reaction_poll_failed", message_id=approval.discord_message_id)
            continue

        if decision is not None:
            await _apply_decision(approval, decision, settings)


async def _apply_decision(
    approval: ApprovalRequest, decision: str, settings: Settings
) -> None:
    """Write the decision to DB, update the Short status, and edit the Discord message."""
    now = datetime.now(UTC)
    new_short_status = "approved" if decision == "approved" else "rejected"

    async with get_session_factory()() as session:
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == approval.id)
            .values(decision=decision, decided_at=now)
        )
        await session.execute(
            update(Short)
            .where(Short.id == approval.short_id)
            .values(status=new_short_status)
        )
        # Record how long the operator took to respond
        created_at = approval.created_at
        session.add(
            StepTiming(
                source_video_id=approval.source_video_id,
                short_id=approval.short_id,
                step_name="approval_wait",
                started_at=created_at,
                ended_at=now,
                duration_seconds=(now - created_at).total_seconds(),
                succeeded=(decision != "rejected_timeout"),
            )
        )
        await session.commit()

    # Edit the Discord message to show the decision
    icon = "✅" if decision == "approved" else "❌"
    label = "Approved" if decision == "approved" else (
        "Rejected (timeout)" if decision == "rejected_timeout" else "Rejected"
    )
    try:
        async with get_session_factory()() as session:
            result = await session.execute(
                select(Short).where(Short.id == approval.short_id)
            )
            short = result.scalar_one_or_none()
        if short is not None:
            title = short.title or f"Short {short.id}"
            await update_message(
                settings.DISCORD_APPROVAL_CHANNEL_ID,
                approval.discord_message_id,
                settings.DISCORD_BOT_TOKEN,
                f"{icon} **{label}** — {title}",
            )
    except Exception:
        log.warning("discord_message_update_failed", message_id=approval.discord_message_id)

    # Clean up Drive preview for rejected shorts
    if new_short_status == "rejected":
        try:
            from src.config import get_settings
            from src.drive.client import DriveClient
            drive = DriveClient(get_settings())
            await drive.delete_file(approval.drive_preview_file_id)
        except Exception:
            log.warning(
                "drive_preview_delete_failed", file_id=approval.drive_preview_file_id
            )

    log.info("approval_decision_applied", short_id=approval.short_id, decision=decision)


async def upload_approved_shorts(
    drive_client: DriveClient, settings: Settings
) -> None:
    """
    Called each poll cycle. Find approved Shorts, download from Drive, upload to YouTube.
    """
    async with get_session_factory()() as session:
        result = await session.execute(
            select(Short, SourceVideo)
            .join(SourceVideo, Short.source_video_id == SourceVideo.id)
            .where(Short.status == "approved")
            .where(Short.youtube_video_id.is_(None))
        )
        rows = result.all()

    for short, source_video in rows:
        if not short.drive_file_id:
            log.warning("approved_short_no_drive_file", short_id=short.id)
            continue

        async with get_session_factory()() as session:
            try:
                await check_and_reserve_upload_quota(session)
                await session.commit()
            except YouTubeQuotaError as exc:
                log.warning("youtube_quota_exhausted", short_id=short.id)
                await send_error(
                    settings.DISCORD_WEBHOOK_URL,
                    title="YouTube quota exhausted",
                    description=str(exc),
                )
                return

        async with get_session_factory()() as session:
            await session.execute(
                update(Short).where(Short.id == short.id).values(status="uploading")
            )
            await session.commit()

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / f"short_{short.id}.mp4"
            try:
                await drive_client.download(short.drive_file_id, local_path)
            except Exception:
                log.exception("approved_short_download_failed", short_id=short.id)
                async with get_session_factory()() as session:
                    await session.execute(
                        update(Short).where(Short.id == short.id).values(status="approved")
                    )
                    await session.commit()
                continue

            title = short.title or source_video.name
            try:
                async with record_step_timing(source_video.id, "youtube_upload", short_id=short.id):
                    video_id = await upload_short(
                        local_path=local_path,
                        title=title,
                        category=source_video.category,
                        rationale=short.gemini_rationale or "",
                        settings=settings,
                    )
            except YouTubeUploadError:
                log.exception("youtube_upload_failed", short_id=short.id)
                async with get_session_factory()() as session:
                    await session.execute(
                        update(Short).where(Short.id == short.id).values(status="failed")
                    )
                    await session.commit()
                continue

        async with get_session_factory()() as session:
            await session.execute(
                update(Short)
                .where(Short.id == short.id)
                .values(status="uploaded", youtube_video_id=video_id)
            )
            await record_youtube_upload(
                session=session,
                source_video_id=source_video.id,
                short_id=short.id,
            )
            await session.commit()

        # Delete Drive preview now that it's on YouTube
        try:
            await drive_client.delete_file(short.drive_file_id)
        except Exception:
            log.warning("drive_preview_delete_after_upload_failed", file_id=short.drive_file_id)

        log.info(
            "short_uploaded",
            short_id=short.id,
            youtube_video_id=video_id,
            title=title,
        )
