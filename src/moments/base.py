from typing import TYPE_CHECKING, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.db.models import Moment

if TYPE_CHECKING:
    pass


class MomentsProvider(Protocol):
    """
    Swappable interface for moment detection.
    MarengoMomentsImpl satisfies this now; PegasusMomentsImpl will replace it
    after the bake-off documented in docs/model-bakeoff.md.
    """

    async def find_moments(
        self,
        index_id: str,
        source_video_id: int,
        run_id: int,
        session: AsyncSession,
        settings: Settings,
    ) -> list[Moment]: ...


def get_moments_provider(settings: Settings) -> MomentsProvider:
    if settings.MOMENTS_PROVIDER == "marengo":
        from src.moments.marengo_impl import MarengoMomentsImpl
        return MarengoMomentsImpl()
    if settings.MOMENTS_PROVIDER == "pegasus":
        from src.moments.pegasus_impl import PegasusMomentsImpl
        return PegasusMomentsImpl()
    raise ValueError(
        f"Unknown MOMENTS_PROVIDER '{settings.MOMENTS_PROVIDER}'. "
        "Valid values: marengo, pegasus"
    )
