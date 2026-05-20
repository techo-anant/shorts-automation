import tempfile
from pathlib import Path

import structlog
from google import genai
from google.genai import types
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.costs.record import record_format_probe
from src.db.models import FormatProfile, SourceVideo
from src.ingest.frames import extract_sample_frames
from src.utils.ffmpeg import VideoInfo

log = structlog.get_logger(__name__)

# Facecam detection requires ≥ this many frames to agree before we trust the result.
# If fewer agree, we mark the profile as unstable and default has_facecam=False.
_FACECAM_AGREEMENT_THRESHOLD = 4

_FRAME_ANALYSIS_PROMPT = """\
You are analysing a single frame from a video created by a YouTube content creator.

Your task is to determine whether there is a FACECAM overlay visible in this frame.

IMPORTANT — definition of facecam:
A facecam is a live webcam feed of the REAL, PHYSICAL human creator who made this video.
It appears as a small rectangular, circular, or irregularly-shaped overlay, usually in a corner
of the screen, showing the creator's actual face and real-world surroundings (desk, room, lighting).

Do NOT classify any of the following as a facecam:
- In-game character models, agents, heroes, or avatars (e.g. Valorant agents, Minecraft skins)
- Character portraits, ability previews, or HUD icons
- NPC faces or cinematic cutscene characters
- Animated or illustrated faces of any kind
- Any face that is clearly part of the game world

Only return has_facecam=true if you can see an unmistakable real-world webcam feed of a human.
When in doubt, return has_facecam=false.

Also return:
- facecam_position: where the overlay sits ("top-left", "top-right", "bottom-left", "bottom-right",
  "center", or null if no facecam)
- facecam_size_pct: approximate percentage of the total frame area occupied by the facecam overlay
  (0.0–100.0, or null if no facecam)
- is_already_vertical: true if the video is already in portrait/vertical orientation (taller than wide)
"""


class _FrameResult(BaseModel):
    has_facecam: bool
    facecam_position: str | None
    facecam_size_pct: float | None
    is_already_vertical: bool


class FormatProbeResult(BaseModel):
    has_facecam: bool
    facecam_position: str | None
    facecam_size_pct: float | None
    source_aspect_ratio: str
    is_already_vertical: bool
    facecam_unstable: bool


async def run_format_probe(
    session: AsyncSession,
    source_video: SourceVideo,
    video_path: Path,
    info: VideoInfo,
    settings: Settings,
) -> FormatProbeResult:
    """
    Extract 5 frames, send each to Gemini Flash for facecam/format analysis,
    aggregate results, write FormatProfile + CostEvent rows, and return the result.
    Frame files are deleted before this function returns.
    """
    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    with tempfile.TemporaryDirectory() as frame_dir:
        frame_paths = await extract_sample_frames(video_path, Path(frame_dir), info)
        frame_results, total_input_tokens, total_output_tokens = await _analyse_frames(
            client, frame_paths
        )

    result = _aggregate_frame_results(frame_results, info)

    log.info(
        "format_probe_complete",
        source_id=source_video.id,
        has_facecam=result.has_facecam,
        facecam_unstable=result.facecam_unstable,
        aspect_ratio=result.source_aspect_ratio,
        is_already_vertical=result.is_already_vertical,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
    )

    profile = FormatProfile(
        source_video_id=source_video.id,
        has_facecam=result.has_facecam,
        facecam_position=result.facecam_position,
        facecam_size_pct=result.facecam_size_pct,
        source_aspect_ratio=result.source_aspect_ratio,
        is_already_vertical=result.is_already_vertical,
        facecam_unstable=result.facecam_unstable,
    )
    session.add(profile)

    await record_format_probe(
        session=session,
        source_video_id=source_video.id,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
    )

    await session.flush()
    return result


async def _analyse_frames(
    client: genai.Client,
    frame_paths: list[Path],
) -> tuple[list[_FrameResult], int, int]:
    """Send each frame to Gemini and return per-frame results plus cumulative token counts."""
    results: list[_FrameResult] = []
    total_input = 0
    total_output = 0

    for frame_path in frame_paths:
        frame_bytes = frame_path.read_bytes()

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=frame_bytes, mime_type="image/png"),
                _FRAME_ANALYSIS_PROMPT,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_FrameResult,
                temperature=0,
            ),
        )

        parsed: _FrameResult = response.parsed  # type: ignore[assignment]
        results.append(parsed)

        usage = response.usage_metadata
        if usage:
            total_input += usage.prompt_token_count or 0
            total_output += usage.candidates_token_count or 0

        log.info(
            "frame_analysed",
            frame=frame_path.name,
            has_facecam=parsed.has_facecam,
            position=parsed.facecam_position,
        )

    return results, total_input, total_output


def _aggregate_frame_results(results: list[_FrameResult], info: VideoInfo) -> FormatProbeResult:
    """
    Combine per-frame Gemini results into a single FormatProbeResult.
    Facecam decision requires ≥4/5 frames to agree; otherwise marked unstable.
    Aspect ratio always comes from FFprobe (more reliable than Gemini for this).
    """
    facecam_votes = sum(1 for r in results if r.has_facecam)
    facecam_unstable = False

    if facecam_votes >= _FACECAM_AGREEMENT_THRESHOLD:
        has_facecam = True
    elif facecam_votes == 0:
        has_facecam = False
    else:
        # Disagreement — fall back to False and flag as unstable
        has_facecam = False
        facecam_unstable = True
        log.warning(
            "facecam_detection_unstable",
            votes_for=facecam_votes,
            total_frames=len(results),
        )

    # Pick position/size from the first frame that detected a facecam
    facecam_position: str | None = None
    facecam_size_pct: float | None = None
    if has_facecam:
        for r in results:
            if r.has_facecam and r.facecam_position:
                facecam_position = r.facecam_position
                facecam_size_pct = r.facecam_size_pct
                break

    # Vertical orientation: majority vote across frames
    vertical_votes = sum(1 for r in results if r.is_already_vertical)
    is_already_vertical = vertical_votes > len(results) / 2

    return FormatProbeResult(
        has_facecam=has_facecam,
        facecam_position=facecam_position,
        facecam_size_pct=facecam_size_pct,
        source_aspect_ratio=info.aspect_ratio,
        is_already_vertical=is_already_vertical,
        facecam_unstable=facecam_unstable,
    )
