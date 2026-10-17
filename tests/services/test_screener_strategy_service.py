"""Unit tests for saved screener strategies."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from app.models.market import (
    ScreenerFilters,
    ScreenerStrategyCreateRequest,
    ScreenerStrategyUpdateRequest,
    SortDirection,
    StockIndex,
    TrendDirection,
)
from app.services.screener_strategy_service import (
    MAX_STRATEGIES_PER_USER,
    ScreenerStrategyService,
)


@pytest.fixture
def service(mock_db):
    mock_db.screener_strategies = MagicMock()
    instance = ScreenerStrategyService()
    instance.db = mock_db
    return instance


def _doc(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "_id": ObjectId(),
        "user_id": "user-1",
        "name": "My screen",
        "filters": {"index": "sp500", "min_rsi": 40.0},
        "sort_by": "return_1m",
        "sort_direction": "asc",
        "created_at": now,
        "updated_at": now,
    }
    doc.update(overrides)
    return doc


class TestRead:
    @pytest.mark.asyncio
    async def test_list_returns_stored_strategies(self, service):
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.to_list = AsyncMock(return_value=[_doc()])
        service.db.screener_strategies.find.return_value = cursor

        strategies = await service.list_strategies("user-1")

        assert [s.name for s in strategies] == ["My screen"]
        assert strategies[0].filters.index == StockIndex.SP500
        assert strategies[0].sort_direction == SortDirection.ASC

    @pytest.mark.asyncio
    async def test_unknown_filter_keys_are_ignored(self, service):
        """A document written by a newer build must still read back."""
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.to_list = AsyncMock(
            return_value=[_doc(filters={"min_rsi": 40.0, "min_sharpe": 1.5})]
        )
        service.db.screener_strategies.find.return_value = cursor

        strategies = await service.list_strategies("user-1")
        assert strategies[0].filters.min_rsi == 40.0

    @pytest.mark.asyncio
    async def test_malformed_id_is_not_found(self, service):
        assert await service.get_strategy("user-1", "not-an-object-id") is None


class TestCreate:
    @pytest.mark.asyncio
    async def test_create_persists_the_filters(self, service):
        service.db.screener_strategies.count_documents = AsyncMock(return_value=0)
        service.db.screener_strategies.find_one = AsyncMock(return_value=None)
        service.db.screener_strategies.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=ObjectId())
        )

        strategy = await service.create_strategy(
            "user-1",
            ScreenerStrategyCreateRequest(
                name="  Breakouts  ",
                filters=ScreenerFilters(
                    min_rsi=60, trends=[TrendDirection.UPTREND], index=StockIndex.NASDAQ100
                ),
            ),
        )

        assert strategy.name == "Breakouts"
        stored = service.db.screener_strategies.insert_one.await_args.args[0]
        assert stored["filters"]["min_rsi"] == 60
        assert stored["filters"]["trends"] == ["uptrend"]
        # exclude_none keeps the document to what the user actually set.
        assert "max_rsi" not in stored["filters"]

    @pytest.mark.asyncio
    async def test_duplicate_name_is_rejected(self, service):
        service.db.screener_strategies.count_documents = AsyncMock(return_value=1)
        service.db.screener_strategies.find_one = AsyncMock(return_value=_doc())

        with pytest.raises(ValueError, match="already exists"):
            await service.create_strategy(
                "user-1", ScreenerStrategyCreateRequest(name="My screen")
            )

    @pytest.mark.asyncio
    async def test_cap_is_enforced(self, service):
        service.db.screener_strategies.count_documents = AsyncMock(
            return_value=MAX_STRATEGIES_PER_USER
        )

        with pytest.raises(ValueError, match="at most"):
            await service.create_strategy(
                "user-1", ScreenerStrategyCreateRequest(name="One more")
            )


class TestUpdate:
    @pytest.mark.asyncio
    async def test_rename_does_not_touch_the_filters(self, service):
        """The update is a patch, so omitting criteria must not clear them."""
        service.db.screener_strategies.find_one = AsyncMock(return_value=None)
        service.db.screener_strategies.find_one_and_update = AsyncMock(
            return_value=_doc(name="Renamed")
        )

        strategy = await service.update_strategy(
            "user-1", str(ObjectId()), ScreenerStrategyUpdateRequest(name="Renamed")
        )

        assert strategy.name == "Renamed"
        update = service.db.screener_strategies.find_one_and_update.await_args.args[1]["$set"]
        assert set(update) == {"name", "updated_at"}

    @pytest.mark.asyncio
    async def test_rename_onto_another_strategy_is_rejected(self, service):
        service.db.screener_strategies.find_one = AsyncMock(return_value=_doc())

        with pytest.raises(ValueError, match="already exists"):
            await service.update_strategy(
                "user-1", str(ObjectId()), ScreenerStrategyUpdateRequest(name="Taken")
            )

    @pytest.mark.asyncio
    async def test_empty_patch_reads_back_unchanged(self, service):
        service.db.screener_strategies.find_one = AsyncMock(return_value=_doc())

        strategy = await service.update_strategy(
            "user-1", str(ObjectId()), ScreenerStrategyUpdateRequest()
        )

        assert strategy.name == "My screen"
        service.db.screener_strategies.find_one_and_update.assert_not_called()


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_reports_whether_a_row_went(self, service):
        service.db.screener_strategies.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=1)
        )
        assert await service.delete_strategy("user-1", str(ObjectId())) is True

        service.db.screener_strategies.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=0)
        )
        assert await service.delete_strategy("user-1", str(ObjectId())) is False

    @pytest.mark.asyncio
    async def test_delete_is_scoped_to_the_owner(self, service):
        service.db.screener_strategies.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=0)
        )
        strategy_id = str(ObjectId())

        await service.delete_strategy("user-1", strategy_id)

        query = service.db.screener_strategies.delete_one.await_args.args[0]
        assert query["user_id"] == "user-1"
