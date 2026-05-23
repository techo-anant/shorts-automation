from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.config import Settings
from src.db.models import FormatProfile

if TYPE_CHECKING:
    from src.db.models import CropRequest

# Output canvas for all YouTube Shorts
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920


@dataclass(frozen=True)
class CutRecipe:
    """Immutable descriptor for a single FFmpeg layout recipe."""

    # "blur_bg" | "split" | "passthrough"
    name: str
    # blur_bg: fraction of OUTPUT_HEIGHT the gameplay fills (rest is blur bars)
    center_pct: float = 0.70
    # split: fraction of OUTPUT_HEIGHT allocated to the facecam strip at the top
    facecam_top_pct: float = 0.30
    # split: where in the source frame the facecam lives ("top-left" etc.)
    facecam_position: str = "top-left"
    # split: facecam size as a fraction of the source frame area (used to estimate crop rect)
    facecam_size_pct: float = 0.15
    # split: optional manual crop override (pixels on the operator's reference frame)
    # When manual_crop_w is set, _facecam_crop_rect is bypassed entirely.
    manual_crop_x: int | None = None
    manual_crop_y: int | None = None
    manual_crop_w: int | None = None
    manual_crop_h: int | None = None
    # Resolution of the frame the operator measured on — used to scale coords to source resolution
    manual_crop_ref_w: int | None = None
    manual_crop_ref_h: int | None = None


def select_recipe(
    category: str,
    profile: FormatProfile,
    settings: Settings,
    crop_request: CropRequest | None = None,
) -> CutRecipe:
    """Choose the appropriate layout recipe for a source video."""
    if profile.is_already_vertical:
        return CutRecipe(name="passthrough")

    if profile.has_facecam and not profile.facecam_unstable:
        manual_x = manual_y = manual_w = manual_h = manual_ref_w = manual_ref_h = None
        if crop_request is not None and crop_request.status == "provided":
            manual_x = crop_request.crop_x
            manual_y = crop_request.crop_y
            manual_w = crop_request.crop_w
            manual_h = crop_request.crop_h
            manual_ref_w = crop_request.ref_width
            manual_ref_h = crop_request.ref_height
        return CutRecipe(
            name="split",
            facecam_top_pct=settings.FACECAM_TOP_PCT,
            facecam_position=profile.facecam_position or "top-left",
            facecam_size_pct=profile.facecam_size_pct or 0.15,
            manual_crop_x=manual_x,
            manual_crop_y=manual_y,
            manual_crop_w=manual_w,
            manual_crop_h=manual_h,
            manual_crop_ref_w=manual_ref_w,
            manual_crop_ref_h=manual_ref_h,
        )

    return CutRecipe(
        name="blur_bg",
        center_pct=settings.GAMEPLAY_CENTER_PCT,
    )
