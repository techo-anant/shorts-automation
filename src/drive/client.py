import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from pydantic import BaseModel

from src.config import Settings
from src.errors import DriveAuthError, DriveDownloadError, DriveUploadError

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

        try:
            await asyncio.to_thread(_run)
        except google_auth_exceptions.TransportError as exc:
            raise DriveAuthError(f"Drive OAuth error downloading {file_id}: {exc}") from exc
        except HttpError as exc:
            if exc.resp.status in (401, 403):
                raise DriveAuthError(f"Drive permission error downloading {file_id}: {exc}") from exc
            raise DriveDownloadError(f"Drive download failed for {file_id}: {exc}") from exc

        if expected_size is not None:
            actual_size = dest_path.stat().st_size
            if actual_size != expected_size:
                dest_path.unlink(missing_ok=True)
                raise DriveDownloadError(
                    f"Drive download truncated: expected {expected_size} bytes,"
                    f" got {actual_size} (file_id={file_id})"
                )

        log.info("drive_download_complete", file_id=file_id, dest=str(dest_path))

    async def upload_file(
        self, local_path: Path, name: str, parent_id: str, mime_type: str = "video/mp4"
    ) -> str:
        """Upload a local file to Drive under parent_id. Returns the new file's Drive ID."""

        def _run() -> str:
            media = MediaFileUpload(
                str(local_path), mimetype=mime_type, resumable=True, chunksize=8 * 1024 * 1024
            )
            created = (
                self._svc()
                .files()
                .create(
                    body={"name": name, "parents": [parent_id]},
                    media_body=media,
                    fields="id",
                )
                .execute()
            )
            return created["id"]  # type: ignore[no-any-return]

        try:
            file_id: str = await asyncio.to_thread(_run)
        except google_auth_exceptions.TransportError as exc:
            raise DriveAuthError(f"Drive OAuth error uploading {name}: {exc}") from exc
        except HttpError as exc:
            if exc.resp.status in (401, 403):
                raise DriveAuthError(f"Drive permission error uploading {name}: {exc}") from exc
            raise DriveUploadError(f"Drive upload failed for {name}: {exc}") from exc
        log.info("drive_upload_complete", file_id=file_id, name=name, parent=parent_id)
        return file_id

    async def make_public(self, file_id: str) -> str:
        """Grant anyone-with-link read access. Returns the shareable web view URL."""

        def _run() -> str:
            self._svc().permissions().create(
                fileId=file_id,
                body={"role": "reader", "type": "anyone"},
            ).execute()
            return f"https://drive.google.com/file/d/{file_id}/view"

        return await asyncio.to_thread(_run)

    async def delete_file(self, file_id: str) -> None:
        """Permanently delete a file from Drive."""

        def _run() -> None:
            self._svc().files().delete(fileId=file_id).execute()

        await asyncio.to_thread(_run)
        log.info("drive_file_deleted", file_id=file_id)

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

    async def wait_for_transcoding(
        self,
        file_id: str,
        poll_interval_seconds: int = 10,
        timeout_seconds: int = 300,
    ) -> bool:
        """
        Poll until Drive reports video transcoding is done (videoMediaMetadata present).
        Returns True when ready, False if the timeout expires first.
        """
        deadline = datetime.now(tz=UTC) + timedelta(seconds=timeout_seconds)

        def _check() -> bool:
            result = (
                self._svc().files().get(fileId=file_id, fields="videoMediaMetadata").execute()
            )
            return bool(result.get("videoMediaMetadata"))

        while datetime.now(tz=UTC) < deadline:
            if await asyncio.to_thread(_check):
                log.info("drive_transcoding_ready", file_id=file_id)
                return True
            remaining = (deadline - datetime.now(tz=UTC)).total_seconds()
            if remaining > 0:
                await asyncio.sleep(min(poll_interval_seconds, remaining))

        log.warning(
            "drive_transcoding_timeout", file_id=file_id, timeout_seconds=timeout_seconds
        )
        return False

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
