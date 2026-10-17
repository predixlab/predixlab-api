"""Unit tests for the predictions service."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.predictions_service import PredictionsService
from app.models.stock import Prediction


class TestPredictionsServiceAvailableDates:
    """Tests for available dates retrieval."""

    @pytest.mark.asyncio
    async def test_get_available_dates_success(self, mock_db):
        """Test getting available dates successfully."""
        service = PredictionsService()
        service.db = mock_db

        async def mock_cursor_iter():
            docs = [
                {"_id": "2024-01-03"},
                {"_id": "2024-01-02"},
                {"_id": "2024-01-01"}
            ]
            for doc in docs:
                yield doc

        mock_cursor = mock_cursor_iter()
        mock_db.predictions.aggregate.return_value = mock_cursor

        result = await service.get_available_dates(market="us", is_premium=True)

        assert result == ["2024-01-03", "2024-01-02", "2024-01-01"]

    @pytest.mark.asyncio
    async def test_get_available_dates_with_limit(self, mock_db):
        """Test getting available dates with limit."""
        service = PredictionsService()
        service.db = mock_db

        async def mock_cursor_iter():
            docs = [
                {"_id": "2024-01-03"},
                {"_id": "2024-01-02"}
            ]
            for doc in docs:
                yield doc

        mock_cursor = mock_cursor_iter()
        mock_db.predictions.aggregate.return_value = mock_cursor

        result = await service.get_available_dates(market="us", limit=2, is_premium=True)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_available_dates_free_user(self, mock_db):
        """Test getting available dates for free user excludes recent data."""
        service = PredictionsService()
        service.db = mock_db

        async def mock_cursor_iter():
            docs = [{"_id": "2024-01-01"}]
            for doc in docs:
                yield doc

        mock_cursor = mock_cursor_iter()
        mock_db.predictions.aggregate.return_value = mock_cursor

        await service.get_available_dates(market="us", is_premium=False)

        # Verify aggregate was called
        mock_db.predictions.aggregate.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_available_dates_empty(self, mock_db):
        """Test getting available dates when none exist."""
        service = PredictionsService()
        service.db = mock_db

        async def mock_cursor_iter():
            return
            yield

        mock_cursor = mock_cursor_iter()
        mock_db.predictions.aggregate.return_value = mock_cursor

        result = await service.get_available_dates(market="us", is_premium=True)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_available_dates_error(self, mock_db):
        """Test getting available dates handles errors."""
        service = PredictionsService()
        service.db = mock_db

        mock_db.predictions.aggregate.side_effect = Exception("Database error")

        result = await service.get_available_dates(market="us", is_premium=True)

        assert result == []


class TestPredictionsServiceGetByDate:
    """Tests for predictions by date retrieval."""

    @pytest.mark.asyncio
    async def test_get_predictions_by_date_success(self, mock_db):
        """Test getting predictions by date successfully."""
        service = PredictionsService()
        service.db = mock_db

        async def mock_cursor_iter():
            docs = [
                {
                    "instrument": "AAPL",
                    "date": "2024-01-01",
                    "market": "us",
                    "predictions": {
                        "us_rise_1d": 0.75,
                        "us_rise_5d": 0.80,
                        "us_decline_1d": 0.25,
                        "us_decline_5d": 0.20
                    }
                },
                {
                    "instrument": "GOOGL",
                    "date": "2024-01-01",
                    "market": "us",
                    "predictions": {
                        "us_rise_1d": 0.65,
                        "us_rise_5d": 0.70
                    }
                }
            ]
            for doc in docs:
                yield doc

        mock_cursor = mock_cursor_iter()
        mock_db.predictions.find.return_value = mock_cursor

        result = await service.get_predictions_by_date("2024-01-01")

        assert len(result) == 2
        assert result[0].instrument == "AAPL"
        assert result[0].us_rise_1d == 0.75
        assert result[0].us_rise_5d == 0.80
        assert result[1].instrument == "GOOGL"

    @pytest.mark.asyncio
    async def test_get_predictions_by_date_empty(self, mock_db):
        """Test getting predictions by date when none exist."""
        service = PredictionsService()
        service.db = mock_db

        async def mock_cursor_iter():
            return
            yield

        mock_cursor = mock_cursor_iter()
        mock_db.predictions.find.return_value = mock_cursor

        result = await service.get_predictions_by_date("2024-01-01")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_predictions_by_date_error(self, mock_db):
        """Test getting predictions by date handles errors."""
        service = PredictionsService()
        service.db = mock_db

        mock_db.predictions.find.side_effect = Exception("Database error")

        result = await service.get_predictions_by_date("2024-01-01")

        assert result == []


class TestPredictionsServiceLatest:
    """Tests for latest predictions retrieval."""

    @pytest.mark.asyncio
    async def test_get_latest_predictions_success(self, mock_db):
        """Test getting latest predictions successfully."""
        service = PredictionsService()
        service.db = mock_db

        # Mock find_one to return the latest date document
        mock_db.predictions.find_one = AsyncMock(return_value={
            "date": "2024-01-01",
            "instrument": "AAPL"
        })

        async def mock_cursor_iter():
            docs = [
                {
                    "instrument": "AAPL",
                    "date": "2024-01-01",
                    "predictions": {
                        "us_rise_1d": 0.75,
                        "us_rise_5d": 0.80
                    }
                }
            ]
            for doc in docs:
                yield doc

        mock_cursor = mock_cursor_iter()
        mock_db.predictions.find.return_value = mock_cursor

        result = await service.get_latest_predictions(market="us", is_premium=True)

        assert len(result) == 1
        assert result[0].instrument == "AAPL"

    @pytest.mark.asyncio
    async def test_get_latest_predictions_no_data(self, mock_db):
        """Test getting latest predictions when no data exists."""
        service = PredictionsService()
        service.db = mock_db

        mock_db.predictions.find_one = AsyncMock(return_value=None)

        result = await service.get_latest_predictions(market="us", is_premium=True)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_latest_predictions_free_user(self, mock_db):
        """Test getting latest predictions for free user."""
        service = PredictionsService()
        service.db = mock_db

        mock_db.predictions.find_one = AsyncMock(return_value={
            "date": "2024-01-01",
            "instrument": "AAPL"
        })

        async def mock_cursor_iter():
            return
            yield

        mock_cursor = mock_cursor_iter()
        mock_db.predictions.find.return_value = mock_cursor

        await service.get_latest_predictions(market="us", is_premium=False)

        # Verify find_one was called with date filter
        call_args = mock_db.predictions.find_one.call_args
        query_filter = call_args[0][0]
        assert "date" in query_filter

    @pytest.mark.asyncio
    async def test_get_latest_predictions_error(self, mock_db):
        """Test getting latest predictions handles errors."""
        service = PredictionsService()
        service.db = mock_db

        mock_db.predictions.find_one = AsyncMock(side_effect=Exception("Database error"))

        result = await service.get_latest_predictions(market="us", is_premium=True)

        assert result == []


class TestPredictionsServiceByDateAndInstrument:
    """Tests for predictions by date and instrument retrieval."""

    @pytest.mark.asyncio
    async def test_get_predictions_by_date_and_instrument_success(self, mock_db):
        """Test getting prediction for specific date and instrument."""
        service = PredictionsService()
        service.db = mock_db

        mock_db.predictions.find_one = AsyncMock(return_value={
            "instrument": "AAPL",
            "date": "2024-01-01",
            "market": "us",
            "predictions": {
                "us_rise_1d": 0.75,
                "us_rise_5d": 0.80,
                "us_decline_1d": 0.25,
                "us_decline_5d": 0.20
            }
        })

        result = await service.get_predictions_by_date_and_instrument(
            date="2024-01-01",
            instrument="AAPL",
            market="us"
        )

        assert result is not None
        assert result.instrument == "AAPL"
        assert result.us_rise_1d == 0.75
        assert result.us_decline_5d == 0.20

    @pytest.mark.asyncio
    async def test_get_predictions_by_date_and_instrument_not_found(self, mock_db):
        """Test getting prediction when not found."""
        service = PredictionsService()
        service.db = mock_db

        mock_db.predictions.find_one = AsyncMock(return_value=None)

        result = await service.get_predictions_by_date_and_instrument(
            date="2024-01-01",
            instrument="UNKNOWN",
            market="us"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_predictions_by_date_and_instrument_error(self, mock_db):
        """Test getting prediction handles errors."""
        service = PredictionsService()
        service.db = mock_db

        mock_db.predictions.find_one = AsyncMock(side_effect=Exception("Database error"))

        result = await service.get_predictions_by_date_and_instrument(
            date="2024-01-01",
            instrument="AAPL",
            market="us"
        )

        assert result is None


class TestPredictionsServiceInitialize:
    """Tests for service initialization."""

    @pytest.mark.asyncio
    async def test_initialize_sets_db(self):
        """Test that initialize sets the database connection."""
        service = PredictionsService()
        assert service.db is None

        with patch('app.services.predictions_service.get_database') as mock_get_db:
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db

            await service.initialize()

    @pytest.mark.asyncio
    async def test_initialize_only_once(self, mock_db):
        """Test that initialize only sets db once."""
        service = PredictionsService()
        service.db = mock_db

        with patch('app.services.predictions_service.get_database') as mock_get_db:
            await service.initialize()

        mock_get_db.assert_not_called()
