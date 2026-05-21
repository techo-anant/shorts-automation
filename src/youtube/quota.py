"""YouTube Data API daily quota tracking via the QuotaUsage DB table."""

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import QuotaUsage
from src.errors import YouTubeQuotaError

log = structlog.get_logger(__name__)

_PROVIDER = "youtube"
_DAILY_LIMIT = 10_000
_UNITS_PER_UPLOAD = 1_600
_WARN_THRESHOLD = 8_000  # 80% of daily limit


async def check_and_reserve_upload_quota(session: AsyncSession) -> int:
    """
    Check that there is room for one upload (1,600 units).
    Increments the quota counter atomically and returns the new total.
    Raises QuotaExhausted if there is no room.
    """
    today = datetime.now(UTC).date()

    result = await session.execute(
        select(QuotaUsage).where(
            QuotaUsage.provider == _PROVIDER,
            QuotaUsage.date == today,
        )
    )
    row = result.scalar_one_or_none()
    current = row.units_used if row is not None else 0

    if current + _UNITS_PER_UPLOAD > _DAILY_LIMIT:
        raise YouTubeQuotaError(
            f"YouTube quota exhausted: {current}/{_DAILY_LIMIT} units used today. "
            "Approved shorts will upload tomorrow."
        )

    # Upsert: create or increment
    stmt = insert(QuotaUsage).values(
        provider=_PROVIDER,
        date=today,
        units_used=_UNITS_PER_UPLOAD,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_quota_usage_provider_date",
        set_={"units_used": QuotaUsage.units_used + _UNITS_PER_UPLOAD},
    )
    await session.execute(stmt)

    new_total = current + _UNITS_PER_UPLOAD
    log.info(
        "youtube_quota_reserved",
        units_used=new_total,
        daily_limit=_DAILY_LIMIT,
    )

    if new_total >= _WARN_THRESHOLD:
        log.warning(
            "youtube_quota_warning",
            units_used=new_total,
            daily_limit=_DAILY_LIMIT,
            pct=round(new_total / _DAILY_LIMIT * 100),
        )

    return new_total


async def get_quota_used_today(session: AsyncSession) -> int:
    today = datetime.now(UTC).date()
    result = await session.execute(
        select(QuotaUsage.units_used).where(
            QuotaUsage.provider == _PROVIDER,
            QuotaUsage.date == today,
        )
    )
    val = result.scalar_one_or_none()
    return val if val is not None else 0
