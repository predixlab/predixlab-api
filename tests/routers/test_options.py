"""Unit tests for the options router."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from app.routers.options import (
    get_gex_by_date,
    get_gex_dates,
    get_gex_highlights,
    get_gex_symbols,
    get_latest_gex,
    get_latest_options_structure,
    get_option_anomalies,
    get_option_anomaly_dates,
    get_options_structure_symbols,
    get_options_structure_highlights,
    get_latest_option_anomalies,
    get_option_anomalies_by_date,
)
from app.models.user import UserInDB, UserRole
from app.models.options import AnomalyOption


class TestGetOptionAnomalies:
    """Tests for get_option_anomalies endpoint."""

    @pytest.mark.asyncio
    async def test_get_option_anomalies_success(self, mock_premium_user):
        """Test getting option anomalies successfully."""
        with patch('app.routers.options.options_service') as mock_service:
            mock_service.db = MagicMock()
            mock_service.initialize = AsyncMock()
            mock_service.get_anomalies = AsyncMock(return_value=[
                {"symbol": "AAPL", "date": "2024-01-01", "anomaly_score": 0.95, "items": []},
                {"symbol": "GOOGL", "date": "2024-01-01", "anomaly_score": 0.85, "items": []}
            ])

            result = await get_option_anomalies(
                offset=0,
                limit=20,
                current_user=mock_premium_user
            )

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_option_anomalies_with_pagination(self, mock_premium_user):
        """Test getting option anomalies with pagination."""
        with patch('app.routers.options.options_service') as mock_service:
            mock_service.db = MagicMock()
            mock_service.initialize = AsyncMock()
            mock_service.get_anomalies = AsyncMock(return_value=[
                {"symbol": "AAPL", "date": "2024-01-01", "anomaly_score": 0.95, "items": []}
            ])

            result = await get_option_anomalies(
                offset=10,
                limit=5,
                current_user=mock_premium_user
            )

            mock_service.get_anomalies.assert_called_once_with(
                limit=5,
                offset=10,
                is_premium=True
            )

    @pytest.mark.asyncio
    async def test_get_option_anomalies_free_user(self, mock_user):
        """Test getting option anomalies for free user."""
        with patch('app.routers.options.options_service') as mock_service:
            mock_service.db = MagicMock()
            mock_service.initialize = AsyncMock()
            mock_service.get_anomalies = AsyncMock(return_value=[])

            result = await get_option_anomalies(
                offset=0,
                limit=20,
                current_user=mock_user
            )

            # Verify is_premium=False is passed
            mock_service.get_anomalies.assert_called_once_with(
                limit=20,
                offset=0,
                is_premium=False
            )

    @pytest.mark.asyncio
    async def test_get_option_anomalies_initializes_service(self, mock_premium_user):
        """Test that service is initialized if db is None."""
        with patch('app.routers.options.options_service') as mock_service:
            mock_service.db = None
            mock_service.initialize = AsyncMock()
            mock_service.get_anomalies = AsyncMock(return_value=[])

            await get_option_anomalies(
                offset=0,
                limit=20,
                current_user=mock_premium_user
            )

            mock_service.initialize.assert_called_once()


class TestGexRoutes:
    @pytest.mark.asyncio
    async def test_get_latest_gex(self, mock_premium_user):
        document = {
            "symbol": "SPY",
            "date": "2024-01-01",
            "scopes": [],
        }
        with patch("app.routers.options.options_service") as mock_service:
            mock_service.db = MagicMock()
            mock_service.get_latest_gex = AsyncMock(return_value=document)

            result = await get_latest_gex(
                symbol="spy",
                current_user=mock_premium_user,
            )

        assert result["symbol"] == "SPY"
        mock_service.get_latest_gex.assert_awaited_once_with(
            symbol="spy",
            is_premium=True,
        )

    @pytest.mark.asyncio
    async def test_get_gex_by_date_returns_not_found(self, mock_premium_user):
        with patch("app.routers.options.options_service") as mock_service:
            mock_service.db = MagicMock()
            mock_service.get_gex = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as error:
                await get_gex_by_date(
                    date="2024-01-01",
                    symbol="SPY",
                    current_user=mock_premium_user,
                )

        assert error.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_gex_dates(self, mock_premium_user):
        with patch("app.routers.options.options_service") as mock_service:
            mock_service.db = MagicMock()
            mock_service.get_gex_dates = AsyncMock(
                return_value=["2024-01-03", "2024-01-02"]
            )

            result = await get_gex_dates(
                symbol="aapl",
                limit=60,
                current_user=mock_premium_user,
            )

        assert result.symbol == "AAPL"
        assert result.count == 2

    @pytest.mark.asyncio
    async def test_get_gex_symbols(self, mock_premium_user):
        with patch("app.routers.options.options_service") as mock_service:
            mock_service.db = MagicMock()
            mock_service.get_gex_symbols = AsyncMock(
                return_value=["AAPL", "NVDA"]
            )

            result = await get_gex_symbols(
                query="",
                limit=1000,
                current_user=mock_premium_user,
            )

        assert result.symbols == ["AAPL", "NVDA"]
        assert result.count == 2

    @pytest.mark.asyncio
    async def test_get_gex_highlights(self, mock_premium_user):
        with patch("app.routers.options.options_service") as mock_service:
            mock_service.db = MagicMock()
            mock_service.get_gex_highlights = AsyncMock(
                return_value={
                    "date": "2024-01-03",
                    "items": [],
                    "count": 0,
                }
            )

            result = await get_gex_highlights(
                limit=6,
                signal=None,
                current_user=mock_premium_user,
            )

        assert result["date"] == "2024-01-03"
        mock_service.get_gex_highlights.assert_awaited_once_with(
            limit=6,
            signal=None,
            is_premium=True,
        )


class TestOptionsStructureRoutes:
    @pytest.mark.asyncio
    async def test_get_latest_options_structure(self, mock_premium_user):
        document = {
            "symbol": "AAPL",
            "date": "2024-01-03",
            "previous_date": "2024-01-02",
        }
        with patch("app.routers.options.options_service") as mock_service:
            mock_service.db = MagicMock()
            mock_service.get_latest_options_structure = AsyncMock(
                return_value=document
            )

            result = await get_latest_options_structure(
                symbol="aapl",
                current_user=mock_premium_user,
            )

        assert result["symbol"] == "AAPL"
        mock_service.get_latest_options_structure.assert_awaited_once_with(
            symbol="aapl",
            is_premium=True,
        )

    @pytest.mark.asyncio
    async def test_get_options_structure_symbols(self, mock_premium_user):
        with patch("app.routers.options.options_service") as mock_service:
            mock_service.db = MagicMock()
            mock_service.get_options_structure_symbols = AsyncMock(
                return_value=["AAPL", "NVDA"]
            )

            result = await get_options_structure_symbols(
                query="",
                limit=1000,
                current_user=mock_premium_user,
            )

        assert result.symbols == ["AAPL", "NVDA"]
        assert result.count == 2


class TestGetLatestOptionAnomalies:
    """Tests for get_latest_option_anomalies endpoint."""

    @pytest.mark.asyncio
    async def test_get_latest_option_anomalies_success(self, mock_premium_user):
        """Test getting latest option anomalies successfully."""
        with patch('app.routers.options.options_service') as mock_service:
            mock_service.db = MagicMock()
            mock_service.initialize = AsyncMock()
            mock_service.get_latest_anomalies = AsyncMock(return_value=[
                {"symbol": "AAPL", "date": "2024-01-01", "anomaly_score": 0.95, "items": []}
            ])

            result = await get_latest_option_anomalies(current_user=mock_premium_user)

        assert len(result) == 1
        mock_service.get_latest_anomalies.assert_awaited_once_with(
            is_premium=True,
            limit=20,
        )

    @pytest.mark.asyncio
    async def test_get_latest_option_anomalies_free_user(self, mock_user):
        """Test getting latest option anomalies for free user."""
        with patch('app.routers.options.options_service') as mock_service:
            mock_service.db = MagicMock()
            mock_service.initialize = AsyncMock()
            mock_service.get_latest_anomalies = AsyncMock(return_value=[])

            result = await get_latest_option_anomalies(current_user=mock_user)

            mock_service.get_latest_anomalies.assert_called_once_with(
                is_premium=False,
                limit=20,
            )


class TestGetOptionAnomaliesByDate:
    """Tests for get_option_anomalies_by_date endpoint."""

    @pytest.mark.asyncio
    async def test_get_option_anomalies_by_date_success(self, mock_premium_user):
        """Test getting option anomalies by date successfully."""
        with patch('app.routers.options.options_service') as mock_service:
            mock_service.db = MagicMock()
            mock_service.initialize = AsyncMock()
            mock_service.get_anomalies_by_date = AsyncMock(return_value=[
                {"symbol": "AAPL", "date": "2024-01-01", "anomaly_score": 0.95, "items": []},
                {"symbol": "GOOGL", "date": "2024-01-01", "anomaly_score": 0.85, "items": []}
            ])

            result = await get_option_anomalies_by_date(
                date="2024-01-01",
                current_user=mock_premium_user
            )

        assert len(result) == 2
        mock_service.get_anomalies_by_date.assert_called_once_with(
            date="2024-01-01",
            limit=20,
            is_premium=True,
        )

    @pytest.mark.asyncio
    async def test_get_option_anomalies_by_date_empty(self, mock_premium_user):
        """Test getting option anomalies by date when none exist."""
        with patch('app.routers.options.options_service') as mock_service:
            mock_service.db = MagicMock()
            mock_service.initialize = AsyncMock()
            mock_service.get_anomalies_by_date = AsyncMock(return_value=[])

            result = await get_option_anomalies_by_date(
                date="2024-01-01",
                current_user=mock_premium_user
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_get_option_anomalies_by_date_initializes_service(self, mock_premium_user):
        """Test that service is initialized if db is None."""
        with patch('app.routers.options.options_service') as mock_service:
            mock_service.db = None
            mock_service.initialize = AsyncMock()
            mock_service.get_anomalies_by_date = AsyncMock(return_value=[])

            await get_option_anomalies_by_date(
                date="2024-01-01",
                current_user=mock_premium_user
            )

            mock_service.initialize.assert_called_once()


class TestGetOptionAnomalyDates:
    @pytest.mark.asyncio
    async def test_get_option_anomaly_dates_uses_database_dates(
        self,
        mock_premium_user,
    ):
        with patch("app.routers.options.options_service") as mock_service:
            mock_service.db = MagicMock()
            mock_service.get_anomaly_dates = AsyncMock(
                return_value=["2024-01-03", "2024-01-02"]
            )

            result = await get_option_anomaly_dates(
                limit=45,
                current_user=mock_premium_user,
            )

        assert result.dates == ["2024-01-03", "2024-01-02"]
        assert result.count == 2
        mock_service.get_anomaly_dates.assert_awaited_once_with(
            limit=45,
            is_premium=True,
        )


class TestGetOptionsStructureHighlights:
    @pytest.mark.asyncio
    async def test_gets_precomputed_structure_highlights(
        self,
        mock_premium_user,
    ):
        with patch("app.routers.options.options_service") as mock_service:
            mock_service.db = MagicMock()
            mock_service.get_options_structure_highlights = AsyncMock(
                return_value={"date": "2026-08-20", "items": [], "count": 0}
            )

            result = await get_options_structure_highlights(
                limit=6,
                signal="iv_expansion",
                current_user=mock_premium_user,
            )

        assert result["count"] == 0
        mock_service.get_options_structure_highlights.assert_awaited_once_with(
            limit=6,
            signal="iv_expansion",
            is_premium=True,
        )
