"""Tests for DriveClient.wait_for_transcoding."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.drive.client import DriveClient


def _client() -> DriveClient:
    settings = MagicMock()
    settings.GOOGLE_OAUTH_REFRESH_TOKEN = "rt"
    settings.GOOGLE_OAUTH_CLIENT_ID = "cid"
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "csec"
    client = DriveClient(settings)
    return client


class TestWaitForTranscoding:
    async def test_returns_true_immediately_when_metadata_present(self) -> None:
        client = _client()

        def _check() -> bool:
            return True

        with patch("src.drive.client.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn: fn())):
            with patch.object(client, "_svc") as mock_svc:
                mock_svc.return_value.files.return_value.get.return_value.execute.return_value = {
                    "videoMediaMetadata": {"width": 1920, "height": 1080}
                }
                result = await client.wait_for_transcoding("file_abc", poll_interval_seconds=1, timeout_seconds=30)

        assert result is True

    async def test_returns_false_after_timeout_when_metadata_absent(self) -> None:
        client = _client()

        call_count = 0

        async def fake_to_thread(fn):  # type: ignore[override]
            nonlocal call_count
            call_count += 1
            return False  # never ready

        with patch("src.drive.client.asyncio.to_thread", fake_to_thread):
            with patch("src.drive.client.asyncio.sleep", AsyncMock()):
                with patch("src.drive.client.datetime") as mock_dt:
                    from datetime import UTC, datetime, timedelta

                    start = datetime(2026, 1, 1, tzinfo=UTC)
                    # Return times that exhaust the deadline quickly
                    times = [start, start + timedelta(seconds=31)]
                    mock_dt.now.side_effect = times
                    mock_dt.now.return_value = start  # fallback

                    result = await client.wait_for_transcoding(
                        "file_abc", poll_interval_seconds=1, timeout_seconds=30
                    )

        assert result is False

    async def test_returns_true_on_second_poll(self) -> None:
        client = _client()

        call_count = 0

        async def fake_to_thread(fn):  # type: ignore[override]
            nonlocal call_count
            call_count += 1
            return call_count > 1  # ready on second call

        with patch("src.drive.client.asyncio.to_thread", fake_to_thread):
            with patch("src.drive.client.asyncio.sleep", AsyncMock()):
                result = await client.wait_for_transcoding(
                    "file_abc", poll_interval_seconds=1, timeout_seconds=300
                )

        assert result is True
        assert call_count == 2
