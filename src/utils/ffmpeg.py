import asyncio
import json
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


class FFmpegError(Exception):
    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"FFmpeg exited {returncode}: {stderr[:300]}")


async def run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg with the given argument list. Never use shell=True or string concatenation."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode or -1, stderr_bytes.decode(errors="replace"))


class VideoInfo:
    """Parsed output from ffprobe for a single video file."""

    def __init__(self, duration_seconds: float, width: int, height: int) -> None:
        self.duration_seconds = duration_seconds
        self.width = width
        self.height = height

    @property
    def duration_minutes(self) -> float:
        return self.duration_seconds / 60.0

    @property
    def aspect_ratio(self) -> str:
        """Returns a normalised string like '16:9' or '9:16'."""
        from math import gcd
        divisor = gcd(self.width, self.height)
        return f"{self.width // divisor}:{self.height // divisor}"


async def probe_video(path: Path) -> VideoInfo:
    """Return duration and dimensions for a video file via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode or -1, stderr_bytes.decode(errors="replace"))

    data = json.loads(stdout_bytes)

    streams = data.get("streams", [])
    fmt = data.get("format", {})

    width: int = streams[0]["width"] if streams else 0
    height: int = streams[0]["height"] if streams else 0

    # Duration can live in the stream or the format container; prefer stream
    raw_duration = (
        streams[0].get("duration")
        or fmt.get("duration")
    )
    if raw_duration is None:
        raise ValueError(f"ffprobe returned no duration for {path}")

    return VideoInfo(
        duration_seconds=float(raw_duration),
        width=width,
        height=height,
    )
