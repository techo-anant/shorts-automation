from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    DATABASE_URL: str

    # Google Drive
    DRIVE_SHORTS_ROOT_FOLDER_ID: str

    # Google OAuth (Drive + YouTube)
    GOOGLE_OAUTH_CLIENT_ID: str
    GOOGLE_OAUTH_CLIENT_SECRET: str
    GOOGLE_OAUTH_REFRESH_TOKEN: str

    # AI services
    GEMINI_API_KEY: str
    TWELVELABS_API_KEY: str

    # Discord
    DISCORD_WEBHOOK_URL: str
    DISCORD_SUCCESS_PINGS: bool = False
    DISCORD_BOT_TOKEN: str
    DISCORD_APPROVAL_CHANNEL_ID: str
    # Hours before an unreviewed approval times out and auto-rejects
    APPROVAL_TIMEOUT_HOURS: int = 48
    # Hours before an unanswered facecam crop request falls back to auto-detection
    CROP_TIMEOUT_HOURS: int = 24

    # Polling
    POLL_INTERVAL_SECONDS: int = 60

    # FFmpeg layout recipes
    # Fraction of 1920px height used by gameplay in blur-bg recipe (rest = blur bars).
    # Set to 1.0 for a pure center-crop with no blur bars.
    GAMEPLAY_CENTER_PCT: float = 0.70
    # Fraction of 1920px height allocated to the facecam strip in the split recipe.
    FACECAM_TOP_PCT: float = 0.30

    # Cost circuit breakers (USD)
    COST_WARN_USD: Decimal = Decimal("5.00")
    COST_HARD_STOP_USD: Decimal = Decimal("8.00")

    # HTTP server (Railway injects PORT)
    PORT: int = 8080

    @property
    def async_database_url(self) -> str:
        url = self.DATABASE_URL
        # Railway uses postgres:// scheme; SQLAlchemy asyncpg driver requires postgresql+asyncpg://
        if url.startswith("postgres://"):
            return "postgresql+asyncpg://" + url[len("postgres://"):]
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            return "postgresql+asyncpg://" + url[len("postgresql://"):]
        return url


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
