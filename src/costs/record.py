from decimal import Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.costs.pricing import PRICES, PRICING_VERSION
from src.db.models import CostEvent

log = structlog.get_logger(__name__)


async def record_format_probe(
    session: AsyncSession,
    source_video_id: int,
    input_tokens: int,
    output_tokens: int,
) -> CostEvent:
    input_cost = Decimal(input_tokens) * PRICES["gemini_flash_input_per_token"]
    output_cost = Decimal(output_tokens) * PRICES["gemini_flash_output_per_token"]
    total = input_cost + output_cost

    event = CostEvent(
        source_video_id=source_video_id,
        step="ingest.format_probe",
        provider="google",
        model="gemini-2.5-flash",
        units=Decimal(input_tokens + output_tokens),
        unit_cost_usd=PRICES["gemini_flash_input_per_token"],
        total_cost_usd=total,
        metadata_json={
            "pricing_version": PRICING_VERSION,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )
    session.add(event)
    await session.flush()
    log.info(
        "cost_event_recorded",
        step="ingest.format_probe",
        provider="google",
        units=input_tokens + output_tokens,
        cost_usd=str(total),
    )
    return event


async def record_marengo_index(
    session: AsyncSession,
    source_video_id: int,
    detection_run_id: int,
    video_minutes: Decimal,
) -> CostEvent:
    unit_cost = PRICES["marengo_index_per_minute"]
    total = video_minutes * unit_cost

    event = CostEvent(
        source_video_id=source_video_id,
        detection_run_id=detection_run_id,
        step="indexer.marengo_index",
        provider="twelvelabs",
        model="marengo-3.0",
        units=video_minutes,
        unit_cost_usd=unit_cost,
        total_cost_usd=total,
        metadata_json={
            "pricing_version": PRICING_VERSION,
            "video_minutes": str(video_minutes),
        },
    )
    session.add(event)
    await session.flush()
    log.info(
        "cost_event_recorded",
        step="indexer.marengo_index",
        provider="twelvelabs",
        units=str(video_minutes),
        cost_usd=str(total),
    )
    return event


async def record_marengo_query(
    session: AsyncSession,
    source_video_id: int,
    detection_run_id: int,
    query_type: str,
) -> CostEvent:
    unit_cost = PRICES["marengo_search_per_query"]

    event = CostEvent(
        source_video_id=source_video_id,
        detection_run_id=detection_run_id,
        step="moments.marengo_query",
        provider="twelvelabs",
        model="marengo-3.0",
        units=Decimal("1"),
        unit_cost_usd=unit_cost,
        total_cost_usd=unit_cost,
        metadata_json={"pricing_version": PRICING_VERSION, "query_type": query_type},
    )
    session.add(event)
    await session.flush()
    log.info(
        "cost_event_recorded",
        step="moments.marengo_query",
        provider="twelvelabs",
        query_type=query_type,
        cost_usd=str(unit_cost),
    )
    return event


async def get_source_total_cost(session: AsyncSession, source_video_id: int) -> Decimal:
    result = await session.execute(
        select(func.coalesce(func.sum(CostEvent.total_cost_usd), 0)).where(
            CostEvent.source_video_id == source_video_id
        )
    )
    return Decimal(str(result.scalar_one()))


async def get_cost_breakdown(
    session: AsyncSession, source_video_id: int
) -> dict[str, Decimal]:
    """Returns {step: total_cost_usd} for all CostEvents of a source video."""
    result = await session.execute(
        select(CostEvent.step, func.sum(CostEvent.total_cost_usd))
        .where(CostEvent.source_video_id == source_video_id)
        .group_by(CostEvent.step)
    )
    return {row[0]: Decimal(str(row[1])) for row in result.fetchall()}
