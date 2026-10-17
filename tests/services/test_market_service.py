"""Tests for read-only market snapshot loading and queries."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.market import MoverCategory
from app.services.market_service import (
    MARKET_RESULT_LIMIT,
    MARKET_SNAPSHOT_VERSION,
    MarketService,
)


def _entry(symbol: str, metric: float) -> dict:
    return {
        "symbol": symbol,
        "date": "2024-06-28",
        "close": 100.0,
        "volume": 1_000_000,
        "change_percent": metric,
        "metric": metric,
        "metric_name": "change_percent",
    }


def _snapshot_document() -> dict:
    movers = {
        category.value: {
            "category": category.value,
            "date": "2024-06-28",
            "entries": [_entry("AAA", 0.10), _entry("BBB", 0.05)],
            "total_count": 2,
        }
        for category in MoverCategory
    }
    movers[MoverCategory.LOSERS.value]["entries"] = [
        _entry("BBB", -0.10),
        _entry("AAA", -0.05),
    ]
    movers[MoverCategory.MOST_ACTIVE.value]["entries"][0]["metric_name"] = "turnover"
    buckets = [
        {
            "label": f"bucket-{number}",
            "count": 2 if number == 0 else 0,
            "percent": 1.0 if number == 0 else 0.0,
        }
        for number in range(8)
    ]
    heatmap_entries = [
        {"symbol": "AAA", "change_percent": 0.10, "volume": 1_000_000, "close": 100.0},
        {"symbol": "BBB", "change_percent": 0.05, "volume": 900_000, "close": 90.0},
    ]
    return {
        "version": MARKET_SNAPSHOT_VERSION,
        "date": "2024-06-28",
        "generated_at": "2024-06-28T12:00:00Z",
        "breadth": {
            "date": "2024-06-28",
            "total_symbols": 2,
            "advancers": 2,
            "decliners": 0,
            "unchanged": 0,
            "percent_advancing": 1.0,
        },
        "sentiment": {
            "score": 62.0,
            "label": "greed",
            "components": {
                "zweig_breadth": {
                    "raw_value": 0.55,
                    "score": 62.0,
                    "weight": 0.15,
                }
            },
            "summary": "Participation is healthy.",
        },
        "movers": movers,
        "distributions": {
            str(days): {
                "date": "2024-06-28",
                "window_days": days,
                "buckets": buckets,
                "total_symbols": 2,
            }
            for days in (1, 7, 30)
        },
        "heatmaps": {
            str(days): {
                "date": "2024-06-28",
                "window_days": days,
                "entries": heatmap_entries,
            }
            for days in (1, 5, 20)
        },
    }


@pytest.fixture
def service(tmp_path) -> MarketService:
    service = MarketService(snapshot_root=tmp_path)
    service._load_snapshot(_snapshot_document())
    return service


class TestReadOnlyLifecycle:
    @pytest.mark.asyncio
    async def test_default_service_loads_latest_database_document(self):
        database = MagicMock()
        collection = database.__getitem__.return_value
        document = _snapshot_document()
        document["market"] = "us"
        document.pop("heatmaps")
        collection.find_one = AsyncMock(return_value=document)
        service = MarketService(database=database)

        await service.initialize()

        breadth = await service.get_breadth()
        assert breadth.total_symbols == 2
        assert breadth.date == "2024-06-28"
        collection.find_one.assert_awaited_with(
            {"market": "us"},
            sort=[("date", -1)],
        )

    @pytest.mark.asyncio
    async def test_initialize_loads_valid_version_three_snapshot(self, tmp_path):
        service = MarketService(snapshot_root=tmp_path)
        service.snapshot_path.parent.mkdir(parents=True)
        service.snapshot_path.write_text(json.dumps(_snapshot_document()), encoding="utf-8")
        original = service.snapshot_path.read_bytes()

        await service.initialize()

        assert (await service.get_breadth()).total_symbols == 2
        assert service.snapshot_path.read_bytes() == original

    @pytest.mark.asyncio
    async def test_initialize_keeps_version_two_score_during_rollout(self, tmp_path):
        document = _snapshot_document()
        document["version"] = 2
        document["sentiment"]["components"] = {"percent_advancing": 1.0}
        service = MarketService(snapshot_root=tmp_path)
        service.snapshot_path.parent.mkdir(parents=True)
        service.snapshot_path.write_text(json.dumps(document), encoding="utf-8")

        await service.initialize()

        sentiment = await service.get_sentiment()
        assert sentiment.score == 62.0
        assert sentiment.components == {}

    @pytest.mark.asyncio
    async def test_initialize_missing_snapshot_keeps_empty_state(self, tmp_path, caplog):
        service = MarketService(snapshot_root=tmp_path)

        await service.initialize()

        assert (await service.get_breadth()).total_symbols == 0
        assert not service.snapshot_path.exists()
        assert "unavailable" in caplog.text

    @pytest.mark.asyncio
    async def test_initialize_invalid_snapshot_keeps_empty_state_without_writing(
        self, tmp_path, caplog
    ):
        service = MarketService(snapshot_root=tmp_path)
        service.snapshot_path.parent.mkdir(parents=True)
        service.snapshot_path.write_text("not json", encoding="utf-8")
        original = service.snapshot_path.read_bytes()

        await service.initialize()

        assert (await service.get_breadth()).total_symbols == 0
        assert service.snapshot_path.read_bytes() == original
        assert "Could not load" in caplog.text

    @pytest.mark.asyncio
    async def test_queries_load_snapshot_published_after_startup(self, tmp_path):
        service = MarketService(snapshot_root=tmp_path)
        await service.initialize()
        service.snapshot_path.parent.mkdir(parents=True)
        service.snapshot_path.write_text(
            json.dumps(_snapshot_document()),
            encoding="utf-8",
        )

        assert (await service.get_breadth()).total_symbols == 2

    @pytest.mark.asyncio
    async def test_invalid_replacement_keeps_last_valid_snapshot(self, tmp_path):
        service = MarketService(snapshot_root=tmp_path)
        service.snapshot_path.parent.mkdir(parents=True)
        service.snapshot_path.write_text(
            json.dumps(_snapshot_document()),
            encoding="utf-8",
        )
        await service.initialize()
        service.snapshot_path.write_text("not json", encoding="utf-8")

        assert (await service.get_breadth()).total_symbols == 2


class TestSnapshotQueries:
    @pytest.mark.asyncio
    async def test_breadth_sentiment_and_overview_are_cached(self, service):
        breadth = await service.get_breadth()
        sentiment = await service.get_sentiment()
        overview = await service.get_overview(movers_limit=1)

        assert breadth.total_symbols == 2
        assert sentiment.label == "greed"
        assert overview.date == breadth.date
        assert len(overview.top_gainers) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("category", list(MoverCategory))
    async def test_every_mover_category_loads_and_respects_limit(self, service, category):
        result = await service.get_movers(category, limit=1)

        assert result.category == category
        assert len(result.entries) == 1
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_mover_results_are_capped_and_sorted_as_stored(self, service):
        gainers = await service.get_movers(MoverCategory.GAINERS, limit=500)
        losers = await service.get_movers(MoverCategory.LOSERS, limit=10)

        assert len(gainers.entries) <= MARKET_RESULT_LIMIT
        assert [entry.metric for entry in gainers.entries] == [0.10, 0.05]
        assert [entry.metric for entry in losers.entries] == [-0.10, -0.05]

    @pytest.mark.asyncio
    async def test_distribution_and_heatmap_preserve_window_queries(self, service):
        distribution = await service.get_performance_distribution(window_days=7)
        heatmap = await service.get_heatmap(window_days=20, limit=1)

        assert len(distribution.buckets) == 8
        assert sum(bucket.count for bucket in distribution.buckets) == 2
        assert heatmap.window_days == 20
        assert len(heatmap.entries) == 1

    @pytest.mark.asyncio
    async def test_unknown_windows_and_categories_return_empty_responses(self, service):
        distribution = await service.get_performance_distribution(window_days=5)
        heatmap = await service.get_heatmap(window_days=7)

        assert distribution.total_symbols == 0
        assert heatmap.entries == []

    def test_snapshot_returns_a_copy(self, service):
        snapshot = service.get_snapshot()
        snapshot["breadth"]["total_symbols"] = 99

        assert service.get_snapshot()["breadth"]["total_symbols"] == 2
