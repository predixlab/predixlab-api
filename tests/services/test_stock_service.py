"""Unit tests for the stock service."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
from pathlib import Path

from app.config import Settings, settings
from app.services.stock_service import StockService
from app.models.stock import (
    Direction, DailyPick, Prediction, StockDataResponse, StockData,
    DailyPicksResponse, AvailableDatesResponse
)
from app.models.user import UserRole


@pytest.fixture
def stock_data_service(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "us_stock_data_dir", str(tmp_path / "stock_data" / "us_data"))
    monkeypatch.setattr(settings, "predixlab_data_dir", None)
    monkeypatch.setattr(settings, "us_reference_data_dir", None)
    monkeypatch.setattr(
        settings, "us_reference_symbols", Settings.model_fields["us_reference_symbols"].default
    )
    service = StockService()
    service.stock_data_dir.mkdir(parents=True)
    service.reference_data_dir.mkdir(parents=True)
    return service


class TestReferenceSymbolSettings:
    def test_default_reference_symbols(self, monkeypatch):
        monkeypatch.delenv("US_REFERENCE_SYMBOLS", raising=False)
        configured = Settings(_env_file=None)

        assert configured.us_reference_symbols == "SPY,QQQ,IWM,DIA"
        for symbol in ("SPY", "QQQ", "IWM", "DIA"):
            assert configured.is_index_symbol(symbol)
            assert configured.is_index_symbol(symbol.lower())
        assert not configured.is_index_symbol("^GSPC")
        assert not configured.is_index_symbol("AAPL")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("VTI,XLK", {"VTI", "XLK"}),
            ("  vTi , xLk  ", {"VTI", "XLK"}),
            (", vti, , XLK,,vTi, ", {"VTI", "XLK"}),
            ("", set()),
            (" , ,  ", set()),
        ],
    )
    def test_custom_reference_symbols(self, monkeypatch, value, expected):
        monkeypatch.setenv("US_REFERENCE_SYMBOLS", value)
        configured = Settings(_env_file=None)

        for symbol in ("VTI", "XLK", "SPY", "^GSPC", ""):
            assert configured.is_index_symbol(symbol) == (symbol in expected)
            assert configured.is_index_symbol(symbol.lower()) == (symbol in expected)


class TestStockServiceReferenceData:
    @pytest.mark.parametrize(
        ("reference_dir", "data_root", "stock_dir", "expected"),
        [
            ("/custom/indices", "/shared", "/data/stock_data/us_data", "/custom/indices"),
            (None, "/shared", "/data/stock_data/us_data", "/shared/reference_data/us_indices"),
            (None, None, "/data/stock_data/us_data", "/data/reference_data/us_indices"),
            (None, None, "data/stock_data/us_data", "data/reference_data/us_indices"),
        ],
    )
    def test_reference_data_dir_resolution(
        self, monkeypatch, reference_dir, data_root, stock_dir, expected
    ):
        for name, value in (
            ("US_REFERENCE_DATA_DIR", reference_dir),
            ("PREDIXLAB_DATA_DIR", data_root),
            ("US_STOCK_DATA_DIR", stock_dir),
        ):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)

        with patch("app.services.stock_service.settings", Settings(_env_file=None)):
            service = StockService()

        assert service.reference_data_dir == Path(expected)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stock_file_exists", [False, True])
    @pytest.mark.parametrize("symbol", ["SPY", "QQQ", "IWM", "DIA"])
    async def test_reference_history_with_period_and_cache(
        self, stock_data_service, symbol, stock_file_exists
    ):
        service = stock_data_service
        reference_path = service.reference_data_dir / f"{symbol}.csv"
        frame = pd.DataFrame(
            {
                "symbol": [symbol] * 3,
                "date": ["2024-03-30", "2024-04-30", "2024-03-31"],
                "open": [100.0] * 3,
                "high": [105.0] * 3,
                "low": [99.0] * 3,
                "close": [104.0] * 3,
                "volume": [1000] * 3,
            }
        )
        frame.to_csv(reference_path, index=False)
        if stock_file_exists:
            frame["close"] = 999.0
            frame.to_csv(service.stock_data_dir / f"{symbol}.csv", index=False)

        with patch.object(service, "_load_data_file", wraps=service._load_data_file) as load:
            result = await service.get_stock_data(symbol.lower(), period="1m")
            full_result = await service.get_stock_data(symbol)
            assert await service.get_stock_data(symbol.lower()) is full_result
            load.assert_called_once_with(reference_path)

        assert result is not None
        assert result.model_dump(mode="json") == {
            "symbol": symbol,
            "is_index": True,
            "total_records": 2,
            "data": [
                {
                    "symbol": symbol,
                    "date": date,
                    "open": 100.0,
                    "high": 105.0,
                    "low": 99.0,
                    "close": 104.0,
                    "volume": 1000,
                }
                for date in ("2024-04-30T00:00:00.000000Z", "2024-03-31T00:00:00.000000Z")
            ],
        }
        assert full_result is not None
        assert full_result.is_index is True
        assert full_result.total_records == 3
        assert [bar.date for bar in full_result.data] == [
            datetime(2024, 4, 30), datetime(2024, 3, 31), datetime(2024, 3, 30)
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("symbol", ["AAPL", "^GSPC"])
    async def test_regular_ticker_reads_only_stock_data_and_is_cached(
        self, stock_data_service, symbol
    ):
        service = stock_data_service
        stock_path = service.stock_data_dir / f"{symbol}.csv"
        frame = pd.DataFrame(
            [{
                "symbol": symbol,
                "date": "2024-04-30",
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": 104.0,
                "volume": 1000,
                "adjclose": 103.0,
                "dividends": 0.0,
                "splits": 0.0,
            }]
        )
        frame.to_csv(stock_path, index=False)
        frame["close"] = 999.0
        frame.to_csv(service.reference_data_dir / f"{symbol}.csv", index=False)

        with patch.object(service, "_load_data_file", wraps=service._load_data_file) as load:
            result = await service.get_stock_data(symbol.lower())
            cached_result = await service.get_stock_data(symbol)
            load.assert_called_once_with(stock_path)

        assert result is not None
        assert result.symbol == symbol
        assert result.is_index is False
        assert result.data[0].symbol == symbol
        assert result.total_records == 1
        assert result.data[0].close == 104.0
        assert cached_result is result

    @pytest.mark.asyncio
    async def test_custom_reference_symbols_control_routing(self, stock_data_service, monkeypatch):
        monkeypatch.setattr(settings, "us_reference_symbols", " , vTi, , ")
        service = stock_data_service
        for symbol in ("VTI", "SPY"):
            for directory, close in (
                (service.stock_data_dir, 101),
                (service.reference_data_dir, 104),
            ):
                (directory / f"{symbol}.csv").write_text(
                    "symbol,date,open,high,low,close,volume\n"
                    f"{symbol},2024-04-30,100,105,99,{close},1000\n"
                )

        reference_result = await service.get_stock_data("vTi")
        regular_result = await service.get_stock_data("sPy")

        assert reference_result is not None
        assert reference_result.symbol == "VTI"
        assert reference_result.is_index is True
        assert reference_result.data[0].close == 104
        assert regular_result is not None
        assert regular_result.symbol == "SPY"
        assert regular_result.is_index is False
        assert regular_result.data[0].close == 101

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("symbol", "is_index"),
        [("SPY", True), ("spy", True), ("AAPL", False), ("^GSPC", False)],
    )
    @pytest.mark.parametrize(
        ("filters", "total_records"),
        [
            ({}, 3),
            ({"period": "1m"}, 2),
            ({"start_date": "2024-03-31"}, 2),
            ({"end_date": "2024-03-31"}, 2),
            ({"start_date": "2024-03-31", "end_date": "2024-04-01"}, 1),
            ({"start_date": "2024-05-01"}, 0),
        ],
    )
    async def test_is_index_with_filters_and_cache(
        self, stock_data_service, symbol, is_index, filters, total_records
    ):
        service = stock_data_service
        data_dir = service.reference_data_dir if is_index else service.stock_data_dir
        data_path = data_dir / f"{symbol.upper()}.csv"
        data_path.write_text(
            "date,open,high,low,close,volume\n"
            "2024-03-30,100,105,99,104,1000\n"
            "2024-03-31,100,105,99,104,1000\n"
            "2024-04-30,100,105,99,104,1000\n"
        )

        with patch.object(service, "_load_data_file", wraps=service._load_data_file) as load:
            result = await service.get_stock_data(symbol, **filters)
            cached_result = await service.get_stock_data(symbol.lower(), **filters)
            load.assert_called_once_with(data_path)

        for response in (result, cached_result):
            assert response is not None
            assert response.symbol == symbol.upper()
            assert response.is_index is is_index
            assert response.total_records == total_records

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("symbol", "wrong_dir"),
        [
            ("SPY", "stock_data_dir"),
            ("AAPL", "reference_data_dir"),
            ("^GSPC", "reference_data_dir"),
        ],
    )
    async def test_no_cross_directory_fallback(self, stock_data_service, symbol, wrong_dir):
        wrong_path = getattr(stock_data_service, wrong_dir) / f"{symbol}.csv"
        wrong_path.write_text(
            "symbol,date,open,high,low,close,volume\n"
            f"{symbol},2024-04-30,100,105,99,104,1000\n"
        )

        with patch.object(stock_data_service, "_load_data_file") as load:
            assert await stock_data_service.get_stock_data(symbol.lower()) is None
            assert await stock_data_service.get_stock_data(symbol) is None
            load.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_index_does_not_affect_regular_symbol_cache(self, stock_data_service):
        service = stock_data_service
        stock_path = service.stock_data_dir / "AAPL.csv"
        stock_path.write_text(
            "symbol,date,open,high,low,close,volume\n"
            "AAPL,2024-04-30,100,105,99,104,1000\n"
        )

        with patch.object(service, "_load_data_file", wraps=service._load_data_file) as load:
            assert await service.get_stock_data("spy") is None
            result = await service.get_stock_data("aapl")
            assert await service.get_stock_data("SPY") is None
            assert await service.get_stock_data("AAPL") is result
            load.assert_called_once_with(stock_path)

        assert result is not None
        assert result.symbol == "AAPL"
        assert result.data[0].close == 104.0

    @pytest.mark.asyncio
    async def test_missing_symbol_returns_none(self, stock_data_service):
        assert await stock_data_service.get_stock_data("MISSING") is None


class TestStockServicePeriodValidation:
    """Tests for period validation and conversion."""

    def test_validate_and_convert_period_none(self):
        """Test that None period returns None."""
        service = StockService()

        result = service._validate_and_convert_period(None)

        assert result is None

    def test_validate_and_convert_period_1m(self):
        """Test converting 1 month period."""
        service = StockService()

        result = service._validate_and_convert_period("1m")

        assert result == 30

    def test_validate_and_convert_period_3m(self):
        """Test converting 3 month period."""
        service = StockService()

        result = service._validate_and_convert_period("3m")

        assert result == 90

    def test_validate_and_convert_period_6m(self):
        """Test converting 6 month period."""
        service = StockService()

        result = service._validate_and_convert_period("6m")

        assert result == 180

    def test_validate_and_convert_period_9m(self):
        """Test converting 9 month period."""
        service = StockService()

        result = service._validate_and_convert_period("9m")

        assert result == 270

    def test_validate_and_convert_period_1y(self):
        """Test converting 1 year period."""
        service = StockService()

        result = service._validate_and_convert_period("1y")

        assert result == 365

    def test_validate_and_convert_period_2y(self):
        """Test converting 2 year period."""
        service = StockService()

        result = service._validate_and_convert_period("2y")

        assert result == 730

    def test_validate_and_convert_period_3y(self):
        """Test converting 3 year period."""
        service = StockService()

        result = service._validate_and_convert_period("3y")

        assert result == 1095

    def test_validate_and_convert_period_uppercase(self):
        """Test that uppercase periods are converted correctly."""
        service = StockService()

        result = service._validate_and_convert_period("1M")

        assert result == 30

    def test_validate_and_convert_period_with_spaces(self):
        """Test that periods with spaces are trimmed."""
        service = StockService()

        result = service._validate_and_convert_period("  1m  ")

        assert result == 30

    def test_validate_and_convert_period_invalid(self):
        """Test that invalid period raises ValueError."""
        service = StockService()

        with pytest.raises(ValueError) as exc_info:
            service._validate_and_convert_period("invalid")

        assert "Invalid period" in str(exc_info.value)


class TestStockServiceTradingDays:
    """Tests for trading day calculations."""

    def test_get_nth_trading_day_after_found(self):
        """Test getting nth trading day when found."""
        service = StockService()
        trading_dates = {
            "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-08", "2024-01-09", "2024-01-10"
        }

        result = service._get_nth_trading_day_after("2024-01-02", 3, trading_dates)

        assert result == "2024-01-05"

    def test_get_nth_trading_day_after_with_weekend(self):
        """Test getting nth trading day skipping weekends."""
        service = StockService()
        # Jan 5 is Friday, Jan 8 is Monday
        trading_dates = {
            "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-08", "2024-01-09", "2024-01-10"
        }

        result = service._get_nth_trading_day_after("2024-01-05", 1, trading_dates)

        assert result == "2024-01-08"

    def test_get_nth_trading_day_after_not_found(self):
        """Test getting nth trading day when not found."""
        service = StockService()
        trading_dates = {"2024-01-02", "2024-01-03"}

        result = service._get_nth_trading_day_after("2024-01-02", 10, trading_dates)

        assert result is None

    def test_get_nth_trading_day_after_invalid_date(self):
        """Test getting nth trading day with invalid start date."""
        service = StockService()
        trading_dates = {"2024-01-02", "2024-01-03"}

        result = service._get_nth_trading_day_after("invalid-date", 1, trading_dates)

        assert result is None


class TestStockServiceDataFilters:
    """Tests for data filtering functionality."""

    def test_apply_data_filters_no_filters(self):
        """Test that no filters returns original data."""
        service = StockService()

        stock_data = [
            StockData(symbol="AAPL", date=datetime(2024, 1, 1), open=100, high=105, low=99, close=104, volume=1000),
            StockData(symbol="AAPL", date=datetime(2024, 1, 2), open=104, high=110, low=103, close=108, volume=1100),
        ]
        response = StockDataResponse(symbol="AAPL", is_index=False, data=stock_data, total_records=2)

        result = service._apply_data_filters(response, None, None, None, None)

        assert result.total_records == 2
        assert len(result.data) == 2

    def test_apply_data_filters_with_date_range(self):
        """Test filtering by date range."""
        service = StockService()

        stock_data = [
            StockData(symbol="AAPL", date=datetime(2024, 1, 1), open=100, high=105, low=99, close=104, volume=1000),
            StockData(symbol="AAPL", date=datetime(2024, 1, 15), open=104, high=110, low=103, close=108, volume=1100),
            StockData(symbol="AAPL", date=datetime(2024, 1, 31), open=108, high=115, low=107, close=112, volume=1200),
        ]
        response = StockDataResponse(symbol="AAPL", is_index=False, data=stock_data, total_records=3)

        start_datetime = datetime(2024, 1, 10)
        end_datetime = datetime(2024, 1, 20)

        result = service._apply_data_filters(response, start_datetime, end_datetime, None, None)

        assert result.total_records == 1
        assert result.data[0].date == datetime(2024, 1, 15)

    def test_apply_data_filters_with_start_date_only(self):
        """Test filtering by start date only."""
        service = StockService()

        stock_data = [
            StockData(symbol="AAPL", date=datetime(2024, 1, 1), open=100, high=105, low=99, close=104, volume=1000),
            StockData(symbol="AAPL", date=datetime(2024, 1, 15), open=104, high=110, low=103, close=108, volume=1100),
            StockData(symbol="AAPL", date=datetime(2024, 1, 31), open=108, high=115, low=107, close=112, volume=1200),
        ]
        response = StockDataResponse(symbol="AAPL", is_index=False, data=stock_data, total_records=3)

        start_datetime = datetime(2024, 1, 10)

        result = service._apply_data_filters(response, start_datetime, None, None, None)

        assert result.total_records == 2

    def test_apply_data_filters_with_end_date_only(self):
        """Test filtering by end date only."""
        service = StockService()

        stock_data = [
            StockData(symbol="AAPL", date=datetime(2024, 1, 1), open=100, high=105, low=99, close=104, volume=1000),
            StockData(symbol="AAPL", date=datetime(2024, 1, 15), open=104, high=110, low=103, close=108, volume=1100),
            StockData(symbol="AAPL", date=datetime(2024, 1, 31), open=108, high=115, low=107, close=112, volume=1200),
        ]
        response = StockDataResponse(symbol="AAPL", is_index=False, data=stock_data, total_records=3)

        end_datetime = datetime(2024, 1, 20)

        result = service._apply_data_filters(response, None, end_datetime, None, None)

        assert result.total_records == 2

    def test_apply_data_filters_period_anchors_on_latest_bar(self):
        """Period windows count back from the newest bar, not from today.

        Price data always lags real time. Anchoring the window on `now` used to
        return a truncated series, or nothing at all once the lag exceeded the
        requested period.
        """
        service = StockService()

        latest = datetime(2024, 4, 24)
        stock_data = [
            StockData(
                symbol="AAPL",
                date=latest - timedelta(days=offset),
                open=100, high=105, low=99, close=104, volume=1000,
            )
            for offset in (0, 30, 80, 200, 400)
        ]
        response = StockDataResponse(symbol="AAPL", is_index=False, data=stock_data, total_records=5)

        result = service._apply_data_filters(response, None, None, 90, "3m")

        assert result.total_records == 3
        assert min(data.date for data in result.data) == latest - timedelta(days=80)

    def test_apply_data_filters_period_with_stale_data(self):
        """A series that ends months ago still returns its most recent window."""
        service = StockService()

        latest = datetime.now() - timedelta(days=400)
        stock_data = [
            StockData(
                symbol="AAPL",
                date=latest - timedelta(days=offset),
                open=100, high=105, low=99, close=104, volume=1000,
            )
            for offset in (0, 10, 20)
        ]
        response = StockDataResponse(symbol="AAPL", is_index=False, data=stock_data, total_records=3)

        result = service._apply_data_filters(response, None, None, 90, "3m")

        assert result.total_records == 3

    def test_apply_data_filters_period_excludes_older_bars(self):
        """Bars outside the window are dropped even when the series is stale."""
        service = StockService()

        latest = datetime.now() - timedelta(days=400)
        stock_data = [
            StockData(
                symbol="AAPL",
                date=latest - timedelta(days=offset),
                open=100, high=105, low=99, close=104, volume=1000,
            )
            for offset in (0, 100)
        ]
        response = StockDataResponse(symbol="AAPL", is_index=False, data=stock_data, total_records=2)

        result = service._apply_data_filters(response, None, None, 90, "3m")

        assert result.total_records == 1
        assert result.data[0].date == latest

    def test_apply_data_filters_period_on_empty_series(self):
        """An empty series stays empty rather than raising."""
        service = StockService()

        response = StockDataResponse(symbol="AAPL", is_index=False, data=[], total_records=0)

        result = service._apply_data_filters(response, None, None, 90, "3m")

        assert result.total_records == 0
        assert result.data == []

    def test_apply_data_filters_date_range_takes_priority_over_period(self):
        """An explicit range wins over the period shorthand."""
        service = StockService()

        stock_data = [
            StockData(symbol="AAPL", date=datetime(2024, 1, 1), open=100, high=105, low=99, close=104, volume=1000),
            StockData(symbol="AAPL", date=datetime(2024, 1, 15), open=104, high=110, low=103, close=108, volume=1100),
            StockData(symbol="AAPL", date=datetime(2024, 1, 31), open=108, high=115, low=107, close=112, volume=1200),
        ]
        response = StockDataResponse(symbol="AAPL", is_index=False, data=stock_data, total_records=3)

        result = service._apply_data_filters(
            response, datetime(2024, 1, 10), datetime(2024, 1, 20), 90, "3m"
        )

        assert result.total_records == 1
        assert result.data[0].date == datetime(2024, 1, 15)


class TestStockServiceCache:
    """Tests for cache-related methods."""

    def test_clear_cache(self):
        """Test clearing the cache."""
        service = StockService()
        service.cache.set("test_key", "test_value")

        service.clear_cache()

        assert service.cache.get("test_key") is None

    def test_cleanup_expired_cache(self):
        """Test cleaning up expired cache entries."""
        service = StockService()
        # Add an entry that will expire
        import time
        service.cache.cache["expired_key"] = ("value", time.time() - 60)
        service.cache.set("valid_key", "valid_value")

        removed = service.cleanup_expired_cache()

        assert removed == 1
        assert service.cache.get("valid_key") == "valid_value"

    def test_get_cache_stats(self):
        """Test getting cache statistics."""
        service = StockService()
        service.cache.set("key1", "value1")
        service.cache.set("key2", "value2")

        stats = service.get_cache_stats()

        assert stats["total_entries"] >= 2
        assert "cache_size_mb" in stats


class TestStockServiceDailyPicks:
    """Tests for daily picks methods."""

    @pytest.mark.asyncio
    async def test_get_daily_picks_both_directions(self):
        """Test getting daily picks for both directions."""
        service = StockService()

        with patch.object(service, 'cache') as mock_cache:
            mock_cache.get.return_value = None

            with patch('app.services.stock_service.picks_service') as mock_picks:
                mock_picks.get_picks_by_date = AsyncMock(return_value=[
                    DailyPick(
                        instrument="AAPL",
                        direction=Direction.RISING,
                        date=datetime(2024, 1, 1),
                        score=0.85
                    ),
                    DailyPick(
                        instrument="GOOGL",
                        direction=Direction.FALLING,
                        date=datetime(2024, 1, 1),
                        score=0.75
                    )
                ])

                result = await service.get_daily_picks(
                    "2024-01-01",
                    direction=None,
                    user_role="admin"
                )

        assert len(result.risings) == 1
        assert len(result.fallings) == 1
        assert result.risings[0].instrument == "AAPL"
        assert result.fallings[0].instrument == "GOOGL"

    @pytest.mark.asyncio
    async def test_get_daily_picks_rising_only(self):
        """Test getting daily picks for rising direction only."""
        service = StockService()

        with patch.object(service, 'cache') as mock_cache:
            mock_cache.get.return_value = None

            with patch('app.services.stock_service.picks_service') as mock_picks:
                mock_picks.get_picks_by_date = AsyncMock(return_value=[
                    DailyPick(
                        instrument="AAPL",
                        direction=Direction.RISING,
                        date=datetime(2024, 1, 1),
                        score=0.85
                    )
                ])

                result = await service.get_daily_picks(
                    "2024-01-01",
                    direction=Direction.RISING,
                    user_role="admin"
                )

        assert len(result.risings) == 1
        assert len(result.fallings) == 0

    @pytest.mark.asyncio
    async def test_get_daily_picks_invalid_date_format(self):
        """Test getting daily picks with invalid date format."""
        service = StockService()

        with pytest.raises(ValueError) as exc_info:
            await service.get_daily_picks(
                "invalid-date",
                direction=None,
                user_role="admin"
            )

        assert "Invalid date format" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_daily_picks_permission_denied(self):
        """Test getting daily picks when free user tries to access recent data."""
        service = StockService()
        # Use a recent date that free users shouldn't access
        recent_date = datetime.now().strftime('%Y-%m-%d')

        with pytest.raises(PermissionError):
            await service.get_daily_picks(
                recent_date,
                direction=None,
                user_role="free"
            )


class TestStockServiceLatestPicks:
    """Tests for latest picks methods."""

    @pytest.mark.asyncio
    async def test_get_latest_picks_premium(self):
        """Test getting latest picks for premium user."""
        service = StockService()

        with patch.object(service, 'cache') as mock_cache:
            mock_cache.get.return_value = None

            with patch('app.services.stock_service.picks_service') as mock_picks:
                mock_picks.get_latest_picks = AsyncMock(return_value=[
                    DailyPick(
                        instrument="AAPL",
                        direction=Direction.RISING,
                        date=datetime(2024, 1, 1),
                        score=0.85
                    )
                ])

                result = await service.get_latest_picks(
                    user_role="premium",
                    premium_expires_at=datetime.now(timezone.utc) + timedelta(days=30)
                )

        assert result.date == "2024-01-01"
        assert len(result.risings) == 1

    @pytest.mark.asyncio
    async def test_get_latest_picks_no_picks(self):
        """Test getting latest picks when no picks available."""
        service = StockService()

        with patch.object(service, 'cache') as mock_cache:
            mock_cache.get.return_value = None

            with patch('app.services.stock_service.picks_service') as mock_picks:
                mock_picks.get_latest_picks = AsyncMock(return_value=[])

                result = await service.get_latest_picks(
                    user_role="premium",
                    premium_expires_at=datetime.now(timezone.utc) + timedelta(days=30)
                )

        assert result.date == ""
        assert len(result.risings) == 0
        assert len(result.fallings) == 0


class TestStockServicePredictions:
    """Tests for predictions methods."""

    @pytest.mark.asyncio
    async def test_get_predictions_success(self):
        """Test getting predictions successfully."""
        service = StockService()

        with patch.object(service, 'cache') as mock_cache:
            mock_cache.get.return_value = None

            with patch('app.services.stock_service.predictions_service') as mock_preds:
                mock_preds.get_predictions_by_date = AsyncMock(return_value=[
                    Prediction(
                        instrument="AAPL",
                        date=datetime(2024, 1, 1),
                        us_rise_1d=0.75,
                        us_rise_5d=0.80
                    )
                ])

                result = await service.get_predictions(
                    "2024-01-01",
                    user_role="admin"
                )

        assert result.date == "2024-01-01"
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_get_predictions_invalid_date(self):
        """Test getting predictions with invalid date."""
        service = StockService()

        with pytest.raises(ValueError) as exc_info:
            await service.get_predictions(
                "invalid",
                user_role="admin"
            )

        assert "Invalid date format" in str(exc_info.value)


class TestStockServiceCacheInvalidation:
    """Tests for cache invalidation methods."""

    def test_invalidate_cache_for_date(self):
        """Test invalidating cache for a specific date."""
        service = StockService()
        service.cache.set("daily_picks:2024-01-01:Direction.RISING", "test_data")
        service.cache.set("predictions:2024-01-01", "test_predictions")

        service.invalidate_cache_for_date("2024-01-01")

        # Predictions cache entry for the date should be invalidated
        assert service.cache.get("predictions:2024-01-01") is None

    def test_invalidate_stock_cache(self):
        """Test invalidating cache for a specific stock."""
        service = StockService()
        service.cache.set("stock_data:AAPL", "test_data")

        service.invalidate_stock_cache("AAPL")

        assert service.cache.get("stock_data:AAPL") is None
