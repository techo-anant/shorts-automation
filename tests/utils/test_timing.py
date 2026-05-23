"""Tests for src/utils/timing.py."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.models import StepTiming
from src.utils.timing import format_timing_summary, record_step_timing


# ---------------------------------------------------------------------------
# format_timing_summary — pure function
# ---------------------------------------------------------------------------


def _timing(step: str, duration: float, short_id: int | None = None) -> StepTiming:
    now = datetime.now(tz=UTC)
    t = MagicMock(spec=StepTiming)
    t.step_name = step
    t.duration_seconds = duration
    t.short_id = short_id
    t.succeeded = True
    t.started_at = now
    return t


class TestFormatTimingSummary:
    def test_returns_empty_string_for_no_timings(self) -> None:
        assert format_timing_summary([]) == ""

    def test_includes_step_name_and_duration(self) -> None:
        result = format_timing_summary([_timing("ffprobe", 0.12)])
        assert "ffprobe" in result
        assert "0.1s" in result

    def test_aggregates_per_short_steps_with_count(self) -> None:
        timings = [
            _timing("ffmpeg_cut", 5.0, short_id=1),
            _timing("ffmpeg_cut", 6.0, short_id=2),
            _timing("ffmpeg_cut", 7.0, short_id=3),
        ]
        result = format_timing_summary(timings)
        assert "ffmpeg_cut" in result
        assert "18.0s" in result
        assert "×3" in result

    def test_grand_total_is_sum_of_all_durations(self) -> None:
        timings = [
            _timing("drive_download", 10.0),
            _timing("ffprobe", 2.0),
            _timing("pegasus_index", 38.0),
        ]
        result = format_timing_summary(timings)
        assert "50s" in result

    def test_source_steps_appear_before_short_steps(self) -> None:
        timings = [
            _timing("ffmpeg_cut", 5.0, short_id=1),
            _timing("drive_download", 12.0),
        ]
        result = format_timing_summary(timings)
        drive_pos = result.index("drive_download")
        cut_pos = result.index("ffmpeg_cut")
        assert drive_pos < cut_pos

    def test_single_clip_has_no_count_suffix(self) -> None:
        result = format_timing_summary([_timing("ffmpeg_cut", 5.0, short_id=1)])
        assert "×" not in result


# ---------------------------------------------------------------------------
# record_step_timing — context manager behaviour
# ---------------------------------------------------------------------------


class TestRecordStepTiming:
    async def test_writes_timing_row_on_success(self) -> None:
        added: list[StepTiming] = []

        mock_session = AsyncMock()
        mock_session.add = lambda obj: added.append(obj)
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.utils.timing.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = mock_session
            async with record_step_timing(42, "pegasus_describe"):
                pass

        assert len(added) == 1
        row = added[0]
        assert row.source_video_id == 42
        assert row.step_name == "pegasus_describe"
        assert row.succeeded is True
        assert row.short_id is None
        assert row.duration_seconds >= 0

    async def test_sets_short_id_when_provided(self) -> None:
        added: list[StepTiming] = []

        mock_session = AsyncMock()
        mock_session.add = lambda obj: added.append(obj)
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.utils.timing.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = mock_session
            async with record_step_timing(1, "ffmpeg_cut", short_id=99):
                pass

        assert added[0].short_id == 99

    async def test_records_succeeded_false_when_block_raises(self) -> None:
        added: list[StepTiming] = []

        mock_session = AsyncMock()
        mock_session.add = lambda obj: added.append(obj)
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.utils.timing.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = mock_session
            with pytest.raises(ValueError, match="boom"):
                async with record_step_timing(1, "ffmpeg_cut"):
                    raise ValueError("boom")

        assert len(added) == 1
        assert added[0].succeeded is False

    async def test_exception_propagates_even_if_timing_write_fails(self) -> None:
        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=RuntimeError("db down"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.utils.timing.get_session_factory") as mock_sf:
            mock_sf.return_value.return_value = mock_session
            # The wrapped ValueError must still propagate even though timing write fails
            with pytest.raises(ValueError, match="original"):
                async with record_step_timing(1, "some_step"):
                    raise ValueError("original")
