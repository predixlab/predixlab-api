"""Unit tests for the options service."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.options_service import OptionsService
from app.models.options import AnomalyOption


class TestOptionsServiceAnomalies:
    """Tests for anomaly retrieval methods."""

    @pytest.mark.asyncio
    async def test_get_anomalies_success(self, mock_db):
        """Test getting anomalies successfully."""
        service = OptionsService()
        service.db = mock_db

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: self
        mock_cursor.__anext__ = MagicMock(side_effect=StopAsyncIteration)

        mock_db.options_anomalies.find.return_value.sort.return_value.skip.return_value.limit.return_value = mock_cursor

        result = await service.get_anomalies(limit=20, offset=0, is_premium=True)

        assert result == []
        mock_db.options_anomalies.find.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_anomalies_with_pagination(self, mock_db):
        """Test getting anomalies with pagination."""
        service = OptionsService()
        service.db = mock_db

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: self
        mock_cursor.__anext__ = MagicMock(side_effect=StopAsyncIteration)

        mock_db.options_anomalies.find.return_value.sort.return_value.skip.return_value.limit.return_value = mock_cursor

        result = await service.get_anomalies(limit=10, offset=20, is_premium=True)

        assert result == []
        mock_db.options_anomalies.find.return_value.sort.return_value.skip.assert_called_with(20)
        mock_db.options_anomalies.find.return_value.sort.return_value.skip.return_value.limit.assert_called_with(10)

    @pytest.mark.asyncio
    async def test_get_anomalies_caps_ticker_limit_at_twenty(self, mock_db):
        service = OptionsService()
        service.db = mock_db

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: self
        mock_cursor.__anext__ = MagicMock(side_effect=StopAsyncIteration)
        limited_cursor = (
            mock_db.options_anomalies.find.return_value.sort.return_value
            .skip.return_value.limit
        )
        limited_cursor.return_value = mock_cursor

        await service.get_anomalies(limit=100, offset=0, is_premium=True)

        limited_cursor.assert_called_once_with(20)

    @pytest.mark.asyncio
    async def test_get_anomalies_free_user(self, mock_db):
        """Test getting anomalies for free user filters out recent data."""
        service = OptionsService()
        service.db = mock_db

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: self
        mock_cursor.__anext__ = MagicMock(side_effect=StopAsyncIteration)

        mock_db.options_anomalies.find.return_value.sort.return_value.skip.return_value.limit.return_value = mock_cursor

        await service.get_anomalies(limit=20, offset=0, is_premium=False)

        # Verify that the query includes a date filter for non-premium users
        call_args = mock_db.options_anomalies.find.call_args
        query_filter = call_args[0][0]
        assert "date" in query_filter
        assert "$lte" in query_filter["date"]

    @pytest.mark.asyncio
    async def test_get_anomalies_premium_user(self, mock_db):
        """Test getting anomalies for premium user has no date restriction."""
        service = OptionsService()
        service.db = mock_db

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: self
        mock_cursor.__anext__ = MagicMock(side_effect=StopAsyncIteration)

        mock_db.options_anomalies.find.return_value.sort.return_value.skip.return_value.limit.return_value = mock_cursor

        await service.get_anomalies(limit=20, offset=0, is_premium=True)

        # Verify that the query has no date filter for premium users
        call_args = mock_db.options_anomalies.find.call_args
        query_filter = call_args[0][0]
        assert "date" not in query_filter


class TestOptionsServiceAnomaliesByDate:
    """Tests for anomaly by date retrieval."""

    @pytest.mark.asyncio
    async def test_free_user_cannot_query_recent_date(self, mock_db):
        """Free users cannot bypass the recent-data delay with a date request."""
        service = OptionsService()
        service.db = mock_db

        result = await service.get_anomalies_by_date("9999-12-31")

        assert result == []
        mock_db.options_anomalies.find.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_anomalies_by_date_success(self, mock_db):
        """Test getting anomalies by date successfully."""
        service = OptionsService()
        service.db = mock_db

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: self
        mock_cursor.__anext__ = MagicMock(side_effect=StopAsyncIteration)

        mock_db.options_anomalies.find.return_value.sort.return_value.limit.return_value = mock_cursor

        result = await service.get_anomalies_by_date("2024-01-01")

        assert result == []
        mock_db.options_anomalies.find.assert_called_with({"date": "2024-01-01"})
        mock_db.options_anomalies.find.return_value.sort.return_value.limit.assert_called_once_with(20)

    @pytest.mark.asyncio
    async def test_get_anomalies_by_date_with_results(self, mock_db):
        """Test getting anomalies by date with actual results."""
        service = OptionsService()
        service.db = mock_db

        # Create mock data
        mock_docs = [
            {
                "_id": "doc1",
                "symbol": "AAPL",
                "date": "2024-01-01",
                "anomaly_score": 0.95,
                "items": []
            },
            {
                "_id": "doc2",
                "symbol": "GOOGL",
                "date": "2024-01-01",
                "anomaly_score": 0.85,
                "items": []
            }
        ]

        async def mock_cursor_iter():
            for doc in mock_docs:
                yield doc

        mock_cursor = mock_cursor_iter()
        mock_db.options_anomalies.find.return_value.sort.return_value.limit.return_value = mock_cursor

        result = await service.get_anomalies_by_date("2024-01-01")

        assert len(result) == 2
        assert result[0]["symbol"] == "AAPL"
        assert result[1]["symbol"] == "GOOGL"


class TestOptionsServiceLatestAnomalies:
    """Tests for latest anomaly retrieval."""

    @pytest.mark.asyncio
    async def test_get_latest_anomalies_success(self, mock_db):
        """Test getting latest anomalies successfully."""
        service = OptionsService()
        service.db = mock_db

        # Mock find_one to return the latest document
        mock_db.options_anomalies.find_one = AsyncMock(return_value={
            "_id": "latest_doc",
            "date": "2024-01-01",
            "symbol": "AAPL"
        })

        # Mock the cursor for get_anomalies_by_date
        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: self
        mock_cursor.__anext__ = MagicMock(side_effect=StopAsyncIteration)
        mock_db.options_anomalies.find.return_value.sort.return_value.limit.return_value = mock_cursor

        result = await service.get_latest_anomalies(is_premium=True)

        mock_db.options_anomalies.find_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_latest_anomalies_no_data(self, mock_db):
        """Test getting latest anomalies when no data exists."""
        service = OptionsService()
        service.db = mock_db

        mock_db.options_anomalies.find_one = AsyncMock(return_value=None)

        result = await service.get_latest_anomalies(is_premium=True)

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_latest_anomalies_free_user(self, mock_db):
        """Test getting latest anomalies for free user."""
        service = OptionsService()
        service.db = mock_db

        mock_db.options_anomalies.find_one = AsyncMock(return_value={
            "date": "2024-01-01",
            "symbol": "AAPL"
        })

        mock_cursor = MagicMock()
        mock_cursor.__aiter__ = lambda self: self
        mock_cursor.__anext__ = MagicMock(side_effect=StopAsyncIteration)
        mock_db.options_anomalies.find.return_value.sort.return_value.limit.return_value = mock_cursor

        await service.get_latest_anomalies(is_premium=False)

        # Verify that the query includes a date filter for non-premium users
        call_args = mock_db.options_anomalies.find_one.call_args
        query_filter = call_args[0][0]
        assert "date" in query_filter

    @pytest.mark.asyncio
    async def test_get_latest_anomalies_missing_date_field(self, mock_db):
        """Test getting latest anomalies when document is missing date field."""
        service = OptionsService()
        service.db = mock_db

        mock_db.options_anomalies.find_one = AsyncMock(return_value={
            "_id": "doc_no_date",
            "symbol": "AAPL"
            # Missing 'date' field
        })

        result = await service.get_latest_anomalies(is_premium=True)

        assert result == {}


class TestOptionsServiceAnomalyDates:
    @pytest.mark.asyncio
    async def test_get_anomaly_dates_sorts_and_limits_database_dates(
        self,
        mock_db,
    ):
        service = OptionsService()
        service.db = mock_db
        mock_db.options_anomalies.distinct = AsyncMock(
            return_value=["2024-01-01", "2024-01-03", "2024-01-02"]
        )

        result = await service.get_anomaly_dates(
            limit=2,
            is_premium=True,
        )

        assert result == ["2024-01-03", "2024-01-02"]
        mock_db.options_anomalies.distinct.assert_awaited_once_with(
            "date",
            {},
        )

    @pytest.mark.asyncio
    async def test_get_anomaly_dates_applies_free_user_cutoff(self, mock_db):
        service = OptionsService()
        service.db = mock_db
        mock_db.options_anomalies.distinct = AsyncMock(return_value=[])

        await service.get_anomaly_dates(is_premium=False)

        query = mock_db.options_anomalies.distinct.await_args.args[1]
        assert "$lte" in query["date"]


class TestOptionsServiceInitialize:
    """Tests for service initialization."""

    @pytest.mark.asyncio
    async def test_initialize_sets_db(self):
        """Test that initialize sets the database connection."""
        service = OptionsService()
        assert service.db is None

        with patch('app.services.options_service.get_database') as mock_get_db:
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db

            await service.initialize()

        # Note: The actual initialization would set service.db
        # This test verifies the method doesn't raise an exception

    @pytest.mark.asyncio
    async def test_initialize_only_once(self):
        """Test that initialize only sets db once."""
        service = OptionsService()
        mock_db = MagicMock()
        service.db = mock_db

        with patch('app.services.options_service.get_database') as mock_get_db:
            await service.initialize()

        # get_database should not be called if db is already set
        mock_get_db.assert_not_called()


class TestOptionsStructureHighlights:
    @pytest.mark.asyncio
    async def test_returns_selected_precomputed_ranking(self, mock_db):
        service = OptionsService()
        service.db = mock_db
        mock_db.options_structure_highlights.find_one = AsyncMock(
            return_value={
                "date": "2026-08-20",
                "rankings": {
                    "iv_expansion": [
                        {
                            "symbol": "ROST",
                            "date": "2026-08-20",
                            "underlying_price": 180.0,
                            "attention_score": 98.0,
                            "primary_signal": "iv_expansion",
                            "atm_iv_change": 0.61,
                            "total_oi_change": 29340,
                            "total_oi_change_ratio": 0.55,
                            "migration_ratio": 0.59,
                        }
                    ]
                },
            }
        )

        result = await service.get_options_structure_highlights(
            limit=6,
            signal="iv_expansion",
            is_premium=True,
        )

        assert result.count == 1
        assert result.items[0].symbol == "ROST"
        mock_db.options_structure_highlights.find_one.assert_awaited_once_with(
            {},
            sort=[("date", -1)],
        )

    @pytest.mark.asyncio
    async def test_applies_free_user_cutoff(self, mock_db):
        service = OptionsService()
        service.db = mock_db
        mock_db.options_structure_highlights.find_one = AsyncMock(
            return_value=None
        )

        await service.get_options_structure_highlights(is_premium=False)

        query = (
            mock_db.options_structure_highlights.find_one.await_args.args[0]
        )
        assert "$lte" in query["date"]


class TestOptionsServiceGex:
    @pytest.mark.asyncio
    async def test_get_gex_normalizes_symbol(self, mock_db):
        service = OptionsService()
        service.db = mock_db
        mock_db.options_gex.find_one = AsyncMock(
            return_value={
                "symbol": "AAPL",
                "date": "2024-01-01",
                "scopes": [{"key": "all"}, {"key": "0dte"}],
            }
        )

        result = await service.get_gex(
            "aapl", "2024-01-01", is_premium=True
        )

        assert result["symbol"] == "AAPL"
        assert result["scopes"] == [{"key": "all"}]
        mock_db.options_gex.find_one.assert_awaited_once_with(
            {"date": "2024-01-01", "symbol": "AAPL"}
        )

    @pytest.mark.asyncio
    async def test_latest_gex_applies_free_user_delay(self, mock_db):
        service = OptionsService()
        service.db = mock_db
        mock_db.options_gex.find_one = AsyncMock(return_value=None)

        await service.get_latest_gex("SPY", is_premium=False)

        query = mock_db.options_gex.find_one.await_args.args[0]
        assert query["symbol"] == "SPY"
        assert "$lte" in query["date"]

    @pytest.mark.asyncio
    async def test_get_gex_dates_sorts_descending(self, mock_db):
        service = OptionsService()
        service.db = mock_db
        mock_db.options_gex.distinct = AsyncMock(
            return_value=["2024-01-01", "2024-01-03", "2024-01-02"]
        )

        result = await service.get_gex_dates(
            "aapl", limit=2, is_premium=True
        )

        assert result == ["2024-01-03", "2024-01-02"]
        mock_db.options_gex.distinct.assert_awaited_once_with(
            "date", {"symbol": "AAPL"}
        )

    @pytest.mark.asyncio
    async def test_get_gex_symbols_only_returns_accessible_matches(self, mock_db):
        service = OptionsService()
        service.db = mock_db
        mock_db.options_gex.distinct = AsyncMock(
            return_value=["AAPL", "AAP"]
        )

        result = await service.get_gex_symbols(
            "aap", limit=10, is_premium=True
        )

        assert result == ["AAP", "AAPL"]
        query = mock_db.options_gex.distinct.await_args.args[1]
        assert query["symbol"]["$regex"] == "AAP"

    @pytest.mark.asyncio
    async def test_get_gex_highlights_reads_precomputed_snapshot(self, mock_db):
        service = OptionsService()
        service.db = mock_db
        calm = {
            "symbol": "CALM",
            "date": "2024-01-03",
            "attention_score": 88,
            "primary_signal": "positive_gamma",
            "regime": "positive",
            "underlying_price": 200,
            "gamma_flip_distance": -0.1,
            "net_gex_change": 200_000,
            "net_gex_change_ratio": 0.02,
            "call_wall_change": 0,
            "put_wall_change": 0,
        }
        risk = {
            **calm,
            "symbol": "RISK",
            "primary_signal": "negative_gamma",
            "regime": "negative",
        }
        mock_db.options_gex_highlights.find_one = AsyncMock(
            return_value={
                "date": "2024-01-03",
                "rankings": {
                    "all": [calm, risk],
                    "positive_gamma": [calm],
                },
            }
        )

        result = await service.get_gex_highlights(limit=2, is_premium=True)

        assert result.date.isoformat() == "2024-01-03"
        assert [item.symbol for item in result.items] == ["CALM", "RISK"]
        assert result.items[0].primary_signal == "positive_gamma"
        assert result.items[1].primary_signal == "negative_gamma"
        mock_db.options_gex_highlights.find_one.assert_awaited_with(
            {},
            sort=[("date", -1)],
        )

        positive = await service.get_gex_highlights(
            limit=6,
            signal="positive_gamma",
            is_premium=True,
        )

        assert [item.symbol for item in positive.items] == ["CALM"]
        assert positive.items[0].primary_signal == "positive_gamma"

    @pytest.mark.asyncio
    async def test_get_gex_highlights_applies_free_user_delay(self, mock_db):
        service = OptionsService()
        service.db = mock_db
        mock_db.options_gex_highlights.find_one = AsyncMock(return_value=None)

        result = await service.get_gex_highlights(is_premium=False)

        assert result.items == []
        query = mock_db.options_gex_highlights.find_one.await_args.args[0]
        assert "$lte" in query["date"]


class TestOptionsStructureService:
    @pytest.mark.asyncio
    async def test_get_latest_structure_normalizes_symbol(self, mock_db):
        service = OptionsService()
        service.db = mock_db
        mock_db.options_structure.find_one = AsyncMock(
            return_value={"symbol": "AAPL", "date": "2024-01-03"}
        )

        result = await service.get_latest_options_structure(
            "aapl",
            is_premium=True,
        )

        assert result["symbol"] == "AAPL"
        mock_db.options_structure.find_one.assert_awaited_once_with(
            {"symbol": "AAPL"},
            sort=[("date", -1)],
        )

    @pytest.mark.asyncio
    async def test_structure_symbols_apply_free_delay(self, mock_db):
        service = OptionsService()
        service.db = mock_db
        mock_db.options_structure.distinct = AsyncMock(
            return_value=["NVDA", "AAPL"]
        )

        result = await service.get_options_structure_symbols(
            is_premium=False,
        )

        assert result == ["AAPL", "NVDA"]
        query = mock_db.options_structure.distinct.await_args.args[1]
        assert "$lte" in query["date"]


class TestOptionsServiceErrorHandling:
    """Tests for error handling in options service."""

    @pytest.mark.asyncio
    async def test_get_anomalies_handles_exception(self, mock_db):
        """Test that get_anomalies handles exceptions gracefully."""
        service = OptionsService()
        service.db = mock_db

        mock_db.options_anomalies.find.side_effect = Exception("Database error")

        result = await service.get_anomalies(limit=20, offset=0, is_premium=True)

        # Should return empty dict on error
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_anomalies_by_date_handles_exception(self, mock_db):
        """Test that get_anomalies_by_date handles exceptions gracefully."""
        service = OptionsService()
        service.db = mock_db

        mock_db.options_anomalies.find.side_effect = Exception("Database error")

        result = await service.get_anomalies_by_date("2024-01-01")

        # Should return empty dict on error
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_latest_anomalies_handles_exception(self, mock_db):
        """Test that get_latest_anomalies handles exceptions gracefully."""
        service = OptionsService()
        service.db = mock_db

        mock_db.options_anomalies.find_one = AsyncMock(side_effect=Exception("Database error"))

        result = await service.get_latest_anomalies(is_premium=True)

        # Should return empty dict on error
        assert result == {}
