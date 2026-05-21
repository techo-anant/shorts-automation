import asyncio
from pathlib import Path

import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from pydantic import BaseModel

from src.config import Settings

log = structlog.get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive"]

_VIDEO_MIME_TYPES = frozenset({
    "video/mp4",
    "video/quicktime",   # .mov
    "video/x-matroska",  # .mkv
    "video/x-msvideo",   # .avi
})


class DriveFolder(BaseModel):
    id: str
    name: str


class DriveFile(BaseModel):
    id: str
    name: str
    size_bytes: int | None = None


class DriveClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._creds: Credentials | None = None
        self._service = None

    def _svc(self):
        """Return cached Drive service, building it on first call."""
        if self._creds is None:
            self._creds = Credentials(
                token=None,
                refresh_token=self._settings.GOOGLE_OAUTH_REFRESH_TOKEN,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self._settings.GOOGLE_OAUTH_CLIENT_ID,
                client_secret=self._settings.GOOGLE_OAUTH_CLIENT_SECRET,
                scopes=_SCOPES,
            )
            self._creds.refresh(Request())
            self._service = build("drive", "v3", credentials=self._creds, cache_discovery=False)
        return self._service

    async def list_subfolders(self, parent_id: str) -> list[DriveFolder]:
        def _run() -> list[dict]:
            query = (
                f"'{parent_id}' in parents"
                " and mimeType = 'application/vnd.google-apps.folder'"
                " and trashed = false"
            )
            resp = self._svc().files().list(q=query, fields="files(id,name)", pageSize=100).execute()
            return resp.get("files", [])  # type: ignore[no-any-return]

        items: list[dict] = await asyncio.to_thread(_run)
        return [DriveFolder(id=f["id"], name=f["name"]) for f in items]

    async def list_videos_in(self, folder_id: str) -> list[DriveFile]:
        def _run() -> list[dict]:
            mime_clause = " or ".join(f"mimeType = '{m}'" for m in _VIDEO_MIME_TYPES)
            query = f"'{folder_id}' in parents and ({mime_clause}) and trashed = false"
            resp = (
                self._svc()
                .files()
                .list(q=query, fields="files(id,name,size)", pageSize=100)
                .execute()
            )
            return resp.get("files", [])  # type: ignore[no-any-return]

        items: list[dict] = await asyncio.to_thread(_run)
        return [
            DriveFile(
                id=f["id"],
                name=f["name"],
                size_bytes=int(f["size"]) if f.get("size") else None,
            )
            for f in items
        ]

    async def get_parents(self, file_id: str) -> list[str]:
        def _run() -> list[str]:
            resp = self._svc().files().get(fileId=file_id, fields="parents").execute()
            return resp.get("parents", [])  # type: ignore[no-any-return]

        return await asyncio.to_thread(_run)

    async def download(
        self, file_id: str, dest_path: Path, expected_size: int | None = None
    ) -> None:
        def _run() -> None:
            request = self._svc().files().get_media(fileId=file_id)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with dest_path.open("wb") as fh:
                downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    if status:
                        log.info(
                            "drive_download_progress",
                            file_id=file_id,
                            pct=round(status.progress() * 100),
                        )

        await asyncio.to_thread(_run)

        if expected_size is not None:
            actual_size = dest_path.stat().st_size
            if actual_size != expected_size:
                dest_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Drive download truncated: expected {expected_size} bytes,"
                    f" got {actual_size} (file_id={file_id})"
                )

        log.info("drive_download_complete", file_id=file_id, dest=str(dest_path))

    async def move(self, file_id: str, new_parent_id: str, current_parent_id: str = "") -> None:
        def _run() -> None:
            kwargs: dict = {
                "fileId": file_id,
                "addParents": new_parent_id,
                "fields": "id,parents",
            }
            if current_parent_id:
                kwargs["removeParents"] = current_parent_id
            self._svc().files().update(**kwargs).execute()

        await asyncio.to_thread(_run)
        log.info("drive_file_moved", file_id=file_id, to=new_parent_id)

    async def get_or_create_folder(self, name: str, parent_id: str) -> str:
        """Return folder ID by name under parent, creating it if it doesn't exist."""
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")

        def _run() -> str:
            svc = self._svc()
            query = (
                f"name = '{escaped}'"
                f" and '{parent_id}' in parents"
                " and mimeType = 'application/vnd.google-apps.folder'"
                " and trashed = false"
            )
            resp = svc.files().list(q=query, fields="files(id)", pageSize=1).execute()
            files = resp.get("files", [])
            if files:
                return files[0]["id"]  # type: ignore[no-any-return]
            created = svc.files().create(
                body={
                    "name": name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                },
                fields="id",
            ).execute()
            return created["id"]  # type: ignore[no-any-return]

        return await asyncio.to_thread(_run)
