"""
FFmpeg cut + reformat for YouTube Shorts (9:16, 1080x1920).

All FFmpeg arguments are passed as argv lists — never shell-concatenated.
Timestamps and paths from external sources never reach a shell string.
"""

from pathlib import Path

import structlog

from src.errors import FFmpegCutError
from src.ffmpeg.recipes import OUTPUT_HEIGHT, OUTPUT_WIDTH, CutRecipe
from src.utils.ffmpeg import FFmpegError, VideoInfo, run_ffmpeg

log = structlog.get_logger(__name__)


async def cut_short(
    source_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
    recipe: CutRecipe,
    info: VideoInfo,
) -> None:
    """
    Cut [start_seconds, end_seconds] from source_path, apply the given recipe,
    and write a 1080×1920 H.264/AAC MP4 to output_path.
    """
    log.info(
        "cutting_short",
        recipe=recipe.name,
        start=start_seconds,
        end=end_seconds,
        output=str(output_path),
    )

    try:
        if recipe.name == "blur_bg":
            await _cut_blur_bg(source_path, output_path, start_seconds, end_seconds, recipe, info)
        elif recipe.name == "split":
            await _cut_split(source_path, output_path, start_seconds, end_seconds, recipe, info)
        elif recipe.name == "passthrough":
            await _cut_passthrough(source_path, output_path, start_seconds, end_seconds)
        else:
            raise ValueError(f"Unknown recipe: {recipe.name}")
    except FFmpegError as exc:
        raise FFmpegCutError(f"FFmpeg cut failed [{recipe.name}]: {exc}") from exc


# ---------------------------------------------------------------------------
# Recipe implementations
# ---------------------------------------------------------------------------


async def _cut_blur_bg(
    source_path: Path,
    output_path: Path,
    start: float,
    end: float,
    recipe: CutRecipe,
    info: VideoInfo,
) -> None:
    """
    Blur-background recipe:
    - Gameplay zoomed/center-cropped to fill 1080 × content_h (content_h = 1920 * center_pct)
    - Blurred background (same source scaled to fill 1080×1920, heavy boxblur) behind it
    - At center_pct=1.0 the blur layer is still rendered but covered entirely by fg
    """
    content_h = round(OUTPUT_HEIGHT * recipe.center_pct)
    # Ensure content_h is even (FFmpeg yuv420p requirement)
    if content_h % 2 != 0:
        content_h -= 1

    overlay_y = (OUTPUT_HEIGHT - content_h) // 2

    # Background: scale source to fill full canvas, blur
    bg_filter = (
        f"[0:v]"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},"
        f"boxblur=luma_radius=20:luma_power=2"
        f"[bg]"
    )

    # Foreground: scale source to fill content zone width×height (zoom-in center crop)
    fg_filter = (
        f"[0:v]"
        f"scale={OUTPUT_WIDTH}:{content_h}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_WIDTH}:{content_h}"
        f"[fg]"
    )

    overlay_filter = f"[bg][fg]overlay=0:{overlay_y}[out]"

    filter_complex = f"{bg_filter};{fg_filter};{overlay_filter}"

    await run_ffmpeg(
        [
            "-ss", str(start),
            "-to", str(end),
            "-i", str(source_path),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            *_encode_args(),
            str(output_path),
        ]
    )


async def _cut_split(
    source_path: Path,
    output_path: Path,
    start: float,
    end: float,
    recipe: CutRecipe,
    info: VideoInfo,
) -> None:
    """
    Split recipe: facecam strip on top, gameplay center-cropped on bottom.
    No blur in either section.
    """
    face_h = round(OUTPUT_HEIGHT * recipe.facecam_top_pct)
    if face_h % 2 != 0:
        face_h -= 1
    game_h = OUTPUT_HEIGHT - face_h
    if game_h % 2 != 0:
        game_h -= 1

    # Estimate facecam crop rect from source frame
    src_w, src_h = info.width, info.height
    face_crop_w, face_crop_h, face_crop_x, face_crop_y = _facecam_crop_rect(
        recipe.facecam_position, recipe.facecam_size_pct, src_w, src_h
    )

    # Facecam: crop the facecam region, scale to fill 1080×face_h
    face_filter = (
        f"[0:v]"
        f"crop={face_crop_w}:{face_crop_h}:{face_crop_x}:{face_crop_y},"
        f"scale={OUTPUT_WIDTH}:{face_h}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_WIDTH}:{face_h}"
        f"[face]"
    )

    # Gameplay: scale source to fill 1080 wide, center-crop to game_h tall
    game_filter = (
        f"[0:v]"
        f"scale={OUTPUT_WIDTH}:{game_h}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_WIDTH}:{game_h}"
        f"[game]"
    )

    # Stack vertically: facecam on top, gameplay on bottom
    stack_filter = "[face][game]vstack=inputs=2[out]"

    filter_complex = f"{face_filter};{game_filter};{stack_filter}"

    await run_ffmpeg(
        [
            "-ss", str(start),
            "-to", str(end),
            "-i", str(source_path),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            *_encode_args(),
            str(output_path),
        ]
    )


async def _cut_passthrough(
    source_path: Path,
    output_path: Path,
    start: float,
    end: float,
) -> None:
    """
    Passthrough recipe: source is already vertical — scale/pad to 1080×1920 and re-encode.
    """
    filter_complex = (
        f"[0:v]"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"
        f"[out]"
    )

    await run_ffmpeg(
        [
            "-ss", str(start),
            "-to", str(end),
            "-i", str(source_path),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            *_encode_args(),
            str(output_path),
        ]
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_args() -> list[str]:
    """Standard YouTube Shorts encode settings."""
    return [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-y",
    ]


def _facecam_crop_rect(
    position: str,
    size_pct: float,
    src_w: int,
    src_h: int,
) -> tuple[int, int, int, int]:
    """
    Estimate (crop_w, crop_h, crop_x, crop_y) for the facecam region.

    size_pct is the fraction of frame WIDTH the facecam spans (0.0–1.0 decimal).
    Height is inferred assuming a roughly 16:9 facecam aspect ratio.
    Position is one of: top-left, top-right, bottom-left, bottom-right, center.
    """
    face_w = round(src_w * size_pct)
    face_h = round(face_w * 9 / 16)

    # Clamp to sensible bounds (at least 20px, at most half the frame)
    face_w = max(20, min(face_w, src_w // 2))
    face_h = max(12, min(face_h, src_h // 2))

    pos = position.lower().replace("_", "-")

    if "left" not in pos and "right" not in pos:
        # "center" or anything unrecognised — place in the horizontal centre
        x = (src_w - face_w) // 2
    else:
        x = 0 if "left" in pos else src_w - face_w

    if "top" not in pos and "bottom" not in pos:
        y = (src_h - face_h) // 2
    else:
        y = 0 if "top" in pos else src_h - face_h

    # Ensure even dimensions (FFmpeg yuv420p requirement)
    if face_w % 2 != 0:
        face_w -= 1
    if face_h % 2 != 0:
        face_h -= 1

    return face_w, face_h, x, y
