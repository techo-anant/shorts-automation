"""Tests for src/ingest/crop_request.py — parse_crop_coords and check_crop_requests."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ingest.crop_request import check_crop_requests, parse_crop_coords


# ---------------------------------------------------------------------------
# parse_crop_coords
# ---------------------------------------------------------------------------


class TestParseCropCoords:
    def test_valid_json_returns_tuple(self) -> None:
        result = parse_crop_coords('{"x": 100, "y": 50, "w": 480, "h": 270}')
        assert result == (100, 50, 480, 270)

    def test_json_embedded_in_text(self) -> None:
        result = parse_crop_coords('Here it is: {"x": 0, "y": 10, "w": 200, "h": 100} ok')
        assert result == (0, 10, 200, 100)

    def test_json_in_discord_code_block(self) -> None:
        text = '```json\n{"x": 5, "y": 3, "w": 300, "h": 150}\n```'
        result = parse_crop_coords(text)
        assert result == (5, 3, 300, 150)

    def test_returns_none_when_no_braces(self) -> None:
        assert parse_crop_coords("no crop here") is None

    def test_returns_none_when_missing_key(self) -> None:
        assert parse_crop_coords('{"x": 1, "y": 2, "w": 3}') is None

    def test_returns_none_when_zero_width(self) -> None:
        assert parse_crop_coords('{"x": 0, "y": 0, "w": 0, "h": 100}') is None

    def test_returns_none_when_negative_x(self) -> None:
        assert parse_crop_coords('{"x": -10, "y": 0, "w": 100, "h": 50}') is None

    def test_float_values_are_truncated_to_int(self) -> None:
        result = parse_crop_coords('{"x": 1.9, "y": 2.1, "w": 480.0, "h": 270.5}')
        assert result == (1, 2, 480, 270)

    def test_returns_none_on_invalid_json(self) -> None:
        assert parse_crop_coords('{"x": 1, bad json}') is None

    def test_string_values_coerced_to_int(self) -> None:
        # int("100") works, so this should succeed
        result = parse_crop_coords('{"x": "10", "y": "5", "w": "200", "h": "100"}')
        assert result == (10, 5, 200, 100)


# ---------------------------------------------------------------------------
# check_crop_requests — integration with mocked DB and Discord
# ---------------------------------------------------------------------------


def _make_source(id: int = 1) -> MagicMock:
    sv = MagicMock()
    sv.id = id
    sv.status = "waiting_for_crop"
    return sv


def _make_crop_req(
    id: int = 10,
    source_id: int = 1,
    status: str = "pending",
    expires_at: datetime | None = None,
    message_id: str = "msg123",
) -> MagicMock:
    cr = MagicMock()
    cr.id = id
    cr.source_video_id = source_id
    cr.status = status
    cr.discord_message_id = message_id
    cr.expires_at = expires_at or datetime.now(tz=UTC) + timedelta(hours=24)
    return cr


class TestCheckCropRequests:
    async def test_marks_provided_and_re_queues_when_reply_received(self) -> None:
        source = _make_source()
        crop_req = _make_crop_req()

        settings = MagicMock()
        settings.DISCORD_APPROVAL_CHANNEL_ID = "ch1"
        settings.DISCORD_BOT_TOKEN = "tok"

        with (
            patch("src.ingest.crop_request.get_session_factory") as mock_sf,
            patch(
                "src.ingest.crop_request.discord_approval.get_message_replies",
                new_callable=AsyncMock,
                return_value=['{"x": 10, "y": 5, "w": 480, "h": 270}'],
            ),
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.all.return_value = [(source, crop_req)]
            mock_session.execute.return_value = mock_result
            mock_sf.return_value.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_sf.return_value.return_value.__aexit__ = AsyncMock(return_value=False)

            await check_crop_requests(settings)

        # Should have executed updates (status=provided + SourceVideo status=pending)
        assert mock_session.execute.call_count >= 2
        assert mock_session.commit.called

    async def test_marks_timed_out_when_expired(self) -> None:
        source = _make_source()
        crop_req = _make_crop_req(expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))

        settings = MagicMock()
        settings.DISCORD_APPROVAL_CHANNEL_ID = "ch1"
        settings.DISCORD_BOT_TOKEN = "tok"

        with (
            patch("src.ingest.crop_request.get_session_factory") as mock_sf,
            patch(
                "src.ingest.crop_request.discord_approval.get_message_replies",
                new_callable=AsyncMock,
            ) as mock_replies,
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.all.return_value = [(source, crop_req)]
            mock_session.execute.return_value = mock_result
            mock_sf.return_value.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_sf.return_value.return_value.__aexit__ = AsyncMock(return_value=False)

            await check_crop_requests(settings)

        # Discord should NOT be polled for expired requests
        mock_replies.assert_not_called()
        assert mock_session.commit.called

    async def test_no_op_when_no_reply_yet(self) -> None:
        source = _make_source()
        crop_req = _make_crop_req()

        settings = MagicMock()
        settings.DISCORD_APPROVAL_CHANNEL_ID = "ch1"
        settings.DISCORD_BOT_TOKEN = "tok"

        with (
            patch("src.ingest.crop_request.get_session_factory") as mock_sf,
            patch(
                "src.ingest.crop_request.discord_approval.get_message_replies",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.all.return_value = [(source, crop_req)]
            mock_session.execute.return_value = mock_result
            mock_sf.return_value.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_sf.return_value.return_value.__aexit__ = AsyncMock(return_value=False)

            await check_crop_requests(settings)

        # No commit should be called (no state changed)
        mock_session.commit.assert_not_called()
