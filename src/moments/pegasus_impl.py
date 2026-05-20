from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.db.models import Moment


class PegasusMomentsImpl:
    """
    Placeholder — implement after the Marengo vs. Pegasus bake-off
    documented in docs/model-bakeoff.md produces results on ≥5 real videos.
    """

    async def find_moments(
        self,
        index_id: str,
        source_video_id: int,
        run_id: int,
        session: AsyncSession,
        settings: Settings,
    ) -> list[Moment]:
        raise NotImplementedError(
            "PegasusMomentsImpl is not yet implemented. "
            "Set MOMENTS_PROVIDER=marengo in your environment."
        )
