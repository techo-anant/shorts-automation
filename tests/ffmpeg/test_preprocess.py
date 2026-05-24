"""Tests for src/ffmpeg/preprocess.py."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.errors import SourceTooLongError
from src.ffmpeg.preprocess import preprocess_if_oversized
from src.utils.ffmpeg import VideoInfo


def _settings(max_size: int = 2_040_109_466, max_duration: int = 6900) -> MagicMock:
    s = MagicMock()
    s.MAX_SOURCE_SIZE_BYTES = max_size
    s.MAX_SOURCE_DURATION_SECONDS = max_duration
    return s


def _info(duration_seconds: float = 3600.0) -> VideoInfo:
    return VideoInfo(duration_seconds=duration_seconds, width=1920, height=1080)


class TestPreprocessIfOversized:
    async def test_returns_original_path_when_within_limits(self, tmp_path: Path) -> None:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 100)  # tiny file
        settings = _settings(max_size=1_000_000, max_duration=7200)
        info = _info(duration_seconds=60.0)

        result = await preprocess_if_oversized(src, info, settings, tmp_path)

        assert result == src

    async def test_raises_source_too_long_when_duration_exceeds_limit(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 100)
        settings = _settings(max_duration=3600)  # 1 h limit
        info = _info(duration_seconds=7300.0)  # ~2 h

        with pytest.raises(SourceTooLongError, match="2.0 h"):
            await preprocess_if_oversized(src, info, settings, tmp_path)

    async def test_raises_source_too_long_when_both_limits_exceeded(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 1000)
        # Duration over-limit takes precedence over size over-limit
        settings = _settings(max_size=10, max_duration=3600)
        info = _info(duration_seconds=9000.0)

        with pytest.raises(SourceTooLongError):
            await preprocess_if_oversized(src, info, settings, tmp_path)

    async def test_re_encodes_when_size_exceeds_limit(self, tmp_path: Path) -> None:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 1000)
        settings = _settings(max_size=10)  # 10-byte limit → any real file exceeds it
        info = _info(duration_seconds=60.0)

        output = tmp_path / "pre_video.mp4"

        async def fake_run_ffmpeg(args: list[str]) -> None:
            # Simulate FFmpeg producing a smaller file
            output.write_bytes(b"y" * 50)

        with patch("src.ffmpeg.preprocess.run_ffmpeg", fake_run_ffmpeg):
            result = await preprocess_if_oversized(src, info, settings, tmp_path)

        assert result == output
        assert result != src

    async def test_re_encode_uses_crf24_and_aac(self, tmp_path: Path) -> None:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 1000)
        settings = _settings(max_size=10)
        info = _info(duration_seconds=60.0)

        output = tmp_path / "pre_video.mp4"
        captured_args: list[list[str]] = []

        async def capture_ffmpeg(args: list[str]) -> None:
            captured_args.append(args)
            output.write_bytes(b"y" * 50)

        with patch("src.ffmpeg.preprocess.run_ffmpeg", capture_ffmpeg):
            await preprocess_if_oversized(src, info, settings, tmp_path)

        assert len(captured_args) == 1
        args = captured_args[0]
        assert "-crf" in args
        assert args[args.index("-crf") + 1] == "24"
        assert "-c:a" in args
        assert args[args.index("-c:a") + 1] == "aac"
        # Must never use shell strings — args must be a list of strings
        assert all(isinstance(a, str) for a in args)

    async def test_output_path_is_prefixed_with_pre(self, tmp_path: Path) -> None:
        src = tmp_path / "my_video.mp4"
        src.write_bytes(b"x" * 1000)
        settings = _settings(max_size=10)
        info = _info(duration_seconds=60.0)

        expected_output = tmp_path / "pre_my_video.mp4"

        async def fake_ffmpeg(args: list[str]) -> None:
            expected_output.write_bytes(b"smaller")

        with patch("src.ffmpeg.preprocess.run_ffmpeg", fake_ffmpeg):
            result = await preprocess_if_oversized(src, info, settings, tmp_path)

        assert result.name == "pre_my_video.mp4"
