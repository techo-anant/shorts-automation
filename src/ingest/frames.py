from pathlib import Path

import structlog

from src.utils.ffmpeg import VideoInfo, run_ffmpeg

log = structlog.get_logger(__name__)

# Sample points as fractions of total duration
_SAMPLE_FRACTIONS = [0.0, 0.25, 0.50, 0.75, 1.0]


async def extract_sample_frames(video_path: Path, output_dir: Path, info: VideoInfo) -> list[Path]:
    """
    Extract 5 frames at 0%, 25%, 50%, 75%, 100% of video duration.
    Returns a list of PNG paths in sample order. Cleans up nothing — caller owns cleanup.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_paths: list[Path] = []

    # Clamp the 100% sample slightly back to avoid seeking past the last keyframe
    duration = info.duration_seconds
    timestamps = [
        min(frac * duration, max(duration - 0.5, 0.0))
        for frac in _SAMPLE_FRACTIONS
    ]

    for i, ts in enumerate(timestamps):
        out_path = output_dir / f"frame_{i:02d}.png"
        await run_ffmpeg([
            "-ss", f"{ts:.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            "-y",
            str(out_path),
        ])
        frame_paths.append(out_path)
        log.info("frame_extracted", index=i, timestamp=round(ts, 2), path=str(out_path))

    return frame_paths
