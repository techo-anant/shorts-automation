"""Tests for Phase 17/18 additions to src/approval/gate.py."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.models import ApprovalRequest, Short, SourceVideo
from src.approval.gate import post_approval_to_discord, upload_preview_to_drive


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_video(id: int = 1, name: str = "video.mp4", category: str = "gameplay") -> SourceVideo:
    sv = MagicMock(spec=SourceVideo)
    sv.id = id
    sv.name = name
    sv.category = category
    return sv


def _short(id: int = 10, rank: int = 8) -> Short:
    s = MagicMock(spec=Short)
    s.id = id
    s.gemini_rank = rank
    s.gemini_rationale = "Great moment"
    s.title = "Best clip"
    s.cut_start_seconds = 30.0
    s.cut_end_seconds = 75.0
    return s


def _settings(**overrides):
    s = MagicMock()
    s.DRIVE_SHORTS_ROOT_FOLDER_ID = "root123"
    s.DISCORD_APPROVAL_CHANNEL_ID = "chan456"
    s.DISCORD_BOT_TOKEN = "tok789"
    s.APPROVAL_TIMEOUT_HOURS = 24
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# upload_preview_to_drive
# ---------------------------------------------------------------------------


class TestUploadPreviewToDrive:
    async def test_returns_drive_file_id_and_preview_url(self, tmp_path) -> None:
        short = _short()
        source_video = _source_video()
        settings = _settings()
        local_path = tmp_path / "short_10.mp4"
        local_path.write_bytes(b"fake")

        drive_client = AsyncMock()
        drive_client.get_or_create_folder = AsyncMock(side_effect=["pending_root", "pending_cat"])
        drive_client.upload_file = AsyncMock(return_value="file_abc")
        drive_client.make_public = AsyncMock(return_value="https://drive.google.com/file/d/file_abc/view")

        drive_file_id, preview_url = await upload_preview_to_drive(
            short=short,
            source_video=source_video,
            local_path=local_path,
            drive_client=drive_client,
            settings=settings,
        )

        assert drive_file_id == "file_abc"
        assert "file_abc" in preview_url

    async def test_creates_folder_under_pending_approval_category(self, tmp_path) -> None:
        short = _short()
        source_video = _source_video(category="tutorial")
        settings = _settings()
        local_path = tmp_path / "short_10.mp4"
        local_path.write_bytes(b"fake")

        drive_client = AsyncMock()
        drive_client.get_or_create_folder = AsyncMock(side_effect=["pending_root", "pending_cat"])
        drive_client.upload_file = AsyncMock(return_value="fid")
        drive_client.make_public = AsyncMock(return_value="https://drive.google.com/file/d/fid/view")

        await upload_preview_to_drive(
            short=short,
            source_video=source_video,
            local_path=local_path,
            drive_client=drive_client,
            settings=settings,
        )

        # First folder call: "pending-approval" under root
        assert drive_client.get_or_create_folder.call_args_list[0].args[0] == "pending-approval"
        # Second folder call: category name under pending-approval
        assert drive_client.get_or_create_folder.call_args_list[1].args[0] == "tutorial"


# ---------------------------------------------------------------------------
# post_approval_to_discord
# ---------------------------------------------------------------------------


class TestPostApprovalToDiscord:
    async def _run(
        self,
        thread_id: str | None = None,
        transcoding_ready: bool = True,
    ) -> tuple[list[ApprovalRequest], ApprovalRequest]:
        added: list[ApprovalRequest] = []

        mock_session = AsyncMock()
        mock_session.add = lambda obj: added.append(obj)
        mock_session.execute = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        short = _short()
        source_video = _source_video()
        settings = _settings()

        with (
            patch("src.approval.gate.post_message", AsyncMock(return_value="msg999")),
            patch("src.approval.gate.add_approval_reactions", AsyncMock()),
        ):
            approval = await post_approval_to_discord(
                short=short,
                source_video=source_video,
                drive_file_id="drive_xyz",
                preview_url="https://drive.google.com/file/d/drive_xyz/view",
                session=mock_session,
                settings=settings,
                thread_id=thread_id,
                transcoding_ready=transcoding_ready,
            )

        return added, approval

    async def test_creates_approval_request_row(self) -> None:
        added, approval = await self._run()
        assert approval.discord_message_id == "msg999"
        assert approval.drive_preview_file_id == "drive_xyz"
        assert approval.discord_thread_id is None

    async def test_stores_thread_id_on_approval_request(self) -> None:
        added, approval = await self._run(thread_id="thread_111")
        assert approval.discord_thread_id == "thread_111"

    async def test_posts_to_thread_when_thread_id_provided(self) -> None:
        short = _short()
        source_video = _source_video()
        settings = _settings()

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.execute = AsyncMock()

        post_mock = AsyncMock(return_value="msg1")
        react_mock = AsyncMock()

        with (
            patch("src.approval.gate.post_message", post_mock),
            patch("src.approval.gate.add_approval_reactions", react_mock),
        ):
            await post_approval_to_discord(
                short=short,
                source_video=source_video,
                drive_file_id="fid",
                preview_url="https://example.com",
                session=mock_session,
                settings=settings,
                thread_id="thread_999",
            )

        # post_message and add_approval_reactions called with thread_id as channel
        assert post_mock.call_args.args[0] == "thread_999"
        assert react_mock.call_args.args[0] == "thread_999"

    async def test_posts_to_main_channel_when_no_thread(self) -> None:
        short = _short()
        source_video = _source_video()
        settings = _settings()

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.execute = AsyncMock()

        post_mock = AsyncMock(return_value="msg2")
        react_mock = AsyncMock()

        with (
            patch("src.approval.gate.post_message", post_mock),
            patch("src.approval.gate.add_approval_reactions", react_mock),
        ):
            await post_approval_to_discord(
                short=short,
                source_video=source_video,
                drive_file_id="fid",
                preview_url="https://example.com",
                session=mock_session,
                settings=settings,
                thread_id=None,
            )

        assert post_mock.call_args.args[0] == "chan456"

    async def test_transcoding_not_ready_note_in_message(self) -> None:
        short = _short()
        source_video = _source_video()
        settings = _settings()

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.execute = AsyncMock()

        captured: list[str] = []

        async def capture_post(channel_id: str, bot_token: str, content: str) -> str:
            captured.append(content)
            return "msg3"

        with (
            patch("src.approval.gate.post_message", capture_post),
            patch("src.approval.gate.add_approval_reactions", AsyncMock()),
        ):
            await post_approval_to_discord(
                short=short,
                source_video=source_video,
                drive_file_id="fid",
                preview_url="https://example.com",
                session=mock_session,
                settings=settings,
                transcoding_ready=False,
            )

        assert len(captured) == 1
        assert "still be processing" in captured[0]

    async def test_transcoding_ready_has_no_processing_note(self) -> None:
        short = _short()
        source_video = _source_video()
        settings = _settings()

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.execute = AsyncMock()

        captured: list[str] = []

        async def capture_post(channel_id: str, bot_token: str, content: str) -> str:
            captured.append(content)
            return "msg4"

        with (
            patch("src.approval.gate.post_message", capture_post),
            patch("src.approval.gate.add_approval_reactions", AsyncMock()),
        ):
            await post_approval_to_discord(
                short=short,
                source_video=source_video,
                drive_file_id="fid",
                preview_url="https://example.com",
                session=mock_session,
                settings=settings,
                transcoding_ready=True,
            )

        assert "still be processing" not in captured[0]
