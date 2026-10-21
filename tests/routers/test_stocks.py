"""Unit tests for the stocks router."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user
from app.routers.stocks import (
    get_available_dates, get_daily_picks, get_latest_picks,
    get_predictions, get_latest_predictions, get_stock_data,
    get_all_stocks, run_backtest, search_stocks, router
)
from app.models.user import UserInDB, UserRole
from app.models.stock import (
    Direction, DailyPick, Prediction, StockDataResponse, StockData,
    DailyPicksResponse, PredictionsResponse, AvailableDatesResponse,
    StockListResponse, BacktestResult, MatchType
)


class TestGetAvailableDates:
    """Tests for get_available_dates endpoint."""

    @pytest.mark.asyncio
    async def test_get_available_dates_success(self, mock_premium_user):
        """Test getting available dates successfully."""
        with patch('app.routers.stocks.picks_service') as mock_service:
            mock_service.db = MagicMock()
            mock_service.initialize = MagicMock()
            mock_service.get_available_dates = AsyncMock(return_value=[
                "2024-01-03", "2024-01-02", "2024-01-01"
            ])

            result = await get_available_dates(mock_premium_user)

        assert len(result.dates) == 3
        assert result.dates[0] == "2024-01-03"


class TestGetDailyPicks:
    """Tests for get_daily_picks endpoint."""

    @pytest.mark.asyncio
    async def test_get_daily_picks_success(self, mock_premium_user):
        """Test getting daily picks successfully."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_daily_picks = AsyncMock(return_value=DailyPicksResponse(
                date="2024-01-01",
                risings=[DailyPick(
                    instrument="AAPL",
                    direction=Direction.RISING,
                    date=datetime(2024, 1, 1),
                    score=0.85
                )],
                fallings=[]
            ))

            result = await get_daily_picks(
                date="2024-01-01",
                current_user=mock_premium_user
            )

        assert result.date == "2024-01-01"
        assert len(result.risings) == 1
        assert result.risings[0].instrument == "AAPL"

    @pytest.mark.asyncio
    async def test_get_daily_picks_with_direction(self, mock_premium_user):
        """Test getting daily picks with direction filter."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_daily_picks = AsyncMock(return_value=DailyPicksResponse(
                date="2024-01-01",
                risings=[],
                fallings=[DailyPick(
                    instrument="GOOGL",
                    direction=Direction.FALLING,
                    date=datetime(2024, 1, 1),
                    score=0.75
                )]
            ))

            result = await get_daily_picks(
                date="2024-01-01",
                current_user=mock_premium_user,
                direction=Direction.FALLING
            )

        assert len(result.fallings) == 1

    @pytest.mark.asyncio
    async def test_get_daily_picks_invalid_date(self, mock_premium_user):
        """Test getting daily picks with invalid date."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_daily_picks = AsyncMock(
                side_effect=ValueError("Invalid date format")
            )

            with pytest.raises(HTTPException) as exc_info:
                await get_daily_picks(
                    date="invalid",
                    current_user=mock_premium_user
                )

            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_get_daily_picks_permission_denied(self, mock_user):
        """Test getting daily picks without permission."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_daily_picks = AsyncMock(
                side_effect=PermissionError("Premium required")
            )

            with pytest.raises(HTTPException) as exc_info:
                await get_daily_picks(
                    date="2024-01-01",
                    current_user=mock_user
                )

            assert exc_info.value.status_code == 403


class TestGetLatestPicks:
    """Tests for get_latest_picks endpoint."""

    @pytest.mark.asyncio
    async def test_get_latest_picks_premium(self, mock_premium_user):
        """Test getting latest picks for premium user."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_latest_picks = AsyncMock(return_value=DailyPicksResponse(
                date="2024-01-01",
                risings=[DailyPick(
                    instrument="AAPL",
                    direction=Direction.RISING,
                    date=datetime(2024, 1, 1),
                    score=0.85
                )],
                fallings=[]
            ))

            result = await get_latest_picks(mock_premium_user)

        assert result.date == "2024-01-01"
        assert len(result.risings) == 1


class TestGetPredictions:
    """Tests for get_predictions endpoint."""

    @pytest.mark.asyncio
    async def test_get_predictions_success(self, mock_premium_user):
        """Test getting predictions successfully."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_predictions = AsyncMock(return_value=PredictionsResponse(
                date="2024-01-01",
                predictions=[Prediction(
                    instrument="AAPL",
                    date=datetime(2024, 1, 1),
                    us_rise_1d=0.75,
                    us_rise_5d=0.80
                )],
                total_count=1
            ))

            result = await get_predictions(
                date="2024-01-01",
                current_user=mock_premium_user
            )

        assert result.date == "2024-01-01"
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_get_predictions_invalid_date(self, mock_premium_user):
        """Test getting predictions with invalid date."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_predictions = AsyncMock(
                side_effect=ValueError("Invalid date format")
            )

            with pytest.raises(HTTPException) as exc_info:
                await get_predictions(
                    date="invalid",
                    current_user=mock_premium_user
                )

            assert exc_info.value.status_code == 400


class TestGetLatestPredictions:
    """Tests for get_latest_predictions endpoint."""

    @pytest.mark.asyncio
    async def test_get_latest_predictions_success(self, mock_premium_user):
        """Test getting latest predictions successfully."""
        with patch('app.routers.stocks.predictions_service') as mock_service:
            mock_service.db = MagicMock()
            mock_service.initialize = AsyncMock()
            mock_service.get_latest_predictions = AsyncMock(return_value=[
                Prediction(
                    instrument="AAPL",
                    date=datetime(2024, 1, 1),
                    us_rise_1d=0.75,
                    us_rise_5d=0.80
                )
            ])

            result = await get_latest_predictions(mock_premium_user)

        assert result.total_count == 1
        assert result.predictions[0].instrument == "AAPL"

    @pytest.mark.asyncio
    async def test_get_latest_predictions_not_found(self, mock_premium_user):
        """Test getting latest predictions when none exist."""
        with patch('app.routers.stocks.predictions_service') as mock_service:
            mock_service.db = MagicMock()
            mock_service.initialize = AsyncMock()
            mock_service.get_latest_predictions = AsyncMock(return_value=[])

            with pytest.raises(HTTPException) as exc_info:
                await get_latest_predictions(mock_premium_user)

            assert exc_info.value.status_code == 404


class TestGetStockData:
    """Tests for get_stock_data endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("symbol", "is_index"), [("AAPL", False), ("SPY", True)])
    async def test_get_stock_data_success(self, mock_premium_user, symbol, is_index):
        """Test getting stock data successfully."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_stock_data = AsyncMock(return_value=StockDataResponse(
                symbol=symbol,
                is_index=is_index,
                data=[StockData(
                    symbol=symbol,
                    date=datetime(2024, 1, 1),
                    open=100.0,
                    high=105.0,
                    low=99.0,
                    close=104.0,
                    volume=1000000
                )],
                total_records=1
            ))

            result = await get_stock_data(
                symbol=symbol,
                current_user=mock_premium_user,
                start_date=None,
                end_date=None,
                period=None
            )

        assert result.symbol == symbol
        assert result.is_index is is_index
        assert result.total_records == 1

    @pytest.mark.parametrize(
        ("symbol", "is_index"), [("AAPL", False), ("SPY", True), ("^GSPC", False)]
    )
    def test_get_stock_data_response_includes_is_index(
        self, mock_premium_user, symbol, is_index
    ):
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: mock_premium_user
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_stock_data = AsyncMock(return_value=StockDataResponse(
                symbol=symbol, is_index=is_index, data=[], total_records=0
            ))

            response = TestClient(app).get(f"/api/v1/stocks/data/{symbol}")

        assert response.status_code == 200
        assert response.json()["symbol"] == symbol
        assert response.json()["is_index"] is is_index

    def test_stock_data_openapi_documents_required_is_index(self):
        app = FastAPI()
        app.include_router(router)

        schema = app.openapi()["components"]["schemas"]["StockDataResponse"]

        assert "is_index" in schema["required"]
        assert schema["properties"]["is_index"]["type"] == "boolean"
        assert "US_REFERENCE_SYMBOLS" in schema["properties"]["is_index"]["description"]

    @pytest.mark.asyncio
    async def test_get_stock_data_not_found(self, mock_premium_user):
        """Test getting stock data when not found."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_stock_data = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await get_stock_data(
                    symbol="UNKNOWN",
                    current_user=mock_premium_user,
                    start_date=None,
                    end_date=None,
                    period=None
                )

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_stock_data_with_period(self, mock_premium_user):
        """Test getting stock data with period filter."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_stock_data = AsyncMock(return_value=StockDataResponse(
                symbol="AAPL",
                is_index=False,
                data=[],
                total_records=0
            ))

            result = await get_stock_data(
                symbol="AAPL",
                current_user=mock_premium_user,
                start_date=None,
                end_date=None,
                period="3m"
            )

            # Verify the service was called with correct parameters
            mock_service.get_stock_data.assert_called_once()
            call_args = mock_service.get_stock_data.call_args
            assert call_args[0][0] == "AAPL"
            assert call_args[1]["period"] == "3m"

        assert result.is_index is False


class TestGetAllStocks:
    """Tests for get_all_stocks endpoint."""

    @pytest.mark.asyncio
    async def test_get_all_stocks_success(self, mock_premium_user):
        """Test getting all stocks successfully."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_all_stocks = AsyncMock(return_value=[
                "AAPL", "GOOGL", "MSFT"
            ])

            result = await get_all_stocks(
                current_user=mock_premium_user,
                limit=None,
                offset=0
            )

        assert result.total_count == 3
        assert "AAPL" in result.symbols

    @pytest.mark.asyncio
    async def test_get_all_stocks_with_pagination(self, mock_premium_user):
        """Test getting all stocks with pagination."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_all_stocks = AsyncMock(return_value=[
                "AAPL", "GOOGL", "MSFT", "AMZN", "META"
            ])

            result = await get_all_stocks(
                current_user=mock_premium_user,
                limit=2,
                offset=1
            )

        assert len(result.symbols) == 2
        assert result.total_count == 5


class TestSearchStocks:
    """Tests for search_stocks endpoint."""

    @pytest.mark.asyncio
    async def test_search_stocks_prefix(self, mock_premium_user):
        """Test searching stocks with prefix match."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_all_stocks = AsyncMock(return_value=[
                "AAPL", "AMZN", "AMD", "GOOGL", "MSFT"
            ])

            result = await search_stocks(
                q="A",
                match_type=MatchType.PREFIX,
                limit=50,
                current_user=mock_premium_user
            )

        # Should match AAPL, AMZN, AMD
        assert result.total_count == 3

    @pytest.mark.asyncio
    async def test_search_stocks_infix(self, mock_premium_user):
        """Test searching stocks with infix match."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.get_all_stocks = AsyncMock(return_value=[
                "AAPL", "GOOG", "GOOGL", "META"
            ])

            result = await search_stocks(
                q="OO",
                match_type=MatchType.INFIX,
                limit=50,
                current_user=mock_premium_user
            )

        # Should match GOOG, GOOGL
        assert result.total_count == 2


class TestRunBacktest:
    """Tests for run_backtest endpoint."""

    @pytest.mark.asyncio
    async def test_run_backtest_success(self, mock_premium_user):
        """Test running backtest successfully."""
        with patch('app.routers.stocks.stock_service') as mock_service:
            mock_service.run_backtest = AsyncMock(return_value=BacktestResult(
                symbol="AAPL",
                period_days=90,
                signals=[],
                performance={}
            ))

            result = await run_backtest(
                symbol="AAPL",
                period_days=90,
                current_user=mock_premium_user
            )

        assert result.symbol == "AAPL"
        assert result.period_days == 90
