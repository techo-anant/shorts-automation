"""
Oversized source video pre-processing.

Pegasus rejects videos larger than ~2 GiB or longer than 2 hours.
This module checks those limits and re-encodes at a lower bitrate when only
the file size is the problem. Duration violations are refused entirely —
splitting a multi-hour video breaks narrative continuity for clip selection.
"""

from pathlib import Path

import structlog

from src.config import Settings
from src.errors import SourceTooLongError
from src.utils.ffmpeg import VideoInfo, run_ffmpeg

log = structlog.get_logger(__name__)

# CRF 24 is more aggressive than the cut/reformat default (23) because Pegasus
# only needs enough quality to understand the video, not to ship it to YouTube.
_PREPROCESS_CRF = 24
_PREPROCESS_AUDIO_BITRATE = "96k"


async def preprocess_if_oversized(
    local_path: Path,
    info: VideoInfo,
    settings: Settings,
    output_dir: Path,
) -> Path:
    """
    Check the source against Pegasus size and duration limits and act accordingly:

    - Duration exceeds MAX_SOURCE_DURATION_SECONDS → raise SourceTooLongError.
      Splitting is not supported — the operator must trim the source manually.
    - Duration ok but file size exceeds MAX_SOURCE_SIZE_BYTES → re-encode at
      CRF 24 / 96k audio and return the new path.  The original file is left
      untouched; the caller is responsible for cleaning up output_dir.
    - Neither limit exceeded → return local_path unchanged (fast path, no I/O).
    """
    size_bytes = local_path.stat().st_size

    if info.duration_seconds > settings.MAX_SOURCE_DURATION_SECONDS:
        hours = info.duration_seconds / 3600
        limit_hours = settings.MAX_SOURCE_DURATION_SECONDS / 3600
        raise SourceTooLongError(
            f"{local_path.name} is {hours:.1f} h — exceeds the {limit_hours:.1f} h Pegasus"
            " limit. Trim the source to under 1 h 55 m before dropping it in to-process/."
        )

    if size_bytes <= settings.MAX_SOURCE_SIZE_BYTES:
        return local_path

    log.info(
        "source_oversized_preprocessing",
        name=local_path.name,
        size_gb=round(size_bytes / 1_073_741_824, 2),
        limit_gb=round(settings.MAX_SOURCE_SIZE_BYTES / 1_073_741_824, 2),
        duration_minutes=round(info.duration_minutes, 1),
    )

    output_path = output_dir / f"pre_{local_path.name}"
    await run_ffmpeg([
        "-i", str(local_path),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", str(_PREPROCESS_CRF),
        "-c:a", "aac",
        "-b:a", _PREPROCESS_AUDIO_BITRATE,
        "-y",
        str(output_path),
    ])

    new_size = output_path.stat().st_size
    log.info(
        "source_preprocessing_complete",
        name=local_path.name,
        original_gb=round(size_bytes / 1_073_741_824, 2),
        new_gb=round(new_size / 1_073_741_824, 2),
        reduction_pct=round((1 - new_size / size_bytes) * 100),
    )
    return output_path
