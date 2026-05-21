from dataclasses import dataclass

from src.config import Settings
from src.db.models import FormatProfile

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


def select_recipe(
    category: str,
    profile: FormatProfile,
    settings: Settings,
) -> CutRecipe:
    """Choose the appropriate layout recipe for a source video."""
    if profile.is_already_vertical:
        return CutRecipe(name="passthrough")

    if profile.has_facecam and not profile.facecam_unstable:
        return CutRecipe(
            name="split",
            facecam_top_pct=settings.FACECAM_TOP_PCT,
            facecam_position=profile.facecam_position or "top-left",
            facecam_size_pct=profile.facecam_size_pct or 0.15,
        )

    return CutRecipe(
        name="blur_bg",
        center_pct=settings.GAMEPLAY_CENTER_PCT,
    )
