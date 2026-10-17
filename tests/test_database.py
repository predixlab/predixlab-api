from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import OperationFailure

from app import database


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        if name not in self.collections:
            collection = MagicMock()
            collection.create_index = AsyncMock()
            self.collections[name] = collection
        return self.collections[name]


@pytest.mark.asyncio
async def test_create_indexes_continues_after_unique_index_failure():
    fake_database = FakeDatabase()
    fake_database["users"].create_index.side_effect = [
        OperationFailure("Cannot create unique index", code=67),
        None,
        None,
    ]
    fake_database["options_gex"].create_index.side_effect = [
        OperationFailure("Cannot create unique index", code=67),
        None,
        None,
    ]
    fake_database["options_gex_highlights"].create_index.side_effect = [
        OperationFailure("Cannot create unique index", code=67),
        None,
    ]

    with patch.object(database.mongodb, "database", fake_database):
        await database.create_indexes()

    fake_database["options_gex"].create_index.assert_any_await(
        [("date", -1), ("symbol", 1)],
        name="gex_date_symbol",
    )
    fake_database["options_gex"].create_index.assert_any_await(
        [("symbol", 1), ("date", -1)],
        name="gex_symbol_date",
    )
    fake_database["options_anomalies"].create_index.assert_any_await(
        [("date", -1), ("symbol", 1)],
        name="options_anomalies_date_symbol",
    )
    fake_database["options_gex_highlights"].create_index.assert_any_await(
        [("date", -1)],
        name="gex_highlights_date",
    )
    fake_database["options_structure_highlights"].create_index.assert_any_await(
        [("date", -1)],
        name="options_structure_highlights_date",
    )


@pytest.mark.asyncio
async def test_close_mongo_connection_awaits_async_client():
    client = MagicMock()
    client.close = AsyncMock()

    with patch.object(database.mongodb, "client", client):
        await database.close_mongo_connection()

    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_create_index_accepts_equivalent_existing_index():
    fake_database = FakeDatabase()
    fake_database["options_structure"].create_index.side_effect = OperationFailure(
        "Index already exists with a different name: existing_index",
        code=85,
    )

    with patch.object(database.mongodb, "database", fake_database):
        result = await database._create_index(
            "options_structure",
            [("symbol", 1), ("date", -1)],
            name="requested_index",
        )

    assert result is True
