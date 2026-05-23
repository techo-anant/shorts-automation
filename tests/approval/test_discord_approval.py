"""Tests for create_thread_from_message in src/approval/discord_approval.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.approval.discord_approval import create_thread_from_message
from src.errors import DiscordPostError


class TestCreateThreadFromMessage:
    async def test_returns_thread_id_on_success(self) -> None:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={"id": "thread_42"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_http = AsyncMock()
        mock_http.post = MagicMock(return_value=mock_resp)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("src.approval.discord_approval.aiohttp.ClientSession", return_value=mock_http):
            result = await create_thread_from_message(
                channel_id="chan1", message_id="msg1", name="My Thread", bot_token="tok"
            )

        assert result == "thread_42"

    async def test_truncates_name_to_100_chars(self) -> None:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={"id": "t1"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        captured_body: list[dict] = []

        def capture_post(url, json, headers):  # type: ignore[override]
            captured_body.append(json)
            return mock_resp

        mock_http.post = capture_post

        long_name = "A" * 200
        with patch("src.approval.discord_approval.aiohttp.ClientSession", return_value=mock_http):
            await create_thread_from_message(
                channel_id="c", message_id="m", name=long_name, bot_token="t"
            )

        assert len(captured_body[0]["name"]) == 100

    async def test_raises_discord_post_error_on_http_failure(self) -> None:
        import aiohttp

        mock_resp = AsyncMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        err = aiohttp.ClientResponseError(request_info=MagicMock(), history=(), status=403)
        mock_resp.raise_for_status = MagicMock(side_effect=err)
        mock_resp.json = AsyncMock(return_value={})

        mock_http = AsyncMock()
        mock_http.post = MagicMock(return_value=mock_resp)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("src.approval.discord_approval.aiohttp.ClientSession", return_value=mock_http):
            with pytest.raises(DiscordPostError, match="HTTP 403"):
                await create_thread_from_message(
                    channel_id="c", message_id="m", name="name", bot_token="t"
                )
