"""
Typed exception hierarchy for the shorts-automation pipeline.

Every error that escapes a pipeline step should be one of these types.
PipelineError is the abstract base — never raise it directly.

Each leaf class carries:
  step     — logical pipeline step (e.g. "pegasus.describe")
  provider — external system involved (e.g. "twelvelabs")

The to_discord() method formats a human-readable message for the operator.
"""


class PipelineError(Exception):
    """Abstract base. Subclass, never instantiate directly."""

    step: str = "unknown"
    provider: str = "unknown"

    def __init__(self, message: str, *, response_body: str | None = None) -> None:
        self.response_body = response_body
        super().__init__(message)

    def to_discord(self) -> str:
        lines = [
            f"**{type(self).__name__}** [{self.provider} › {self.step}]",
            str(self),
        ]
        if self.response_body:
            lines.append(self.response_body[:400])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pegasus / TwelveLabs
# ---------------------------------------------------------------------------


class PegasusError(PipelineError):
    provider = "twelvelabs"


class PegasusIndexError(PegasusError):
    """Failed to upload or access a Pegasus asset/index."""
    step = "pegasus.index"


class PegasusDescribeError(PegasusError):
    """Pegasus analyze_async call failed or returned bad data."""
    step = "pegasus.describe"


class PegasusQuotaError(PegasusError):
    """TwelveLabs rate limit or quota exhausted."""
    step = "pegasus.quota"


# ---------------------------------------------------------------------------
# Gemini / Google AI
# ---------------------------------------------------------------------------


class GeminiError(PipelineError):
    provider = "gemini"


class GeminiProbeError(GeminiError):
    """Gemini format-probe call (frame analysis) failed."""
    step = "gemini.format_probe"


class GeminiSelectionError(GeminiError):
    """Gemini clip-selection call failed or returned invalid JSON."""
    step = "gemini.clip_selection"


class GeminiQuotaError(GeminiError):
    """Gemini rate limit or quota exhausted (429)."""
    step = "gemini.quota"


# ---------------------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------------------


class FFmpegPipelineError(PipelineError):
    provider = "ffmpeg"


class FFmpegProbeError(FFmpegPipelineError):
    """ffprobe subprocess failed."""
    step = "ffmpeg.probe"


class FFmpegCutError(FFmpegPipelineError):
    """FFmpeg cut/reformat subprocess failed."""
    step = "ffmpeg.cut"


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------


class DriveError(PipelineError):
    provider = "google_drive"


class DriveAuthError(DriveError):
    """Google OAuth refresh or Drive permission error (401/403)."""
    step = "drive.auth"


class DriveDownloadError(DriveError):
    """Failed to download a file from Drive."""
    step = "drive.download"


class DriveUploadError(DriveError):
    """Failed to upload a file to Drive."""
    step = "drive.upload"


class DrivePreviewTimeoutError(DriveError):
    """Drive video transcoding did not complete within the timeout (Phase 17)."""
    step = "drive.preview_timeout"


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------


class DiscordError(PipelineError):
    provider = "discord"


class DiscordPostError(DiscordError):
    """Failed to post or edit a Discord message (load-bearing approval flow)."""
    step = "discord.post"


class DiscordReactionError(DiscordError):
    """Failed to add reactions or read reaction state from Discord."""
    step = "discord.reaction"


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------


class YouTubeError(PipelineError):
    provider = "youtube"


class YouTubeUploadError(YouTubeError):
    """YouTube videos.insert call failed."""
    step = "youtube.upload"


class YouTubeQuotaError(YouTubeError):
    """Daily YouTube Data API quota would be exceeded."""
    step = "youtube.quota"


class YouTubeAuthError(YouTubeError):
    """YouTube OAuth refresh or permission error (401/403)."""
    step = "youtube.auth"
