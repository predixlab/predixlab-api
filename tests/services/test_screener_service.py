"""Tests for read-only screener snapshot loading, filtering, and presets."""

import pandas as pd
import pytest

from app.models.market import (
    ScreenerFilters,
    ScreenerRequest,
    SortDirection,
    StockIndex,
    TrendDirection,
)
from app.services.screener_service import SCREENER_SNAPSHOT_COLUMNS, ScreenerService


def _snapshot_frame() -> pd.DataFrame:
    rows = [
        ["AAA", "2024-06-28", 100.0, 0.10, 3_000_000, 2_000_000, 1.5, 55.0, 90.0, 80.0, 70.0, 0.10, 0.20, 0.40, 0.20, -0.10, -0.02, 0.50, True, True, "uptrend", '["sp500"]'],
        ["BBB", "2024-06-28", 50.0, -0.05, 2_000_000, 3_000_000, 0.67, 30.0, 55.0, 60.0, 45.0, -0.10, -0.20, 0.10, 0.30, -0.40, -0.20, 0.10, False, True, "neutral", '["sp500","nasdaq100"]'],
        ["CCC", "2024-06-28", 200.0, 0.02, 5_000_000, 4_000_000, 1.25, 80.0, 190.0, 180.0, 170.0, 0.05, 0.30, 0.50, 0.40, -0.20, -0.01, 0.70, True, True, "strong_uptrend", '["nasdaq100"]'],
        ["DDD", "2024-06-28", 10.0, -0.10, 1_000_000, 1_500_000, 0.67, 20.0, 12.0, 15.0, 20.0, -0.20, -0.30, -0.50, 0.50, -0.70, -0.80, 0.02, False, False, "downtrend", '["sp500"]'],
    ]
    return pd.DataFrame(rows, columns=SCREENER_SNAPSHOT_COLUMNS)


@pytest.fixture
def service(tmp_path) -> ScreenerService:
    screener = ScreenerService(snapshot_root=tmp_path)
    frame = _snapshot_frame()
    screener.snapshot_path.parent.mkdir(parents=True)
    frame.to_parquet(screener.snapshot_path, engine="pyarrow", index=False)
    screener._load_snapshot()
    return screener


class TestReadOnlyLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_loads_valid_parquet_without_writing(self, tmp_path):
        service = ScreenerService(snapshot_root=tmp_path)
        service.snapshot_path.parent.mkdir(parents=True)
        _snapshot_frame().to_parquet(service.snapshot_path, engine="pyarrow", index=False)
        original = service.snapshot_path.read_bytes()

        await service.initialize()

        assert len(service.build_metrics()) == 4
        assert service.snapshot_path.read_bytes() == original

    @pytest.mark.asyncio
    async def test_initialize_missing_snapshot_keeps_empty_table(self, tmp_path, caplog):
        service = ScreenerService(snapshot_root=tmp_path)

        await service.initialize()

        assert service.build_metrics().empty
        assert not service.snapshot_path.exists()
        assert "unavailable" in caplog.text

    @pytest.mark.asyncio
    async def test_initialize_invalid_snapshot_keeps_empty_table_without_writing(
        self, tmp_path, caplog
    ):
        service = ScreenerService(snapshot_root=tmp_path)
        service.snapshot_path.parent.mkdir(parents=True)
        service.snapshot_path.write_text("not parquet", encoding="utf-8")
        original = service.snapshot_path.read_bytes()

        await service.initialize()

        assert service.build_metrics().empty
        assert service.snapshot_path.read_bytes() == original
        assert "Could not load" in caplog.text

    @pytest.mark.asyncio
    async def test_initialize_rejects_snapshot_missing_schema_columns(self, tmp_path):
        service = ScreenerService(snapshot_root=tmp_path)
        service.snapshot_path.parent.mkdir(parents=True)
        pd.DataFrame({"symbol": ["AAA"]}).to_parquet(
            service.snapshot_path, engine="pyarrow", index=False
        )

        await service.initialize()

        assert service.build_metrics().empty

    @pytest.mark.asyncio
    async def test_queries_load_snapshot_published_after_startup(self, tmp_path):
        service = ScreenerService(snapshot_root=tmp_path)
        await service.initialize()
        service.snapshot_path.parent.mkdir(parents=True)
        _snapshot_frame().to_parquet(
            service.snapshot_path,
            engine="pyarrow",
            index=False,
        )

        assert len(service.build_metrics()) == 4

    @pytest.mark.asyncio
    async def test_invalid_replacement_keeps_last_valid_table(self, tmp_path):
        service = ScreenerService(snapshot_root=tmp_path)
        service.snapshot_path.parent.mkdir(parents=True)
        _snapshot_frame().to_parquet(
            service.snapshot_path,
            engine="pyarrow",
            index=False,
        )
        await service.initialize()
        service.snapshot_path.write_text("not parquet", encoding="utf-8")

        assert len(service.build_metrics()) == 4


class TestScreening:
    @pytest.mark.asyncio
    async def test_empty_filters_return_entire_loaded_snapshot(self, service):
        result = await service.screen(ScreenerRequest())

        assert result.total_count == 4
        assert result.universe_size == 4

    @pytest.mark.asyncio
    async def test_price_rsi_and_boolean_filters(self, service):
        result = await service.screen(
            ScreenerRequest(
                filters=ScreenerFilters(
                    min_price=75,
                    min_rsi=50,
                    above_sma200=True,
                    golden_cross=True,
                )
            )
        )

        assert {match.symbol for match in result.matches} == {"AAA", "CCC"}

    @pytest.mark.asyncio
    async def test_range_volume_risk_and_distance_filters(self, service):
        result = await service.screen(
            ScreenerRequest(
                filters=ScreenerFilters(
                    min_avg_volume=2_000_000,
                    max_volume_ratio=1.3,
                    max_drawdown_floor=-0.5,
                    min_distance_to_52w_high=-0.25,
                    max_distance_to_52w_high=-0.01,
                    max_rsi=50,
                )
            )
        )

        assert {match.symbol for match in result.matches} == {"BBB"}

    @pytest.mark.asyncio
    async def test_trend_and_near_range_filters(self, service):
        result = await service.screen(
            ScreenerRequest(
                filters=ScreenerFilters(
                    trends=[TrendDirection.UPTREND, TrendDirection.STRONG_UPTREND],
                    near_52w_high_percent=0.02,
                )
            )
        )

        assert {match.symbol for match in result.matches} == {"AAA", "CCC"}

    @pytest.mark.asyncio
    async def test_index_and_symbol_filters_intersect(self, service):
        result = await service.screen(
            ScreenerRequest(
                filters=ScreenerFilters(
                    index=StockIndex.NASDAQ100,
                    symbols=["AAA", "BBB"],
                )
            )
        )

        assert result.universe_size == 1
        assert [match.symbol for match in result.matches] == ["BBB"]
        assert result.matches[0].indexes == ["sp500", "nasdaq100"]

    @pytest.mark.asyncio
    async def test_impossible_filter_returns_empty_result(self, service):
        result = await service.screen(
            ScreenerRequest(filters=ScreenerFilters(min_price=10_000_000))
        )

        assert result.total_count == 0
        assert result.matches == []


class TestSortingPagingAndPresets:
    @pytest.mark.asyncio
    async def test_sorting_paging_and_unknown_sort_fallback(self, service):
        ascending = await service.screen(
            ScreenerRequest(sort_by="close", sort_direction=SortDirection.ASC, limit=2)
        )
        fallback = await service.screen(ScreenerRequest(sort_by="not_a_field"))

        assert [match.symbol for match in ascending.matches] == ["DDD", "BBB"]
        assert fallback.matches[0].symbol == "AAA"
        assert fallback.total_count == 4

    def test_symbol_subset_uses_loaded_table_case_insensitively(self, service):
        full = service.build_metrics()
        subset = service.build_metrics(["aaa"])

        assert list(subset["symbol"]) == ["AAA"]
        assert service.build_metrics() is full

    @pytest.mark.asyncio
    async def test_presets_remain_runnable_and_sortable(self, service):
        presets = service.get_presets()

        assert len({preset.id for preset in presets}) == len(presets)
        assert all(preset.filters.index == StockIndex.ALL for preset in presets)
        for preset in presets:
            result = await service.screen(
                ScreenerRequest(
                    filters=preset.filters,
                    sort_by=preset.sort_by,
                    sort_direction=preset.sort_direction,
                )
            )
            assert result.universe_size >= result.total_count
