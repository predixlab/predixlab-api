"""Unit tests for the pivot picks service."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.config import settings
from app.models.pivot import PickEvent, PivotPick, pick_from_document
from app.services.pivot_service import PUBLIC_PICK_EVENTS, PivotService


def _document(
    symbol: str = "AAPL",
    direction: str = "long",
    rank: int = 1,
    action: str = "STRONG_BUY",
    score: float = 0.31,
    date: str = "2024-01-02",
    realized: float | None = None,
):
    return {
        "_id": "abc123",
        "market": "us",
        "date": date,
        "symbol": symbol,
        "event": "BUY_TURN" if direction == "long" else "SELL_TURN",
        "kind": "reversal",
        "direction": direction,
        "rank": rank,
        "horizon": 10,
        "tag": "default",
        "generated_at": datetime(2024, 1, 3, 2, 0),
        "action": action,
        "score": score,
        "confidence": 0.98,
        "swing_position": 0.12,
        "p_up": 0.61,
        "sustained_bullish": 0.82,
        "rationale": "RSI is oversold",
        "close": 1.42,
        "price": 189.5,
        "forward_return": realized,
        "signal_return": realized,
        "excess_return": None if realized is None else realized - 0.005,
    }


class _AsyncCursor:
    """Minimal async cursor supporting the chained calls the service makes."""

    def __init__(self, documents):
        self.documents = list(documents)
        self.sorted_by = None
        self.limited = None

    def sort(self, key, direction=1):
        self.sorted_by = (key, direction)
        return self

    def limit(self, count):
        self.limited = count
        return self

    def __aiter__(self):
        async def generator():
            for document in self.documents:
                yield document

        return generator()


def _collection(documents=None) -> MagicMock:
    """A collection double modelling pymongo's *async* API faithfully.

    ``AsyncCollection.aggregate`` is a coroutine function that must be awaited
    before its cursor can be iterated, while ``find`` returns a cursor directly.
    A double that hands back an iterable from both lets un-awaited aggregate
    calls pass in tests and fail against a real database.
    """
    collection = MagicMock()
    collection.find = MagicMock(return_value=_AsyncCursor(documents or []))
    collection.aggregate = AsyncMock(return_value=_AsyncCursor(documents or []))
    return collection


def _service(collection) -> PivotService:
    service = PivotService()
    service.db = MagicMock()
    service.db.pivot_picks = collection
    service.db.pivot_runs = collection
    return service


class TestPickFromDocument:
    def test_public_events_only_include_trade_signals(self):
        assert {event.value for event in PickEvent} == {
            "BUY_TURN",
            "SELL_TURN",
            "TREND_BUY",
            "BASE_BREAKOUT",
        }

    def test_drops_unknown_keys(self):
        pick = pick_from_document(_document())
        assert isinstance(pick, PivotPick)
        assert pick.symbol == "AAPL"
        assert pick.direction == "long"
        assert pick.rank == 1

    def test_defaults_the_market(self):
        document = _document()
        document.pop("market")
        assert pick_from_document(document).market == "us"

    def test_carries_the_forward_return(self):
        pick = pick_from_document(_document(realized=0.055))
        assert pick.signal_return == pytest.approx(0.055)
        assert pick.excess_return == pytest.approx(0.05)

    def test_open_signals_have_no_outcome(self):
        """Recent signals are still open, not missing."""
        assert pick_from_document(_document()).signal_return is None

class TestQueryFilters:
    def test_free_users_get_a_date_ceiling(self):
        service = _service(MagicMock())
        query = service._base_filter("us", is_premium=False)
        cutoff = (datetime.now() - timedelta(days=settings.free_user_days_limit)).strftime("%Y-%m-%d")
        assert query["date"]["$lte"] == cutoff

    def test_premium_users_get_no_ceiling(self):
        service = _service(MagicMock())
        assert service._base_filter("us", is_premium=True) == {
            "market": "us",
            "event": {"$in": PUBLIC_PICK_EVENTS},
        }


class TestGetAvailableDates:
    @pytest.mark.asyncio
    async def test_returns_dates_newest_first(self):
        collection = _collection()
        collection.aggregate = AsyncMock(
            return_value=_AsyncCursor([{"_id": "2024-01-03"}, {"_id": "2024-01-02"}])
        )
        service = _service(collection)

        dates = await service.get_available_dates("us", limit=60, is_premium=True)

        assert dates == ["2024-01-03", "2024-01-02"]

    @pytest.mark.asyncio
    async def test_aggregate_is_awaited(self):
        """Guards the pymongo-async contract: `aggregate` is a coroutine fn."""
        collection = _collection()
        collection.aggregate = AsyncMock(return_value=_AsyncCursor([]))
        service = _service(collection)

        await service.get_available_dates("us", is_premium=True)

        collection.aggregate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_database_failure_returns_empty(self):
        collection = _collection()
        collection.aggregate = AsyncMock(side_effect=RuntimeError("boom"))
        assert await _service(collection).get_available_dates("us") == []


class TestGetPicksByDate:
    @pytest.mark.asyncio
    async def test_reads_deployed_underscore_collection(self):
        collection = _collection(
            [
                _document(
                    symbol="EXPE",
                    action="BUY",
                    score=0.0163,
                    date="2023-10-23",
                )
            ]
        )
        database = MagicMock()
        database.pivot_picks = collection
        service = PivotService()
        service.db = database

        picks = await service.get_picks_by_date("2023-10-23", market="us")

        assert [pick.symbol for pick in picks] == ["EXPE"]
        collection.find.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_models(self):
        collection = _collection([_document(), _document("MSFT", "short", 1, "STRONG_SELL", -0.2)])
        service = _service(collection)

        picks = await service.get_picks_by_date("2024-01-02", market="us")

        assert [p.symbol for p in picks] == ["AAPL", "MSFT"]

    @pytest.mark.asyncio
    async def test_orders_by_side_then_rank_in_application(self):
        cursor = _AsyncCursor(
            [
                _document("MSFT", "short", 2, "STRONG_SELL", -0.2),
                _document("GOOGL", "long", 2),
                _document("TSLA", "short", 1, "STRONG_SELL", -0.3),
                _document("AAPL", "long", 1),
            ]
        )
        collection = MagicMock()
        collection.find = MagicMock(return_value=cursor)
        service = _service(collection)

        picks = await service.get_picks_by_date("2024-01-02")

        assert [pick.symbol for pick in picks] == ["AAPL", "GOOGL", "TSLA", "MSFT"]
        assert cursor.sorted_by is None

    @pytest.mark.asyncio
    async def test_limit_is_applied_per_side(self):
        collection = _collection(
            [
                _document("AAPL", "long", 1),
                _document("GOOGL", "long", 2),
                _document("TSLA", "short", 1, "STRONG_SELL", -0.3),
                _document("MSFT", "short", 2, "STRONG_SELL", -0.2),
            ]
        )

        picks = await _service(collection).get_picks_by_date("2024-01-02", limit=1)

        assert [pick.symbol for pick in picks] == ["AAPL", "TSLA"]

    @pytest.mark.asyncio
    async def test_direction_filter_is_applied(self):
        collection = _collection()
        service = _service(collection)

        await service.get_picks_by_date("2024-01-02", direction="long")

        assert collection.find.call_args.args[0]["direction"] == "long"

    @pytest.mark.asyncio
    async def test_internal_detection_markers_are_excluded(self):
        collection = _collection()
        service = _service(collection)

        await service.get_picks_by_date("2024-01-02")

        event_filter = collection.find.call_args.args[0]["event"]
        assert event_filter == {"$in": PUBLIC_PICK_EVENTS}
        assert "BOTTOM_TURN" not in event_filter["$in"]
        assert "TOP_TURN" not in event_filter["$in"]

    @pytest.mark.asyncio
    async def test_database_failure_returns_empty(self):
        collection = MagicMock()
        collection.find = MagicMock(side_effect=RuntimeError("boom"))
        assert await _service(collection).get_picks_by_date("2024-01-02") == []

    @pytest.mark.asyncio
    async def test_strict_database_failure_is_reraised(self):
        collection = MagicMock()
        collection.find = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await _service(collection).get_picks_by_date(
                "2024-01-02", strict=True
            )

    @pytest.mark.asyncio
    async def test_strict_count_failure_is_reraised(self):
        collection = _collection()
        collection.aggregate = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await _service(collection).count_events(
                "2024-01-02", strict=True
            )

    @pytest.mark.asyncio
    async def test_legacy_count_failure_returns_an_empty_mapping(self):
        collection = _collection()
        collection.aggregate = AsyncMock(side_effect=RuntimeError("boom"))

        assert await _service(collection).count_events("2024-01-02") == {}


class TestGetSymbolHistory:
    @pytest.mark.asyncio
    async def test_history_is_returned_oldest_first(self):
        collection = _collection(
            [_document(date="2024-01-03"), _document(date="2024-01-02")]
        )
        service = _service(collection)

        items = await service.get_symbol_history("aapl", limit=10, is_premium=True)

        assert [i.date for i in items] == ["2024-01-02", "2024-01-03"]

    @pytest.mark.asyncio
    async def test_symbol_is_upper_cased(self):
        collection = _collection()
        service = _service(collection)

        await service.get_symbol_history("aapl", is_premium=True)

        assert collection.find.call_args.args[0]["symbol"] == "AAPL"


class TestGetSummary:
    @pytest.mark.asyncio
    async def test_summary_without_data_is_empty(self):
        collection = _collection()
        collection.find_one = AsyncMock(return_value=None)
        summary = await _service(collection).get_summary("us", is_premium=True)
        assert summary.date is None
        assert summary.long_count == 0

    @pytest.mark.asyncio
    async def test_summary_counts_each_side(self):
        collection = _collection([_document()])
        collection.find_one = AsyncMock(return_value=_document())
        collection.aggregate = AsyncMock(
            return_value=_AsyncCursor(
                [{"_id": "long", "count": 20}, {"_id": "short", "count": 20}]
            )
        )
        summary = await _service(collection).get_summary("us", is_premium=True)

        assert summary.date == "2024-01-02"
        assert summary.long_count == 20
        assert summary.short_count == 20
        assert summary.horizon == 10


class TestGetPerformance:
    """Forward-horizon outcome, raw and benchmark-relative."""

    @pytest.mark.asyncio
    async def test_no_resolved_signals_yields_an_empty_report(self):
        collection = _collection()
        collection.aggregate = AsyncMock(return_value=_AsyncCursor([]))
        collection.count_documents = AsyncMock(return_value=0)
        perf = await _service(collection).get_performance("us")
        assert perf.evaluated == 0
        assert perf.win_rate is None

    @pytest.mark.asyncio
    async def test_aggregates_raw_and_excess(self):
        collection = _collection()
        collection.count_documents = AsyncMock(return_value=7)
        collection.aggregate = AsyncMock(
            return_value=_AsyncCursor(
                [
                    {
                        "_id": None,
                        "n": 4,
                        "wins": 3,
                        "excess_wins": 2,
                        "avg": 0.02,
                        "avg_excess": 0.004,
                        "best": 0.1,
                        "worst": -0.05,
                        "start": "2024-01-02",
                        "end": "2024-03-02",
                        "horizon": 10,
                        "returns": [-0.05, 0.01, 0.02, 0.1],
                        "excess": [-0.01, 0.002, 0.006, 0.02],
                    }
                ]
            )
        )
        perf = await _service(collection).get_performance("us")

        assert perf.evaluated == 4
        assert perf.pending == 7
        assert perf.win_rate == pytest.approx(0.75)
        assert perf.excess_win_rate == pytest.approx(0.5)
        assert perf.median_return == pytest.approx(0.015)
        assert perf.median_excess_return == pytest.approx(0.004)
        assert perf.horizon == 10

    @pytest.mark.asyncio
    async def test_only_resolved_signals_are_aggregated(self):
        """Open signals must not be counted as zero-return outcomes."""
        collection = _collection()
        collection.count_documents = AsyncMock(return_value=0)
        collection.aggregate = AsyncMock(return_value=_AsyncCursor([]))
        await _service(collection).get_performance("us")
        match = collection.aggregate.call_args.args[0][0]["$match"]
        assert match["signal_return"] == {"$ne": None}

    @pytest.mark.asyncio
    async def test_pending_counts_the_unresolved(self):
        collection = _collection()
        collection.count_documents = AsyncMock(return_value=92)
        collection.aggregate = AsyncMock(return_value=_AsyncCursor([]))
        perf = await _service(collection).get_performance("us")
        assert perf.pending == 92
        assert collection.count_documents.call_args.args[0]["signal_return"] is None

    @pytest.mark.asyncio
    async def test_detection_is_computed_for_reversals(self):
        collection = _collection()
        # pending, then detection_scored, then detection hits
        collection.count_documents = AsyncMock(side_effect=[0, 3100, 1755])
        collection.aggregate = AsyncMock(
            return_value=_AsyncCursor(
                [
                    {
                        "_id": None, "n": 3100, "wins": 1600, "excess_wins": 1500,
                        "avg": 0.005, "avg_excess": 0.0018, "best": 0.5, "worst": -0.3,
                        "start": "2023-05-05", "end": "2026-04-10", "horizon": 10,
                        "returns": [0.005], "excess": [0.0018],
                    }
                ]
            )
        )
        perf = await _service(collection).get_performance("us", event="BUY_TURN")

        assert perf.detection_scored == 3100
        assert perf.detection_rate == pytest.approx(0.5661, abs=1e-3)
        assert perf.detection_lift and perf.detection_lift > 3.0

    @pytest.mark.asyncio
    async def test_no_detection_when_nothing_is_scored(self):
        """Trend entries are left unscored, not scored as zero."""
        collection = _collection()
        collection.count_documents = AsyncMock(side_effect=[0, 0])
        collection.aggregate = AsyncMock(
            return_value=_AsyncCursor(
                [
                    {
                        "_id": None, "n": 439, "wins": 230, "excess_wins": 222,
                        "avg": 0.0049, "avg_excess": 0.0015, "best": 0.3, "worst": -0.2,
                        "start": "2023-07-21", "end": "2026-04-08", "horizon": 10,
                        "returns": [0.0049], "excess": [0.0015],
                    }
                ]
            )
        )
        perf = await _service(collection).get_performance("us", event="TREND_BUY")

        assert perf.detection_scored == 0
        assert perf.detection_rate is None

    @pytest.mark.asyncio
    async def test_event_filter_reaches_both_queries(self):
        collection = _collection()
        collection.count_documents = AsyncMock(return_value=0)
        collection.aggregate = AsyncMock(return_value=_AsyncCursor([]))
        await _service(collection).get_performance("us", event="TREND_BUY")
        assert collection.count_documents.call_args_list[0].args[0]["event"] == "TREND_BUY"
        assert collection.aggregate.call_args.args[0][0]["$match"]["event"] == "TREND_BUY"

class TestGetLatestRun:
    @pytest.mark.asyncio
    async def test_returns_none_without_runs(self):
        collection = _collection()
        collection.find_one = AsyncMock(return_value=None)
        assert await _service(collection).get_latest_run("us") is None

    @pytest.mark.asyncio
    async def test_maps_the_run_document(self):
        collection = _collection()
        collection.find_one = AsyncMock(
            return_value={
                "market": "us",
                "date": "2024-01-02",
                "horizon": 10,
                "status": "success",
                "symbols_scored": 514,
                "published": 40,
                "counts": {"long": 20, "short": 20},
                "model": {"rows": 1_156_059, "features": 122, "unknown": 1},
                "error": None,
                "publish_error": None,
            }
        )
        run = await _service(collection).get_latest_run("us")

        assert run.status == "success"
        assert run.published == 40
        assert run.model.features == 122

    @pytest.mark.asyncio
    async def test_surfaces_a_publication_failure(self):
        """A stale board must be distinguishable from a healthy one."""
        collection = _collection()
        collection.find_one = AsyncMock(
            return_value={
                "market": "us",
                "date": "2024-01-02",
                "status": "success",
                "publish_error": "cosmos is down",
            }
        )
        run = await _service(collection).get_latest_run("us")
        assert run.publish_error == "cosmos is down"
