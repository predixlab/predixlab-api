"""Unit tests for the AlphaRank async Mongo service."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.models.alpharank import AlphaRankSignal, alpha_rank_from_document
from app.services.alpharank_service import AlphaRankService
from app.services.path_metrics_service import PathOutcome


def _document(
    symbol: str = "AAPL",
    rank: int = 1,
    date: str = "2024-01-02",
    generation_id: str = "committed-v1",
):
    return {
        "_id": "mongo-id",
        "market": "us",
        "date": date,
        "symbol": symbol,
        "rank": rank,
        "horizon": 10,
        "tag": "daily",
        "generated_at": datetime(2024, 1, 3, 2, 0),
        "signal_model": "alpha_rank",
        "generation_id": generation_id,
        "score": 0.95,
        "consensus": 0.81,
        "predicted_excess_return": 0.034,
        "predicted_rank": 0.873,
        "top20_probability": 0.93,
        "close": 189.5,
        "pipeline_only": "ignored",
    }


def _manifest(
    date: str = "2024-01-02",
    active_generation_id: str = "committed-v1",
    row_count: int = 1,
):
    return {
        "market": "us",
        "date": date,
        "signal_model": "alpha_rank",
        "active_generation_id": active_generation_id,
        "row_count": row_count,
    }


class _AsyncCursor:
    def __init__(self, documents):
        self.documents = documents

    def __aiter__(self):
        async def generator():
            for document in self.documents:
                yield document

        return generator()

    def sort(self, key, direction=1):
        self.documents.sort(
            key=lambda document: document.get(key, ""),
            reverse=direction < 0,
        )
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self


def _service(
    documents=None, manifests=None
) -> tuple[AlphaRankService, MagicMock, MagicMock]:
    documents = list(documents or [])
    manifests = list(
        manifests
        if manifests is not None
        else (
            [
                _manifest(
                    documents[0]["date"],
                    documents[0]["generation_id"],
                    len(documents),
                )
            ]
            if documents
            else []
        )
    )
    signals = MagicMock()
    signals.find = MagicMock(return_value=_AsyncCursor(documents))
    generations = MagicMock()
    generations.find = MagicMock(return_value=_AsyncCursor(manifests))
    generations.find_one = AsyncMock(return_value=manifests[0] if manifests else None)
    generations.aggregate = AsyncMock(return_value=_AsyncCursor(manifests))
    service = AlphaRankService()
    service.db = MagicMock()
    service.db.__getitem__.side_effect = {
        settings.alpharank_collection: signals,
        settings.alpharank_generations_collection: generations,
    }.__getitem__
    return service, signals, generations


def test_document_conversion_drops_mongo_fields_and_preserves_contract():
    signal = alpha_rank_from_document(_document())

    assert isinstance(signal, AlphaRankSignal)
    assert signal.symbol == "AAPL"
    assert signal.signal_model == "alpha_rank"
    assert signal.close == pytest.approx(189.5)
    assert signal.predicted_rank == pytest.approx(0.873)


def test_document_conversion_accepts_a_null_close():
    document = _document()
    document["close"] = None

    assert alpha_rank_from_document(document).close is None


def test_document_conversion_rejects_unclipped_predicted_rank():
    document = _document()
    document["predicted_rank"] = 1.001

    with pytest.raises(ValueError):
        alpha_rank_from_document(document)


class TestAlphaRankFilters:
    def test_free_user_gets_the_pivot_equivalent_date_ceiling(self):
        service, _, _ = _service()

        assert service._base_filter("us", False)["date"]["$lte"] == (
            datetime.now() - timedelta(days=settings.free_user_days_limit)
        ).strftime("%Y-%m-%d")

    def test_premium_user_has_no_date_ceiling(self):
        service, _, _ = _service()

        assert service._base_filter("us", True) == {
            "market": "us",
            "signal_model": "alpha_rank",
        }


class TestAlphaRankQueries:
    @pytest.mark.asyncio
    async def test_latest_date_uses_the_visible_manifest_filter(self):
        service, _, generations = _service()
        generations.find_one = AsyncMock(return_value=_manifest(date="2024-01-03"))

        assert await service.get_latest_date(is_premium=True) == "2024-01-03"
        assert generations.find_one.call_args.args[0] == {
            "market": "us",
            "signal_model": "alpha_rank",
            "active_generation_id": {"$exists": True, "$ne": None},
            "row_count": {"$gte": 0},
        }

    @pytest.mark.asyncio
    async def test_dates_are_aggregated_from_manifests_newest_first(self):
        service, _, generations = _service(
            manifests=[{"_id": "2024-01-03"}, {"_id": "2024-01-02"}]
        )

        assert await service.get_available_dates(is_premium=True) == [
            "2024-01-03",
            "2024-01-02",
        ]
        generations.aggregate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_latest_and_dates_ignore_uncommitted_raw_signal_dates(self):
        service, _, generations = _service(
            documents=[_document(date="2024-01-04", generation_id="staged-v2")],
            manifests=[_manifest(date="2024-01-03")],
        )
        generations.aggregate = AsyncMock(return_value=_AsyncCursor([{"_id": "2024-01-03"}]))

        assert await service.get_latest_date(is_premium=True) == "2024-01-03"
        assert await service.get_available_dates(is_premium=True) == ["2024-01-03"]

    @pytest.mark.asyncio
    async def test_signals_have_deterministic_rank_then_symbol_order(self):
        service, _, _ = _service(
            [
                _document("MSFT", 2),
                _document("ZETA", 1),
                _document("AAPL", 1),
            ]
        )

        signals = await service.get_signals_by_date("2024-01-02", is_premium=True)

        assert [signal.symbol for signal in signals] == ["AAPL", "ZETA", "MSFT"]

    @pytest.mark.asyncio
    async def test_only_the_committed_generation_is_returned(self):
        committed_first = _document("MSFT", 2, generation_id="committed-v1")
        committed_second = _document("AAPL", 1, generation_id="committed-v1")
        staged_partial = _document("STAGED", 1, generation_id="staged-v2")
        service, signal_collection, _ = _service(
            [committed_first, committed_second, staged_partial],
            manifests=[_manifest(active_generation_id="committed-v1", row_count=2)],
        )

        def documents_for_query(query):
            assert query["generation_id"] == "committed-v1"
            return _AsyncCursor([committed_first, committed_second])

        signal_collection.find = MagicMock(side_effect=documents_for_query)

        signals = await service.get_signals_by_date("2024-01-02", is_premium=True)

        assert [signal.symbol for signal in signals] == ["AAPL", "MSFT"]

    @pytest.mark.asyncio
    async def test_promotion_race_retries_and_returns_the_new_committed_generation(self):
        old = _document("OLD", generation_id="committed-v1")
        new = _document("NEW", generation_id="committed-v2")
        old_manifest = _manifest(active_generation_id="committed-v1")
        new_manifest = _manifest(active_generation_id="committed-v2")
        service, signal_collection, generations = _service([old, new])
        generations.find_one = AsyncMock(
            side_effect=[old_manifest, new_manifest, new_manifest, new_manifest]
        )

        def documents_for_generation(query):
            return _AsyncCursor([old] if query["generation_id"] == "committed-v1" else [new])

        signal_collection.find = MagicMock(side_effect=documents_for_generation)

        signals = await service.get_signals_by_date("2024-01-02", is_premium=True)

        assert [signal.symbol for signal in signals] == ["NEW"]
        assert signal_collection.find.call_count == 2

    @pytest.mark.asyncio
    async def test_persistent_manifest_row_count_mismatch_raises(self):
        manifest = _manifest(row_count=2)
        signal = _document()
        service, signal_collection, generations = _service([signal])
        generations.find_one = AsyncMock(return_value=manifest)
        signal_collection.find = MagicMock(return_value=_AsyncCursor([signal]))

        with pytest.raises(RuntimeError, match="complete stable AlphaRank generation"):
            await service.get_signals_by_date("2024-01-02", is_premium=True)

        assert signal_collection.find.call_count == 3

    @pytest.mark.asyncio
    async def test_configured_collection_names_are_used(self):
        signal = _document()
        manifest = _manifest()
        signals = MagicMock(find=MagicMock(return_value=_AsyncCursor([signal])))
        generations = MagicMock(find_one=AsyncMock(return_value=manifest))
        service = AlphaRankService()
        service.db = MagicMock()
        service.db.__getitem__.side_effect = {
            "publisher_signals": signals,
            "publisher_manifests": generations,
        }.__getitem__

        with (
            patch.object(settings, "alpharank_collection", "publisher_signals"),
            patch.object(
                settings,
                "alpharank_generations_collection",
                "publisher_manifests",
            ),
        ):
            result = await service.get_signals_by_date("2024-01-02", is_premium=True)

        assert [signal.symbol for signal in result] == ["AAPL"]
        assert service.db.__getitem__.call_args_list[0].args == ("publisher_manifests",)
        assert service.db.__getitem__.call_args_list[1].args == ("publisher_signals",)

    @pytest.mark.asyncio
    async def test_malformed_documents_are_skipped_without_losing_valid_signals(self):
        invalid = _document("BAD", 2)
        invalid.pop("score")
        service, _, _ = _service([invalid, _document("AAPL", 1)])

        signals = await service.get_signals_by_date("2024-01-02", is_premium=True)

        assert [signal.symbol for signal in signals] == ["AAPL"]

    @pytest.mark.asyncio
    async def test_date_query_applies_visibility_filter(self):
        service, signal_collection, _ = _service([_document()])

        await service.get_signals_by_date("2024-01-02", is_premium=True)

        query = signal_collection.find.call_args.args[0]
        assert query["date"] == "2024-01-02"
        assert query["signal_model"] == "alpha_rank"
        assert query["generation_id"] == "committed-v1"

    @pytest.mark.asyncio
    async def test_free_date_query_checks_manifest_visibility(self):
        service, _, generations = _service([_document()])

        await service.get_signals_by_date("2024-01-02", is_premium=False)

        assert generations.find_one.call_args.args[0]["date"] == {
            "$eq": "2024-01-02",
            "$lte": service._cutoff(),
        }

    @pytest.mark.asyncio
    async def test_empty_date_has_no_committed_generation_and_returns_an_empty_list(self):
        service, signal_collection, _ = _service()

        assert await service.get_signals_by_date("2024-01-02", is_premium=True) == []
        signal_collection.find.assert_not_called()

    @pytest.mark.asyncio
    async def test_manifest_without_an_active_generation_is_not_read(self):
        service, signal_collection, generations = _service(
            manifests=[
                {
                    "market": "us",
                    "date": "2024-01-02",
                    "signal_model": "alpha_rank",
                }
            ]
        )
        generations.find_one = AsyncMock(return_value=None)

        assert await service.get_signals_by_date("2024-01-02", is_premium=True) == []
        signal_collection.find.assert_not_called()
        assert generations.find_one.call_args.args[0]["active_generation_id"] == {
            "$exists": True,
            "$ne": None,
        }

    @pytest.mark.asyncio
    async def test_collection_failures_are_reraised(self):
        service, signal_collection, generations = _service()
        generations.aggregate = AsyncMock(side_effect=RuntimeError("aggregate unavailable"))
        with pytest.raises(RuntimeError, match="aggregate unavailable"):
            await service.get_available_dates()

        generations.find_one = AsyncMock(side_effect=RuntimeError("latest unavailable"))
        with pytest.raises(RuntimeError, match="latest unavailable"):
            await service.get_latest_date()

        generations.find_one = AsyncMock(
            return_value=_manifest()
        )
        signal_collection.find = MagicMock(side_effect=RuntimeError("find unavailable"))
        with pytest.raises(RuntimeError, match="find unavailable"):
            await service.get_signals_by_date("2024-01-02")


class TestAlphaRankTickerHistory:
    def _history_service(self):
        old_aapl = _document(
            "AAPL", date="2024-01-02", generation_id="committed-old"
        )
        new_aapl = _document(
            "AAPL", date="2024-01-03", generation_id="committed-new"
        )
        staged_aapl = _document(
            "AAPL", date="2024-01-03", generation_id="staged-new"
        )
        manifests = [
            _manifest(
                date="2024-01-02",
                active_generation_id="committed-old",
                row_count=2,
            ),
            _manifest(
                date="2024-01-03",
                active_generation_id="committed-new",
                row_count=2,
            ),
        ]
        service, signal_collection, generations = _service(
            [old_aapl, new_aapl, staged_aapl], manifests=manifests
        )
        signal_collection.find = MagicMock(
            return_value=_AsyncCursor([old_aapl, new_aapl, staged_aapl])
        )
        generations.find = MagicMock(return_value=_AsyncCursor(manifests))
        return service, signal_collection, generations

    @pytest.mark.asyncio
    async def test_returns_only_committed_generations_oldest_first(self):
        service, signal_collection, _ = self._history_service()

        items = await service.get_ticker_history("aapl", is_premium=True)

        assert [item.date for item in items] == ["2024-01-02", "2024-01-03"]
        assert [item.symbol for item in items] == ["AAPL", "AAPL"]
        assert signal_collection.find.call_count == 1

    @pytest.mark.asyncio
    async def test_applies_limit_after_matching_the_ticker(self):
        service, _, _ = self._history_service()

        items = await service.get_ticker_history("AAPL", limit=1, is_premium=True)

        assert [item.date for item in items] == ["2024-01-03"]

    @pytest.mark.asyncio
    async def test_history_checks_free_visibility_on_documents_and_manifests(self):
        service, _, generations = self._history_service()

        await service.get_ticker_history("AAPL", is_premium=False)

        assert generations.find.call_args.args[0]["date"]["$lte"] == service._cutoff()

    @pytest.mark.asyncio
    async def test_infrequent_ticker_older_than_750_sessions_is_not_lost(self):
        old = _document("RARE", date="2021-01-04", generation_id="committed-old")
        old_manifest = _manifest(
            date="2021-01-04",
            active_generation_id="committed-old",
            row_count=1,
        )
        service, signal_collection, generations = _service([old], manifests=[old_manifest])
        newer_manifests = [
            _manifest(
                date=(datetime(2021, 1, 5) + timedelta(days=day)).strftime("%Y-%m-%d"),
                active_generation_id=f"new-{day}",
                row_count=1,
            )
            for day in range(751)
        ]
        all_manifests = [old_manifest, *newer_manifests]
        signal_collection.find = MagicMock(return_value=_AsyncCursor([old]))

        def all_visible_manifests(query):
            assert "date" not in query
            return _AsyncCursor(all_manifests)

        generations.find = MagicMock(side_effect=all_visible_manifests)

        items = await service.get_ticker_history("RARE", is_premium=True)

        assert [item.date for item in items] == ["2021-01-04"]
        assert len(newer_manifests) > 750
        assert signal_collection.find.call_count == 1
        assert generations.find.call_count == 2

    @pytest.mark.asyncio
    async def test_history_uses_a_bounded_three_query_snapshot(self):
        service, signal_collection, generations = self._history_service()

        await service.get_ticker_history("AAPL", is_premium=True)

        assert signal_collection.find.call_count == 1
        assert generations.find.call_count == 2

    @pytest.mark.asyncio
    async def test_promotion_between_manifest_and_row_reads_retries(self):
        old = _document("AAPL", date="2024-01-02", generation_id="committed-old")
        new = _document(
            "AAPL", rank=2, date="2024-01-02", generation_id="committed-new"
        )
        old_manifest = _manifest(
            active_generation_id="committed-old", row_count=1
        )
        new_manifest = _manifest(
            active_generation_id="committed-new", row_count=1
        )
        service, signal_collection, generations = _service([old, new])
        generations.find = MagicMock(
            side_effect=[
                _AsyncCursor([old_manifest]),
                _AsyncCursor([new_manifest]),
                _AsyncCursor([new_manifest]),
                _AsyncCursor([new_manifest]),
            ]
        )

        def rows_for_generation(query):
            generation = query["$or"][0]["generation_id"]
            return _AsyncCursor([old] if generation == "committed-old" else [new])

        signal_collection.find = MagicMock(side_effect=rows_for_generation)

        items = await service.get_ticker_history("AAPL", is_premium=True)

        assert [item.symbol for item in items] == ["AAPL"]
        assert items[0].rank == 2
        assert signal_collection.find.call_count == 2
        assert generations.find.call_count == 4

    @pytest.mark.asyncio
    async def test_attaches_long_path_metrics_and_preserves_incomplete_state(self):
        service, _, _ = self._history_service()
        outcome = PathOutcome(0.12, -0.04, 3, 1, False)
        with patch(
            "app.services.alpharank_service.path_metrics_service.outcome_for",
            return_value=outcome,
        ) as outcome_for:
            items = await service.get_ticker_history("AAPL", limit=1, is_premium=True)

        item = items[0]
        assert item.max_gain == pytest.approx(0.12)
        assert item.max_loss == pytest.approx(-0.04)
        assert item.days_to_max_gain == 3
        assert item.days_to_max_loss == 1
        assert item.path_complete is False
        assert outcome_for.call_args.args[-1] == "long"

    @pytest.mark.asyncio
    async def test_missing_path_data_remains_null(self):
        service, _, _ = self._history_service()
        with patch(
            "app.services.alpharank_service.path_metrics_service.outcome_for",
            return_value=None,
        ):
            items = await service.get_ticker_history("AAPL", limit=1, is_premium=True)

        assert items[0].max_gain is None
        assert items[0].max_loss is None
        assert items[0].path_complete is None

    @pytest.mark.asyncio
    async def test_history_database_errors_are_reraised(self):
        service, _, generations = self._history_service()
        generations.find = MagicMock(side_effect=RuntimeError("manifest outage"))

        with pytest.raises(RuntimeError, match="manifest outage"):
            await service.get_ticker_history("AAPL", is_premium=True)
