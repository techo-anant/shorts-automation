"""
Discord HTTP API calls for the approval gate.
Uses the bot token to post messages, add reactions, and poll who reacted.
No discord.py dependency — plain aiohttp calls against the REST API.
"""

import urllib.parse

import aiohttp
import structlog

from src.errors import DiscordPostError, DiscordReactionError

log = structlog.get_logger(__name__)

_API = "https://discord.com/api/v10"
_APPROVE_EMOJI = "👍"
_REJECT_EMOJI = "👎"


def _headers(bot_token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"}


async def post_message(channel_id: str, bot_token: str, content: str) -> str:
    """Post a message to a channel. Returns the Discord message ID."""
    url = f"{_API}/channels/{channel_id}/messages"
    try:
        async with aiohttp.ClientSession() as session, session.post(
            url, json={"content": content}, headers=_headers(bot_token)
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return str(data["id"])
    except aiohttp.ClientResponseError as exc:
        raise DiscordPostError(f"Discord post failed (HTTP {exc.status}): {exc.message}") from exc
    except Exception as exc:
        raise DiscordPostError(f"Discord post failed: {exc}") from exc


async def add_approval_reactions(
    channel_id: str, message_id: str, bot_token: str
) -> None:
    """Pre-add 👍 and 👎 to a message so the operator just clicks."""
    for emoji in (_APPROVE_EMOJI, _REJECT_EMOJI):
        encoded = urllib.parse.quote(emoji)
        url = f"{_API}/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me"
        try:
            async with aiohttp.ClientSession() as http, http.put(url, headers=_headers(bot_token)) as resp:
                resp.raise_for_status()
        except aiohttp.ClientResponseError as exc:
            raise DiscordReactionError(
                f"Discord reaction failed for message {message_id} (HTTP {exc.status}): {exc.message}"
            ) from exc
        except Exception as exc:
            raise DiscordReactionError(f"Discord reaction failed for message {message_id}: {exc}") from exc


async def get_reaction_user_ids(
    channel_id: str, message_id: str, emoji: str, bot_token: str
) -> list[str]:
    """Return list of user IDs (excluding the bot itself) who reacted with emoji."""
    encoded = urllib.parse.quote(emoji)
    url = f"{_API}/channels/{channel_id}/messages/{message_id}/reactions/{encoded}"
    async with aiohttp.ClientSession() as session:
        # First get bot's own user ID so we can exclude it
        async with session.get(
            f"{_API}/users/@me", headers=_headers(bot_token)
        ) as me_resp:
            me_resp.raise_for_status()
            bot_id = str((await me_resp.json())["id"])

        async with session.get(url, headers=_headers(bot_token)) as resp:
            if resp.status == 404:
                return []
            resp.raise_for_status()
            users = await resp.json()
            return [str(u["id"]) for u in users if str(u["id"]) != bot_id]


async def get_decision(
    channel_id: str, message_id: str, bot_token: str
) -> str | None:
    """
    Check reactions on a message.
    Returns 'approved' if anyone reacted 👍, 'rejected' if anyone reacted 👎, None if neither.
    👍 takes precedence if both are present.
    """
    approvers = await get_reaction_user_ids(channel_id, message_id, _APPROVE_EMOJI, bot_token)
    if approvers:
        return "approved"
    rejecters = await get_reaction_user_ids(channel_id, message_id, _REJECT_EMOJI, bot_token)
    if rejecters:
        return "rejected"
    return None


async def get_message_replies(
    channel_id: str, message_id: str, bot_token: str
) -> list[str]:
    """
    Return the text content of all messages that explicitly reply to message_id.
    Fetches up to 50 messages posted after it and filters by message_reference.
    """
    url = f"{_API}/channels/{channel_id}/messages"
    params = {"after": message_id, "limit": "50"}
    async with aiohttp.ClientSession() as session, session.get(
        url, params=params, headers=_headers(bot_token)
    ) as resp:
        if resp.status == 404:
            return []
        resp.raise_for_status()
        messages = await resp.json()
    return [
        m["content"]
        for m in messages
        if (m.get("message_reference") or {}).get("message_id") == message_id
    ]


async def update_message(
    channel_id: str, message_id: str, bot_token: str, content: str
) -> None:
    """Edit an existing message (used to mark it decided)."""
    url = f"{_API}/channels/{channel_id}/messages/{message_id}"
    async with aiohttp.ClientSession() as session, session.patch(
        url, json={"content": content}, headers=_headers(bot_token)
    ) as resp:
        resp.raise_for_status()


async def create_thread_from_message(
    channel_id: str, message_id: str, name: str, bot_token: str
) -> str:
    """
    Create a public thread attached to message_id in channel_id.
    Returns the thread ID (used as channel_id for subsequent messages into the thread).
    name is truncated to 100 characters (Discord limit).
    """
    url = f"{_API}/channels/{channel_id}/messages/{message_id}/threads"
    try:
        async with aiohttp.ClientSession() as session, session.post(
            url,
            json={"name": name[:100], "auto_archive_duration": 1440},
            headers=_headers(bot_token),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return str(data["id"])
    except aiohttp.ClientResponseError as exc:
        raise DiscordPostError(
            f"Discord thread creation failed (HTTP {exc.status}): {exc.message}"
        ) from exc
    except Exception as exc:
        raise DiscordPostError(f"Discord thread creation failed: {exc}") from exc
