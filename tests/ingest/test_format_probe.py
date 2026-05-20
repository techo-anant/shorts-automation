from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ingest.format_probe import (
    FormatProbeResult,
    _aggregate_frame_results,
    _FrameResult,
    run_format_probe,
)
from src.utils.ffmpeg import VideoInfo


def _frame(
    has_facecam: bool,
    position: str | None = None,
    size_pct: float | None = None,
    vertical: bool = False,
) -> _FrameResult:
    return _FrameResult(
        has_facecam=has_facecam,
        facecam_position=position,
        facecam_size_pct=size_pct,
        is_already_vertical=vertical,
    )


def _info(width: int = 1920, height: int = 1080) -> VideoInfo:
    return VideoInfo(duration_seconds=60.0, width=width, height=height)


class TestAggregateFrameResults:
    def test_all_five_agree_facecam_present(self) -> None:
        frames = [_frame(True, "top-right", 10.0)] * 5
        result = _aggregate_frame_results(frames, _info())
        assert result.has_facecam is True
        assert result.facecam_unstable is False

    def test_exactly_four_of_five_agree_at_threshold(self) -> None:
        frames = [_frame(True, "top-right", 10.0)] * 4 + [_frame(False)]
        result = _aggregate_frame_results(frames, _info())
        assert result.has_facecam is True
        assert result.facecam_unstable is False

    def test_three_of_five_triggers_unstable_and_defaults_to_no_facecam(self) -> None:
        frames = [_frame(True, "top-right", 10.0)] * 3 + [_frame(False)] * 2
        result = _aggregate_frame_results(frames, _info())
        assert result.has_facecam is False
        assert result.facecam_unstable is True

    def test_one_of_five_triggers_unstable(self) -> None:
        frames = [_frame(True, "top-right", 10.0)] + [_frame(False)] * 4
        result = _aggregate_frame_results(frames, _info())
        assert result.has_facecam is False
        assert result.facecam_unstable is True

    def test_zero_of_five_is_stable_no_facecam(self) -> None:
        frames = [_frame(False)] * 5
        result = _aggregate_frame_results(frames, _info())
        assert result.has_facecam is False
        assert result.facecam_unstable is False

    def test_facecam_position_from_first_agreeing_frame(self) -> None:
        # Frame 0 has no facecam; frame 1 is the first to agree — its position wins.
        frames = [
            _frame(False),
            _frame(True, "top-left", 8.0),
            _frame(True, "top-right", 12.0),
            _frame(True, "top-left", 9.0),
            _frame(True, "top-left", 9.0),
        ]
        result = _aggregate_frame_results(frames, _info())
        assert result.has_facecam is True
        assert result.facecam_position == "top-left"
        assert result.facecam_size_pct == 8.0

    def test_facecam_position_is_none_when_no_facecam(self) -> None:
        frames = [_frame(False)] * 5
        result = _aggregate_frame_results(frames, _info())
        assert result.facecam_position is None
        assert result.facecam_size_pct is None

    def test_vertical_detected_by_majority_vote(self) -> None:
        frames = [_frame(False, vertical=True)] * 3 + [_frame(False, vertical=False)] * 2
        result = _aggregate_frame_results(frames, _info(1080, 1920))
        assert result.is_already_vertical is True

    def test_not_vertical_when_minority_vote(self) -> None:
        frames = [_frame(False, vertical=True)] * 2 + [_frame(False, vertical=False)] * 3
        result = _aggregate_frame_results(frames, _info(1920, 1080))
        assert result.is_already_vertical is False

    def test_aspect_ratio_comes_from_video_info_not_frames(self) -> None:
        frames = [_frame(False)] * 5
        result = _aggregate_frame_results(frames, _info(1920, 1080))
        assert result.source_aspect_ratio == "16:9"

    def test_aspect_ratio_for_vertical_source(self) -> None:
        frames = [_frame(False)] * 5
        result = _aggregate_frame_results(frames, _info(1080, 1920))
        assert result.source_aspect_ratio == "9:16"

    def test_facecam_position_none_when_unstable(self) -> None:
        # 3/5 triggers unstable → has_facecam=False → position/size should be None
        frames = [_frame(True, "bottom-right", 5.0)] * 3 + [_frame(False)] * 2
        result = _aggregate_frame_results(frames, _info())
        assert result.facecam_position is None
        assert result.facecam_size_pct is None


class TestRunFormatProbe:
    async def test_creates_format_profile_and_cost_event_on_success(self) -> None:
        session = AsyncMock()
        session.add = MagicMock()

        source_video = MagicMock()
        source_video.id = 7
        info = _info(1920, 1080)
        settings = MagicMock()
        settings.GEMINI_API_KEY = "test_key"

        frame_results = [_frame(True, "top-right", 10.0)] * 5

        with (
            patch("src.ingest.format_probe.genai.Client"),
            patch(
                "src.ingest.format_probe.extract_sample_frames",
                new_callable=AsyncMock,
                return_value=[Path("f.png")],
            ),
            patch(
                "src.ingest.format_probe._analyse_frames",
                new_callable=AsyncMock,
                return_value=(frame_results, 1000, 200),
            ),
        ):
            result = await run_format_probe(
                session=session,
                source_video=source_video,
                video_path=Path("/tmp/test.mp4"),
                info=info,
                settings=settings,
            )

        assert isinstance(result, FormatProbeResult)
        assert result.has_facecam is True
        assert result.source_aspect_ratio == "16:9"
        assert result.facecam_unstable is False
        # session.add called twice: FormatProfile row + CostEvent row
        assert session.add.call_count == 2

    async def test_propagates_exception_from_gemini(self) -> None:
        session = AsyncMock()
        source_video = MagicMock()
        source_video.id = 1
        settings = MagicMock()
        settings.GEMINI_API_KEY = "test_key"

        with (
            patch("src.ingest.format_probe.genai.Client"),
            patch(
                "src.ingest.format_probe.extract_sample_frames",
                new_callable=AsyncMock,
                return_value=[Path("f.png")],
            ),
            patch(
                "src.ingest.format_probe._analyse_frames",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Gemini unavailable"),
            ),
            pytest.raises(RuntimeError, match="Gemini unavailable"),
        ):
            await run_format_probe(
                session=session,
                source_video=source_video,
                video_path=Path("/tmp/test.mp4"),
                info=_info(),
                settings=settings,
            )
