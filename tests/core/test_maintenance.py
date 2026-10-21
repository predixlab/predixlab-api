import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, call, patch

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.core import maintenance


async def test_purge_removes_only_expired_documents_and_is_idempotent(mongo_auth_db):
    now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    database = {
        name: getattr(mongo_auth_db, name)
        for name in maintenance.EXPIRING_COLLECTIONS
    }
    for collection in database.values():
        for key, expiry in (
            ("expired", now - timedelta(seconds=1)),
            ("boundary", now),
            ("active", now + timedelta(seconds=1)),
            ("missing-expiry", None),
        ):
            document = {"_id": key}
            if expiry is not None:
                document["expires_at"] = expiry
            await collection.insert_one(document)

    with (
        patch.object(maintenance, "get_database", AsyncMock(return_value=database)),
        patch.object(maintenance, "datetime", wraps=datetime) as clock,
    ):
        clock.now.return_value = now
        await maintenance.purge_expired_documents()
        await maintenance.purge_expired_documents()

    for collection in database.values():
        assert set(collection.documents) == {"boundary", "active", "missing-expiry"}
        assert collection.delete_many.await_args_list == [
            call({"expires_at": {"$lt": now}}),
            call({"expires_at": {"$lt": now}}),
        ]


async def test_purge_logs_collection_failure_and_continues(mock_db, caplog):
    database = {
        name: getattr(mock_db, name)
        for name in maintenance.EXPIRING_COLLECTIONS
    }
    for collection in database.values():
        collection.delete_many = AsyncMock()
    database["rate_limits"].delete_many.side_effect = RuntimeError("unavailable")
    with patch.object(maintenance, "get_database", AsyncMock(return_value=database)):
        await maintenance.purge_expired_documents()

    database["user_sessions"].delete_many.assert_awaited_once()
    assert "Failed to purge expired documents from rate_limits" in caplog.text
    assert "unavailable" in caplog.text


async def test_cleanup_runs_immediately_retries_and_uses_configured_interval(monkeypatch, caplog):
    monkeypatch.setattr(settings, "expiry_cleanup_interval_seconds", 123)
    with (
        patch.object(
            maintenance, "purge_expired_documents",
            AsyncMock(side_effect=[RuntimeError("unavailable"), None]),
        ) as purge,
        patch.object(
            maintenance.asyncio, "sleep",
            AsyncMock(side_effect=[None, asyncio.CancelledError]),
        ) as sleep,
        pytest.raises(asyncio.CancelledError),
    ):
        await maintenance.run_expiry_cleanup()

    assert purge.await_count == 2
    assert sleep.await_args_list == [call(123), call(123)]
    assert "Expired-document cleanup failed" in caplog.text


@pytest.mark.parametrize("interval", [0, -1])
def test_cleanup_interval_must_be_positive(interval):
    with pytest.raises(ValidationError, match="expiry_cleanup_interval_seconds"):
        Settings(expiry_cleanup_interval_seconds=interval)


def test_cleanup_interval_reads_environment(monkeypatch):
    monkeypatch.setenv("EXPIRY_CLEANUP_INTERVAL_SECONDS", "90")
    assert Settings(_env_file=None).expiry_cleanup_interval_seconds == 90
