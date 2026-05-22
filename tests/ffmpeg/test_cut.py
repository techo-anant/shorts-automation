"""
Tests for ffmpeg/cut.py — split (facecam) recipe.

A synthetic 1920×1080 source is generated once per session by FFmpeg's lavfi:
  - Blue background  → gameplay area
  - Red 480×270 box in the top-left corner  → facecam overlay (25% of frame width)

After cut_short with the split recipe the output must be 1080×1920 with:
  - Top strip (30% = 576px tall) entirely RED  — facecam cropped and ZOOMED to fill
  - Bottom strip (70% = 1344px tall) BLUE      — gameplay center-cropped

The key regression check is the RIGHT EDGE of the top strip.
With the old whole-frame-crop bug (facecam_size_pct treated as 0–100 percentage
instead of a 0.0–1.0 fraction) the "crop" covered the entire 1920×1080 source, so
the top strip showed a scaled-down copy of the whole frame — right edge was blue.
With the fix (correct fraction → 480×270 crop → 2.25× zoom) the right edge is red.
"""

import subprocess
from pathlib import Path

import pytest

from src.ffmpeg.cut import _facecam_crop_rect, cut_short
from src.ffmpeg.recipes import CutRecipe
from src.utils.ffmpeg import VideoInfo

# ---------------------------------------------------------------------------
# Fixture video parameters
# ---------------------------------------------------------------------------

_FACECAM_SIZE_PCT = 0.25          # fraction of frame width  (the fix expects 0.0–1.0)
_SRC_W, _SRC_H = 1920, 1080
_BOX_W = round(_SRC_W * _FACECAM_SIZE_PCT)   # 480
_BOX_H = round(_BOX_W * 9 / 16)              # 270  (16:9 facecam)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def facecam_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    3-second 1920×1080 MP4: blue background with a 480×270 red box at top-left.
    Generated once per test session via FFmpeg lavfi — no binary checked in.
    """
    path = tmp_path_factory.mktemp("fixtures") / "facecam_source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"color=c=0x0000ff:s={_SRC_W}x{_SRC_H}:d=3:r=30",
            "-vf", f"drawbox=x=0:y=0:w={_BOX_W}:h={_BOX_H}:color=0xff0000:t=fill",
            "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _sample_pixel(video_path: Path, x: int, y: int, frame_width: int = 1080) -> tuple[int, int, int]:
    """
    Return (R, G, B) for the pixel at (x, y) in the first frame of video_path.

    Extracts the full frame as raw RGB24 bytes and indexes directly — avoids the
    FFmpeg crop filter, which can fail with tiny output sizes on some builds.
    """
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(video_path),
            "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    offset = (y * frame_width + x) * 3
    data = result.stdout
    return int(data[offset]), int(data[offset + 1]), int(data[offset + 2])


def _is_red(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r > 150 and r > g + 100 and r > b + 100


def _is_blue(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return b > 150 and b > r + 100 and b > g + 100


# ---------------------------------------------------------------------------
# Unit tests: _facecam_crop_rect
# ---------------------------------------------------------------------------


class TestFacecamCropRect:
    def test_top_left_origin(self) -> None:
        w, h, x, y = _facecam_crop_rect("top-left", 0.25, 1920, 1080)
        assert w == 480
        assert h == 270
        assert x == 0
        assert y == 0

    def test_top_right_x_offset(self) -> None:
        w, h, x, y = _facecam_crop_rect("top-right", 0.25, 1920, 1080)
        assert x == 1920 - 480
        assert y == 0

    def test_bottom_left_y_offset(self) -> None:
        w, h, x, y = _facecam_crop_rect("bottom-left", 0.25, 1920, 1080)
        assert x == 0
        assert y == 1080 - 270

    def test_bottom_right_both_offsets(self) -> None:
        w, h, x, y = _facecam_crop_rect("bottom-right", 0.25, 1920, 1080)
        assert x == 1920 - 480
        assert y == 1080 - 270

    def test_center_position_centers_on_both_axes(self) -> None:
        w, h, x, y = _facecam_crop_rect("center", 0.25, 1920, 1080)
        assert x == (1920 - 480) // 2
        assert y == (1080 - 270) // 2

    def test_output_dimensions_are_always_even(self) -> None:
        # size_pct chosen to produce an odd intermediate dimension
        w, h, x, y = _facecam_crop_rect("top-left", 0.101, 1920, 1080)
        assert w % 2 == 0
        assert h % 2 == 0

    def test_oversized_fraction_clamped_to_half_frame(self) -> None:
        w, h, x, y = _facecam_crop_rect("top-left", 0.9, 1920, 1080)
        assert w <= 1920 // 2

    def test_zero_fraction_clamped_to_minimum_width(self) -> None:
        w, h, x, y = _facecam_crop_rect("top-left", 0.0, 1920, 1080)
        assert w >= 20
        assert h >= 12


# ---------------------------------------------------------------------------
# Integration test: cut_short with split recipe (real FFmpeg)
# ---------------------------------------------------------------------------


class TestSplitRecipeIntegration:
    async def test_facecam_is_zoomed_to_fill_top_strip_and_gameplay_fills_bottom(
        self,
        facecam_source: Path,
        tmp_path: Path,
    ) -> None:
        """
        Runs cut_short against the synthetic source and verifies pixel colors.

        Pass conditions (all must hold):
          1. Output is 1080×1920.
          2. Center of top strip is red   — facecam content is present.
          3. Right edge of top strip is red — facecam was ZOOMED to full width,
             not just placed as a small corner on a scaled-down full frame.
          4. Center of bottom strip is blue — gameplay content is correct.
        """
        output = tmp_path / "split_out.mp4"
        face_h = round(1920 * 0.30)   # 576 px

        recipe = CutRecipe(
            name="split",
            facecam_top_pct=0.30,
            facecam_position="top-left",
            facecam_size_pct=_FACECAM_SIZE_PCT,
        )
        info = VideoInfo(duration_seconds=3.0, width=_SRC_W, height=_SRC_H)

        await cut_short(
            source_path=facecam_source,
            output_path=output,
            start_seconds=0.0,
            end_seconds=3.0,
            recipe=recipe,
            info=info,
        )

        assert output.exists(), "cut_short did not produce an output file"

        # 1. Dimensions
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        out_w, out_h = probe.stdout.strip().split(",")
        assert int(out_w) == 1080, f"Expected width 1080, got {out_w}"
        assert int(out_h) == 1920, f"Expected height 1920, got {out_h}"

        # 2 & 3. Top strip — center and right edge must both be red
        top_center = _sample_pixel(output, 540, face_h // 2)
        top_right_edge = _sample_pixel(output, 1070, face_h // 2)

        assert _is_red(top_center), (
            f"Top strip center is not red: {top_center}. "
            "Expected the facecam (red box) to fill the top strip."
        )
        assert _is_red(top_right_edge), (
            f"Top strip right edge is not red: {top_right_edge}. "
            "The facecam was not zoomed to fill the full strip width — "
            "this is the whole-frame-crop regression."
        )

        # 4. Bottom strip center must be blue
        bottom_center_y = face_h + (1920 - face_h) // 2
        bottom_center = _sample_pixel(output, 540, bottom_center_y)

        assert _is_blue(bottom_center), (
            f"Bottom strip center is not blue: {bottom_center}. "
            "Expected gameplay (blue background) in the bottom strip."
        )
