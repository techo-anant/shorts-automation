from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from src.costs.pricing import PRICES, PRICING_VERSION
from src.costs.record import (
    get_cost_breakdown,
    get_source_total_cost,
    record_format_probe,
    record_marengo_index,
    record_marengo_query,
)
from src.db.models import CostEvent


def _make_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    return session


class TestRecordFormatProbe:
    async def test_writes_cost_event_with_correct_fields(self) -> None:
        session = _make_session()
        await record_format_probe(
            session=session,
            source_video_id=42,
            input_tokens=500,
            output_tokens=100,
        )

        session.add.assert_called_once()
        event = session.add.call_args[0][0]

        assert isinstance(event, CostEvent)
        assert event.source_video_id == 42
        assert event.step == "ingest.format_probe"
        assert event.provider == "google"
        assert event.model == "gemini-2.5-flash"
        assert event.units == Decimal("600")
        assert event.metadata_json["pricing_version"] == PRICING_VERSION
        assert event.metadata_json["input_tokens"] == 500
        assert event.metadata_json["output_tokens"] == 100
        session.flush.assert_called_once()

    async def test_total_cost_is_sum_of_input_and_output_costs(self) -> None:
        session = _make_session()
        await record_format_probe(
            session=session,
            source_video_id=1,
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        event = session.add.call_args[0][0]

        expected = (
            Decimal("1000000") * PRICES["gemini_flash_input_per_token"]
            + Decimal("1000000") * PRICES["gemini_flash_output_per_token"]
        )
        assert event.total_cost_usd == expected

    async def test_zero_tokens_produces_zero_cost(self) -> None:
        session = _make_session()
        await record_format_probe(
            session=session, source_video_id=1, input_tokens=0, output_tokens=0
        )
        event = session.add.call_args[0][0]
        assert event.total_cost_usd == Decimal("0")


class TestRecordMarengoIndex:
    async def test_writes_cost_event_with_correct_fields(self) -> None:
        session = _make_session()
        await record_marengo_index(
            session=session,
            source_video_id=5,
            detection_run_id=3,
            video_minutes=Decimal("45.5"),
        )
        event = session.add.call_args[0][0]

        assert isinstance(event, CostEvent)
        assert event.source_video_id == 5
        assert event.detection_run_id == 3
        assert event.step == "indexer.marengo_index"
        assert event.provider == "twelvelabs"
        assert event.model == "marengo-3.0"
        assert event.units == Decimal("45.5")
        assert event.total_cost_usd == Decimal("45.5") * PRICES["marengo_index_per_minute"]
        assert event.metadata_json["pricing_version"] == PRICING_VERSION
        assert event.metadata_json["video_minutes"] == "45.5"

    async def test_cost_scales_linearly_with_video_minutes(self) -> None:
        session = _make_session()
        await record_marengo_index(
            session=session,
            source_video_id=1,
            detection_run_id=1,
            video_minutes=Decimal("60"),
        )
        event = session.add.call_args[0][0]
        assert event.total_cost_usd == Decimal("60") * PRICES["marengo_index_per_minute"]


class TestRecordMarengoQuery:
    async def test_writes_cost_event_with_correct_fields(self) -> None:
        session = _make_session()
        await record_marengo_query(
            session=session,
            source_video_id=5,
            detection_run_id=3,
            query_type="shorts",
        )
        event = session.add.call_args[0][0]

        assert isinstance(event, CostEvent)
        assert event.step == "moments.marengo_query"
        assert event.provider == "twelvelabs"
        assert event.units == Decimal("1")
        assert event.total_cost_usd == PRICES["marengo_search_per_query"]
        assert event.metadata_json["query_type"] == "shorts"

    async def test_query_type_stored_in_metadata(self) -> None:
        session = _make_session()
        await record_marengo_query(
            session=session,
            source_video_id=1,
            detection_run_id=1,
            query_type="highlight_reel",
        )
        event = session.add.call_args[0][0]
        assert event.metadata_json["query_type"] == "highlight_reel"

    async def test_cost_is_fixed_per_query_regardless_of_type(self) -> None:
        shorts_session = _make_session()
        highlight_session = _make_session()

        await record_marengo_query(
            session=shorts_session, source_video_id=1, detection_run_id=1, query_type="shorts"
        )
        await record_marengo_query(
            session=highlight_session,
            source_video_id=1,
            detection_run_id=1,
            query_type="highlight_reel",
        )

        shorts_cost = shorts_session.add.call_args[0][0].total_cost_usd
        highlight_cost = highlight_session.add.call_args[0][0].total_cost_usd
        assert shorts_cost == highlight_cost


class TestGetSourceTotalCost:
    async def test_returns_sum_of_all_cost_events(self) -> None:
        session = _make_session()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = Decimal("3.14")
        session.execute.return_value = mock_result

        total = await get_source_total_cost(session, source_video_id=1)
        assert total == Decimal("3.14")

    async def test_returns_zero_when_no_events_exist(self) -> None:
        session = _make_session()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        session.execute.return_value = mock_result

        total = await get_source_total_cost(session, source_video_id=99)
        assert total == Decimal("0")


class TestGetCostBreakdown:
    async def test_returns_dict_keyed_by_step(self) -> None:
        session = _make_session()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("ingest.format_probe", Decimal("0.001")),
            ("indexer.marengo_index", Decimal("4.200")),
            ("moments.marengo_query", Decimal("0.008")),
        ]
        session.execute.return_value = mock_result

        breakdown = await get_cost_breakdown(session, source_video_id=1)

        assert breakdown["ingest.format_probe"] == Decimal("0.001")
        assert breakdown["indexer.marengo_index"] == Decimal("4.200")
        assert breakdown["moments.marengo_query"] == Decimal("0.008")

    async def test_returns_empty_dict_when_no_events(self) -> None:
        session = _make_session()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        session.execute.return_value = mock_result

        breakdown = await get_cost_breakdown(session, source_video_id=99)
        assert breakdown == {}
