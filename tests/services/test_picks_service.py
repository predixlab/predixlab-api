"""Unit tests for the picks service."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.picks_service import PicksService
from app.models.stock import Direction, DailyPick


class TestPicksServiceAvailableDates:
    """Tests for available dates retrieval."""

    @pytest.mark.asyncio
    async def test_get_available_dates_success(self, mock_db):
        """Test getting available dates successfully."""
        service = PicksService()
        service.db = mock_db

        # Mock the aggregate cursor
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[
            {"_id": "2024-01-03"},
            {"_id": "2024-01-02"},
            {"_id": "2024-01-01"}
        ])
        mock_db.pivot_picks.aggregate = AsyncMock(return_value=mock_cursor)

        result = await service.get_available_dates(market="us", is_premium=True)

        assert result == ["2024-01-03", "2024-01-02", "2024-01-01"]

    @pytest.mark.asyncio
    async def test_get_available_dates_with_limit(self, mock_db):
        """Test getting available dates with limit."""
        service = PicksService()
        service.db = mock_db

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[
            {"_id": "2024-01-03"},
            {"_id": "2024-01-02"}
        ])
        mock_db.pivot_picks.aggregate = AsyncMock(return_value=mock_cursor)

        result = await service.get_available_dates(market="us", limit=2, is_premium=True)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_available_dates_free_user(self, mock_db):
        """Test getting available dates for free user excludes recent data."""
        service = PicksService()
        service.db = mock_db

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[{"_id": "2024-01-01"}])
        mock_db.pivot_picks.aggregate = AsyncMock(return_value=mock_cursor)

        await service.get_available_dates(market="us", is_premium=False)

        # Verify aggregate was called with date filter
        call_args = mock_db.pivot_picks.aggregate.call_args
        pipeline = call_args[0][0]
        match_stage = pipeline[0]["$match"]
        assert "date" in match_stage

    @pytest.mark.asyncio
    async def test_get_available_dates_empty(self, mock_db):
        """Test getting available dates when none exist."""
        service = PicksService()
        service.db = mock_db

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_db.pivot_picks.aggregate = AsyncMock(return_value=mock_cursor)

        result = await service.get_available_dates(market="us", is_premium=True)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_available_dates_error(self, mock_db):
        """Test getting available dates handles errors."""
        service = PicksService()
        service.db = mock_db

        mock_db.pivot_picks.aggregate = AsyncMock(side_effect=Exception("Database error"))

        result = await service.get_available_dates(market="us", is_premium=True)

        assert result == []


class TestPicksServiceGetPicksByDate:
    """Tests for picks by date retrieval."""

    @pytest.mark.asyncio
    async def test_get_picks_by_date_all_directions(self, mock_db):
        """Test getting picks by date for all directions."""
        service = PicksService()
        service.db = mock_db

        async def mock_cursor_iter():
            docs = [
                {
                    "instrument": "AAPL",
                    "direction": "rising",
                    "date": "2024-01-01",
                    "score": 0.85,
                    "strategy": "momentum"
                },
                {
                    "instrument": "GOOGL",
                    "direction": "declining",
                    "date": "2024-01-01",
                    "score": 0.75,
                    "strategy": "reversal"
                }
            ]
            for doc in docs:
                yield doc

        mock_cursor = mock_cursor_iter()
        mock_db.pivot_picks.find.return_value = mock_cursor

        result = await service.get_picks_by_date("2024-01-01")

        assert len(result) == 2
        assert result[0].instrument == "AAPL"
        assert result[0].direction == Direction.RISING
        assert result[1].instrument == "GOOGL"
        assert result[1].direction == Direction.FALLING

    @pytest.mark.asyncio
    async def test_get_picks_by_date_rising_only(self, mock_db):
        """Test getting picks by date for rising direction only."""
        service = PicksService()
        service.db = mock_db

        async def mock_cursor_iter():
            docs = [
                {
                    "instrument": "AAPL",
                    "direction": "rising",
                    "date": "2024-01-01",
                    "score": 0.85
                }
            ]
            for doc in docs:
                yield doc

        mock_cursor = mock_cursor_iter()
        mock_db.pivot_picks.find.return_value = mock_cursor

        result = await service.get_picks_by_date("2024-01-01", direction=Direction.RISING)

        assert len(result) == 1
        assert result[0].direction == Direction.RISING

        # Verify query includes direction filter
        call_args = mock_db.pivot_picks.find.call_args
        query = call_args[0][0]
        assert query["direction"] == "long"

    @pytest.mark.asyncio
    async def test_get_picks_by_date_empty(self, mock_db):
        """Test getting picks by date when none exist."""
        service = PicksService()
        service.db = mock_db

        async def mock_cursor_iter():
            return
            yield  # Make it a generator

        mock_cursor = mock_cursor_iter()
        mock_db.pivot_picks.find.return_value = mock_cursor

        result = await service.get_picks_by_date("2024-01-01")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_picks_by_date_error(self, mock_db):
        """Test getting picks by date handles errors."""
        service = PicksService()
        service.db = mock_db

        mock_db.pivot_picks.find.side_effect = Exception("Database error")

        result = await service.get_picks_by_date("2024-01-01")

        assert result == []


class TestPicksServiceGetPicksBySymbol:
    """Tests for picks by symbol retrieval."""

    @pytest.mark.asyncio
    async def test_get_picks_by_symbol_success(self, mock_db):
        """Test getting picks by symbol successfully."""
        service = PicksService()
        service.db = mock_db

        async def mock_cursor_iter():
            docs = [
                {
                    "instrument": "AAPL",
                    "direction": "rising",
                    "date": "2024-01-01",
                    "score": 0.85
                },
                {
                    "instrument": "AAPL",
                    "direction": "declining",
                    "date": "2024-01-15",
                    "score": 0.75
                }
            ]
            for doc in docs:
                yield doc

        mock_cursor = mock_cursor_iter()
        mock_db.pivot_picks.find.return_value.sort.return_value = mock_cursor

        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 1, 31)

        result = await service.get_picks_by_symbol("AAPL", start_date, end_date)

        assert len(result) == 2
        assert result[0]["date"] == "2024-01-01"
        assert result[0]["direction"] == "rising"
        assert result[1]["date"] == "2024-01-15"

    @pytest.mark.asyncio
    async def test_get_picks_by_symbol_empty(self, mock_db):
        """Test getting picks by symbol when none exist."""
        service = PicksService()
        service.db = mock_db

        async def mock_cursor_iter():
            return
            yield

        mock_cursor = mock_cursor_iter()
        mock_db.pivot_picks.find.return_value.sort.return_value = mock_cursor

        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 1, 31)

        result = await service.get_picks_by_symbol("UNKNOWN", start_date, end_date)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_picks_by_symbol_error(self, mock_db):
        """Test getting picks by symbol handles errors."""
        service = PicksService()
        service.db = mock_db

        mock_db.pivot_picks.find.side_effect = Exception("Database error")

        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 1, 31)

        result = await service.get_picks_by_symbol("AAPL", start_date, end_date)

        assert result == []


class TestPicksServiceLatestPicks:
    """Tests for latest picks retrieval."""

    @pytest.mark.asyncio
    async def test_get_latest_picks_success(self, mock_db):
        """Latest compatibility picks come from the pivot_picks schema."""
        service = PicksService()
        service.db = mock_db

        # Mock find_one to return the latest date document
        mock_db.pivot_picks.find_one = AsyncMock(return_value={
            "date": "2023-10-23",
            "symbol": "EXPE"
        })

        # Mock the cursor for getting all picks on that date
        async def mock_cursor_iter():
            docs = [
                {
                    "symbol": "EXPE",
                    "direction": "long",
                    "date": "2023-10-23",
                    "event": "BUY_TURN",
                    "score": 0.0163
                },
                {
                    "symbol": "TSLA",
                    "direction": "short",
                    "date": "2023-10-23",
                    "event": "SELL_TURN",
                    "score": -0.02
                }
            ]
            for doc in docs:
                yield doc

        mock_cursor = mock_cursor_iter()
        mock_db.pivot_picks.find.return_value = mock_cursor

        result = await service.get_latest_picks(market="us", is_premium=True)

        assert len(result) == 2
        assert result[0].instrument == "EXPE"
        assert result[0].direction == Direction.RISING
        assert result[0].strategy == "BUY_TURN"
        assert result[1].direction == Direction.FALLING

    @pytest.mark.asyncio
    async def test_get_latest_picks_no_data(self, mock_db):
        """Test getting latest picks when no data exists."""
        service = PicksService()
        service.db = mock_db

        mock_db.pivot_picks.find_one = AsyncMock(return_value=None)

        result = await service.get_latest_picks(market="us", is_premium=True)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_latest_picks_free_user(self, mock_db):
        """Test getting latest picks for free user."""
        service = PicksService()
        service.db = mock_db

        mock_db.pivot_picks.find_one = AsyncMock(return_value={
            "date": "2024-01-01",
            "instrument": "AAPL"
        })

        async def mock_cursor_iter():
            return
            yield

        mock_cursor = mock_cursor_iter()
        mock_db.pivot_picks.find.return_value = mock_cursor

        await service.get_latest_picks(market="us", is_premium=False)

        # Verify find_one was called with date filter
        call_args = mock_db.pivot_picks.find_one.call_args
        query_filter = call_args[0][0]
        assert "date" in query_filter

    @pytest.mark.asyncio
    async def test_get_latest_picks_error(self, mock_db):
        """Test getting latest picks handles errors."""
        service = PicksService()
        service.db = mock_db

        mock_db.pivot_picks.find_one = AsyncMock(side_effect=Exception("Database error"))

        result = await service.get_latest_picks(market="us", is_premium=True)

        assert result == []


class TestPicksServiceInitialize:
    """Tests for service initialization."""

    @pytest.mark.asyncio
    async def test_initialize_sets_db(self):
        """Test that initialize sets the database connection."""
        service = PicksService()
        assert service.db is None

        with patch('app.services.picks_service.get_database') as mock_get_db:
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db

            await service.initialize()

    @pytest.mark.asyncio
    async def test_initialize_only_once(self, mock_db):
        """Test that initialize only sets db once."""
        service = PicksService()
        service.db = mock_db

        with patch('app.services.picks_service.get_database') as mock_get_db:
            await service.initialize()

        mock_get_db.assert_not_called()
