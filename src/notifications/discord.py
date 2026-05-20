import aiohttp
import structlog

log = structlog.get_logger(__name__)


async def send_message(webhook_url: str, content: str) -> None:
    async with (
        aiohttp.ClientSession() as session,
        session.post(webhook_url, json={"content": content}) as resp,
    ):
        if resp.status not in (200, 204):
            log.warning("discord_send_failed", status=resp.status)


async def send_error(webhook_url: str, title: str, description: str) -> None:
    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": 0xFF0000,
            }
        ]
    }
    async with (
        aiohttp.ClientSession() as session,
        session.post(webhook_url, json=payload) as resp,
    ):
        if resp.status not in (200, 204):
            log.warning("discord_send_failed", status=resp.status)
