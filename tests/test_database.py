from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import OperationFailure

from app import database
from app.services.agent_key_service import agent_key_service


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


@pytest.mark.asyncio
async def test_authentication_ttl_indexes():
    fake_database = FakeDatabase()
    with patch.object(database.mongodb, "database", fake_database):
        await database.create_indexes()

    fake_database["rate_limits"].create_index.assert_awaited_once_with(
        "expires_at", expireAfterSeconds=0
    )
    fake_database["verification_codes"].create_index.assert_any_await("email")
    fake_database["verification_codes"].create_index.assert_any_await(
        "expires_at", expireAfterSeconds=0
    )
    fake_database["user_sessions"].create_index.assert_any_await(
        "expires_at", expireAfterSeconds=0
    )


async def test_agent_key_list_sort_matches_existing_index(agent_key_database, mock_user):
    fake_database = FakeDatabase()
    with patch.object(database.mongodb, "database", fake_database):
        await database.create_indexes()

    create_index = fake_database["agent_api_keys"].create_index
    create_index.assert_awaited_once_with(
        [("user_id", 1), ("created_at", -1), ("_id", -1)]
    )
    index_keys = create_index.await_args.args[0]
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])

    with patch.object(agent_key_database.agent_api_keys, "find", return_value=cursor):
        await agent_key_service.list(mock_user.id, 10, 0)

    cursor.sort.assert_called_once_with(index_keys)


async def test_unsupported_custom_field_ttl_is_logged_and_nonfatal(caplog):
    fake_database = FakeDatabase()

    async def create_index(keys, **kwargs):
        if "expireAfterSeconds" in kwargs:
            raise OperationFailure("TTL is supported only on _ts", code=67)

    for name in ("verification_codes", "rate_limits", "user_sessions"):
        fake_database[name].create_index.side_effect = create_index

    with patch.object(database.mongodb, "database", fake_database):
        await database.create_indexes()

    for name in ("verification_codes", "rate_limits", "user_sessions"):
        assert f"Failed to create index on {name} for expires_at" in caplog.text
    fake_database["options_structure_highlights"].create_index.assert_awaited()
