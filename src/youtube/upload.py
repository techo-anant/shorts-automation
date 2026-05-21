"""YouTube Shorts upload via Data API v3."""

import asyncio
from pathlib import Path

import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.config import Settings
from src.errors import YouTubeAuthError, YouTubeUploadError

log = structlog.get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# YouTube Data API category ID for Gaming
_CATEGORY_GAMING = "20"

# Hashtags rendered as clickable chips above the title (first 3 shown by YouTube)
_CATEGORY_HASHTAGS: dict[str, str] = {
    "gameplay": "#Shorts #Valorant #ValorantClips #Gaming #ValorantHighlights",
    "tutorial": "#Shorts #Valorant #ValorantGuide #Gaming #Tutorial",
    "podcast":  "#Shorts #Gaming #Podcast",
}
_DEFAULT_HASHTAGS = "#Shorts #Gaming"


def _build_service(settings: Settings):  # type: ignore[no-untyped-def]
    creds = Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=settings.GOOGLE_OAUTH_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=_SCOPES,
    )
    creds.refresh(Request())  # type: ignore[no-untyped-call]
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _build_description(category: str, rationale: str) -> str:
    hashtags = _CATEGORY_HASHTAGS.get(category, _DEFAULT_HASHTAGS)
    lines = []
    if rationale:
        lines.append(rationale)
    lines.append("")
    lines.append(hashtags)
    return "\n".join(lines)


def _upload_video(
    local_path: Path,
    title: str,
    description: str,
    settings: Settings,
) -> str:
    """Synchronous — runs in a thread pool. Returns the YouTube video ID."""
    svc = _build_service(settings)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": _CATEGORY_GAMING,
            "tags": [],
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(local_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=8 * 1024 * 1024,
    )

    request = svc.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("youtube_upload_progress", pct=round(status.progress() * 100))

    video_id: str = response["id"]
    log.info("youtube_upload_complete", video_id=video_id, title=title)
    return video_id


async def upload_short(
    local_path: Path,
    title: str,
    category: str,
    rationale: str,
    settings: Settings,
) -> str:
    """Upload a Short MP4 to YouTube. Returns the YouTube video ID."""
    description = _build_description(category, rationale)
    try:
        video_id = await asyncio.to_thread(
            _upload_video, local_path, title, description, settings
        )
    except HttpError as exc:
        if exc.resp.status in (401, 403):
            raise YouTubeAuthError(f"YouTube auth error: {exc}") from exc
        raise YouTubeUploadError(f"YouTube upload failed (HTTP {exc.resp.status}): {exc}") from exc
    except Exception as exc:
        raise YouTubeUploadError(f"YouTube upload failed: {exc}") from exc
    return video_id
