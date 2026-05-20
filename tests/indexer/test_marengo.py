from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.models import MomentDetectionRun
from src.indexer.marengo import MarengoIndexError, get_or_create_index
from src.utils.ffmpeg import VideoInfo


def _make_session(existing_run: MomentDetectionRun | None = None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_run
    session.execute.return_value = mock_result
    return session


def _source_video(source_id: int = 1, name: str = "gameplay.mp4") -> MagicMock:
    sv = MagicMock()
    sv.id = source_id
    sv.name = name
    return sv


_VIDEO_INFO = VideoInfo(duration_seconds=600.0, width=1920, height=1080)


class TestIdempotencyGuard:
    async def test_returns_existing_run_when_status_is_ready(self) -> None:
        existing = MomentDetectionRun(
            source_video_id=1, provider="marengo", index_id="idx_abc", status="ready"
        )
        session = _make_session(existing_run=existing)

        with patch("src.indexer.marengo._create_and_upload") as mock_upload:
            result = await get_or_create_index(
                session, _source_video(), Path("/tmp/v.mp4"), _VIDEO_INFO, MagicMock()
            )

        assert result is existing
        mock_upload.assert_not_called()
        session.add.assert_not_called()

    async def test_returns_existing_run_when_status_is_queried(self) -> None:
        existing = MomentDetectionRun(
            source_video_id=1, provider="marengo", index_id="idx_def", status="queried"
        )
        session = _make_session(existing_run=existing)

        with patch("src.indexer.marengo._create_and_upload") as mock_upload:
            result = await get_or_create_index(
                session, _source_video(), Path("/tmp/v.mp4"), _VIDEO_INFO, MagicMock()
            )

        assert result is existing
        mock_upload.assert_not_called()

    async def test_never_calls_twelvelabs_when_live_index_exists(self) -> None:
        existing = MomentDetectionRun(
            source_video_id=1, provider="marengo", index_id="idx_xyz", status="ready"
        )
        session = _make_session(existing_run=existing)

        with patch("src.indexer.marengo._create_and_upload") as mock_upload, patch(
            "src.indexer.marengo.record_marengo_index", new_callable=AsyncMock
        ) as mock_record:
            await get_or_create_index(
                session, _source_video(), Path("/tmp/v.mp4"), _VIDEO_INFO, MagicMock()
            )

        mock_upload.assert_not_called()
        mock_record.assert_not_called()


class TestNewIndexCreation:
    async def test_creates_run_row_and_calls_twelvelabs(self) -> None:
        session = _make_session(existing_run=None)
        settings = MagicMock()
        settings.TWELVELABS_API_KEY = "test_key"

        with (
            patch(
                "src.indexer.marengo._create_and_upload", return_value="new_idx"
            ) as mock_upload,
            patch("src.indexer.marengo.record_marengo_index", new_callable=AsyncMock),
        ):
            run = await get_or_create_index(
                session, _source_video(), Path("/tmp/v.mp4"), _VIDEO_INFO, settings
            )

        assert run.index_id == "new_idx"
        assert run.status == "ready"
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        assert isinstance(added, MomentDetectionRun)
        mock_upload.assert_called_once()

    async def test_records_cost_event_after_successful_index(self) -> None:
        session = _make_session(existing_run=None)

        with (
            patch("src.indexer.marengo._create_and_upload", return_value="new_idx"),
            patch(
                "src.indexer.marengo.record_marengo_index", new_callable=AsyncMock
            ) as mock_record,
        ):
            await get_or_create_index(
                session, _source_video(), Path("/tmp/v.mp4"), _VIDEO_INFO, MagicMock()
            )

        mock_record.assert_called_once()

    async def test_run_starts_with_indexing_status_before_upload(self) -> None:
        # Capture the run's status at the moment session.add is called (before TwelveLabs).
        captured_status: list[str] = []

        def _capture_add(obj: object) -> None:
            if isinstance(obj, MomentDetectionRun):
                captured_status.append(obj.status)

        session = _make_session(existing_run=None)
        session.add = MagicMock(side_effect=_capture_add)

        with (
            patch("src.indexer.marengo._create_and_upload", return_value="new_idx"),
            patch("src.indexer.marengo.record_marengo_index", new_callable=AsyncMock),
        ):
            await get_or_create_index(
                session, _source_video(), Path("/tmp/v.mp4"), _VIDEO_INFO, MagicMock()
            )

        assert captured_status == ["indexing"]


class TestTimeoutHandling:
    async def test_raises_marengo_error_on_timeout(self) -> None:
        session = _make_session(existing_run=None)

        with (
            patch("asyncio.wait_for", new=AsyncMock(side_effect=TimeoutError())),
            patch("src.indexer.marengo.record_marengo_index", new_callable=AsyncMock),
            pytest.raises(MarengoIndexError, match="timed out"),
        ):
            await get_or_create_index(
                session, _source_video(), Path("/tmp/v.mp4"), _VIDEO_INFO, MagicMock()
            )

    async def test_sets_run_status_to_failed_on_timeout(self) -> None:
        session = _make_session(existing_run=None)

        with (
            patch("asyncio.wait_for", new=AsyncMock(side_effect=TimeoutError())),
            patch("src.indexer.marengo.record_marengo_index", new_callable=AsyncMock),
            pytest.raises(MarengoIndexError),
        ):
            await get_or_create_index(
                session, _source_video(), Path("/tmp/v.mp4"), _VIDEO_INFO, MagicMock()
            )

        # The run object that was add()ed to the session should have status="failed"
        added_run = session.add.call_args[0][0]
        assert added_run.status == "failed"
