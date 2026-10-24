"""API-key storage, least privilege and lifecycle concurrency."""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.agent import AgentApiKeyCreate
from app.config import settings
from app.services.agent_key_service import KEY_PATTERN, agent_key_service
from app.services.user_service import UserService


async def test_issue_stores_only_a_digest_and_returns_secret_once(agent_key_database, mock_user):
    issued = await agent_key_service.create(
        mock_user.id, AgentApiKeyCreate(name="  Research agent  ")
    )
    assert KEY_PATTERN.fullmatch(issued.api_key)
    assert issued.key.name == "Research agent"
    assert issued.key.scope == "mcp:read"
    assert issued.key.expires_at - issued.key.created_at == timedelta(days=90)
    document = agent_key_database.agent_api_keys.documents[issued.key.id]
    assert document["key_hash"] == hashlib.sha256(issued.api_key.encode("ascii")).hexdigest()
    assert issued.api_key not in repr(document)
    assert issued.api_key not in repr(issued)
    assert issued.api_key not in issued.key.model_dump_json()
    listed = await agent_key_service.list(mock_user.id, 10, 0)
    assert issued.api_key not in listed.model_dump_json()
    assert "key_hash" not in listed.model_dump_json()


@pytest.mark.parametrize("days", [30, 90, 365])
async def test_explicit_lifetimes(agent_key_database, mock_user, days):
    issued = await agent_key_service.create(
        mock_user.id, AgentApiKeyCreate(name="Agent", expires_in_days=days)
    )
    assert issued.key.expires_at - issued.key.created_at == timedelta(days=days)


@pytest.mark.parametrize(
    "data",
    [{"name": ""}, {"name": " "}, {"name": "a" * 65},
     {"name": "Agent", "expires_in_days": 0}, {"name": "Agent", "expires_in_days": 366}],
)
def test_key_settings_are_validated(data):
    with pytest.raises(ValidationError):
        AgentApiKeyCreate.model_validate(data)


async def test_key_names_are_not_part_of_the_credential(agent_key_database, mock_user):
    first = await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Same name"))
    second = await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Same name"))
    assert first.key.id != second.key.id
    assert first.api_key != second.api_key


async def test_authentication_records_usage_and_checks_live_account(
    agent_key_database, agent_tokens, mock_user
):
    issued = agent_tokens[mock_user.id]
    assert issued.key.last_used_at is None
    result = await agent_key_service.authenticate(issued.api_key)
    assert result is mock_user
    document = agent_key_database.agent_api_keys.documents[issued.key.id]
    assert document["last_used_at"] is not None
    mock_user.is_active = False
    with pytest.raises(HTTPException) as error:
        await agent_key_service.authenticate(issued.api_key)
    assert error.value.status_code == 403


@pytest.mark.parametrize(
    "state,reason",
    [
        ("wrong_secret", "secret_mismatch"),
        ("expired", "expired"),
        ("revoked", "revoked"),
        ("scope", "invalid_scope"),
        ("missing_verifier", "missing_verifier"),
        ("unknown_key", "unknown_key"),
        ("deleted_user", "account check rejected: status=401"),
    ],
)
async def test_bad_keys_are_denied(agent_key_database, agent_tokens, mock_user, state, reason, caplog):
    issued = agent_tokens[mock_user.id]
    document = agent_key_database.agent_api_keys.documents[issued.key.id]
    token = issued.api_key
    if state == "wrong_secret":
        token = f"plmcp_{issued.key.id}_{'x' * 43}"
    elif state == "expired":
        document["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif state == "revoked":
        document["revoked_at"] = datetime.now(timezone.utc)
    elif state == "scope":
        document["scope"] = "admin"
    elif state == "missing_verifier":
        document["key_hash"] = None
    elif state == "unknown_key":
        del agent_key_database.agent_api_keys.documents[issued.key.id]
    else:
        document["user_id"] = "deleted-user"
    with pytest.raises(HTTPException) as error:
        await agent_key_service.authenticate(token)
    assert error.value.status_code == 401
    assert reason in caplog.text
    assert issued.api_key not in caplog.text
    assert token not in caplog.text
    assert issued.key.id not in caplog.text
    assert "key_hash" not in caplog.text


@pytest.mark.parametrize("token", ["", "plmcp_invalid", "api-key", "x" * 10000, "\u4e2d\u6587"])
async def test_malformed_keys_do_not_reach_storage(agent_key_database, token):
    with pytest.raises(HTTPException) as error:
        await agent_key_service.authenticate(token)
    assert error.value.status_code == 401
    agent_key_database.agent_api_keys.find_one.assert_not_awaited()


@pytest.mark.parametrize(
    "token,reason",
    [
        ("${input:predixlab-api-key}", "unresolved_input"),
        ("${env:PREDIXLAB_MCP_API_KEY}", "unresolved_environment"),
        ("not-a-real-key", "invalid_format"),
    ],
)
async def test_invalid_input_diagnostics_do_not_log_credentials(
    agent_key_database, token, reason, caplog
):
    with pytest.raises(HTTPException) as error:
        await agent_key_service.authenticate(token)
    assert error.value.status_code == 401
    assert error.value.detail == "Invalid or expired MCP API key"
    assert f"reason={reason}" in caplog.text
    assert token not in caplog.text
    agent_key_database.agent_api_keys.find_one.assert_not_awaited()


async def test_rotation_replaces_secret_preserves_metadata_and_invalidates_old_key(
    agent_key_database, agent_tokens, mock_user
):
    original = agent_tokens[mock_user.id]
    rotated = await agent_key_service.rotate(mock_user.id, original.key.id, 1)
    assert rotated.key.id == original.key.id
    assert rotated.key.name == original.key.name
    assert rotated.key.version == 2
    assert rotated.key.created_at == original.key.created_at
    assert rotated.api_key != original.api_key
    assert rotated.key.expires_at >= original.key.expires_at
    with pytest.raises(HTTPException) as error:
        await agent_key_service.authenticate(original.api_key)
    assert error.value.status_code == 401
    assert await agent_key_service.authenticate(rotated.api_key) is mock_user
    with pytest.raises(HTTPException) as stale:
        await agent_key_service.rotate(mock_user.id, original.key.id, 1)
    assert stale.value.status_code == 409


async def test_expired_keys_can_be_rotated_but_revoked_keys_cannot(
    agent_key_database, agent_tokens, mock_user
):
    issued = agent_tokens[mock_user.id]
    agent_key_database.agent_api_keys.documents[issued.key.id]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    )
    rotated = await agent_key_service.rotate(mock_user.id, issued.key.id, 1)
    assert rotated.key.status == "active"
    await agent_key_service.revoke(mock_user.id, issued.key.id, 2)
    assert agent_key_database.agent_api_keys.documents[issued.key.id]["key_hash"] is None
    with pytest.raises(HTTPException) as error:
        await agent_key_service.authenticate(rotated.api_key)
    assert error.value.status_code == 401
    with pytest.raises(HTTPException) as revoked:
        await agent_key_service.rotate(mock_user.id, issued.key.id, 3)
    assert revoked.value.status_code == 409


@pytest.mark.parametrize("operation", ["rotate", "revoke"])
async def test_other_users_cannot_manage_keys(
    agent_key_database, agent_tokens, mock_user, mock_premium_user, operation
):
    issued = agent_tokens[mock_user.id]
    with pytest.raises(HTTPException) as error:
        await getattr(agent_key_service, operation)(mock_premium_user.id, issued.key.id, 1)
    assert error.value.status_code == 404
    assert await agent_key_service.authenticate(issued.api_key) is mock_user


async def test_only_one_concurrent_rotation_succeeds(
    agent_key_database, agent_tokens, mock_user
):
    key_id = agent_tokens[mock_user.id].key.id
    atomic_update = agent_key_database.agent_api_keys.find_one_and_update.side_effect

    async def yielding_update(*args, **kwargs):
        await asyncio.sleep(0)
        return await atomic_update(*args, **kwargs)

    agent_key_database.agent_api_keys.find_one_and_update.side_effect = yielding_update
    results = await asyncio.gather(*(
        agent_key_service.rotate(mock_user.id, key_id, 1) for _ in range(5)
    ), return_exceptions=True)
    errors = [result for result in results if isinstance(result, HTTPException)]
    assert len(errors) == 4
    assert all(error.status_code == 409 for error in errors)
    assert agent_key_database.agent_api_keys.documents[key_id]["version"] == 2


async def test_rotation_and_revocation_do_not_overwrite_each_other(
    agent_key_database, agent_tokens, mock_user
):
    key_id = agent_tokens[mock_user.id].key.id
    results = await asyncio.gather(
        agent_key_service.rotate(mock_user.id, key_id, 1),
        agent_key_service.revoke(mock_user.id, key_id, 1),
        return_exceptions=True,
    )
    errors = [result for result in results if isinstance(result, HTTPException)]
    assert len(errors) == 1
    assert errors[0].status_code == 409
    assert agent_key_database.agent_api_keys.documents[key_id]["version"] == 2


@pytest.mark.parametrize("operation", ["rotate", "revoke"])
async def test_key_is_rechecked_after_account_lookup(
    agent_key_database, agent_tokens, mock_user, operation
):
    original = agent_tokens[mock_user.id]

    async def account_lookup(user_id):
        await getattr(agent_key_service, operation)(user_id, original.key.id, 1)
        return mock_user

    with patch(
        "app.core.dependencies.user_service.get_user_by_id",
        AsyncMock(side_effect=account_lookup),
    ):
        with pytest.raises(HTTPException) as error:
            await agent_key_service.authenticate(original.api_key)
    assert error.value.status_code == 401


async def test_listing_is_owned_paginated_and_has_explicit_states(
    agent_key_database, agent_tokens, mock_user, mock_premium_user
):
    first = agent_tokens[mock_user.id]
    agent_key_database.agent_api_keys.documents[first.key.id]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    )
    second = await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Second"))
    await agent_key_service.revoke(mock_user.id, second.key.id, 1)
    listed = await agent_key_service.list(mock_user.id, 1, 0)
    assert listed.total_count == 2
    assert len(listed.items) == 1
    assert listed.items[0].id == second.key.id
    assert listed.items[0].status == "revoked"
    next_page = await agent_key_service.list(mock_user.id, 1, 1)
    assert next_page.items[0].status == "expired"
    assert agent_tokens[mock_premium_user.id].key.id not in listed.model_dump_json()


@pytest.mark.parametrize(
    "limit,offset,expected_ids",
    [
        (10, 0, ["e" * 32, "1" * 32, "f" * 32]),
        (1, 0, ["e" * 32]),
        (1, 1, ["1" * 32]),
        (1, 2, ["f" * 32]),
    ],
)
async def test_listing_is_newest_first_with_descending_id_tiebreak(
    agent_key_database, mock_user, limit, offset, expected_ids
):
    now = datetime.now(timezone.utc)
    with patch(
        "app.services.agent_key_service.secrets.token_hex",
        side_effect=["f" * 32, "1" * 32, "e" * 32],
    ):
        for name, created_at in [
            ("Older", now - timedelta(days=1)),
            ("Newer lower ID", now),
            ("Newer higher ID", now),
        ]:
            issued = await agent_key_service.create(
                mock_user.id, AgentApiKeyCreate(name=name)
            )
            agent_key_database.agent_api_keys.documents[issued.key.id]["created_at"] = created_at

    listed = await agent_key_service.list(mock_user.id, limit, offset)

    assert [key.id for key in listed.items] == expected_ids
    assert listed.total_count == 3
    assert listed.limit == limit
    assert listed.offset == offset


async def test_mongo_naive_utc_dates_are_supported(agent_key_database, agent_tokens, mock_user):
    issued = agent_tokens[mock_user.id]
    document = agent_key_database.agent_api_keys.documents[issued.key.id]
    document["created_at"] = document["created_at"].replace(tzinfo=None)
    document["expires_at"] = document["expires_at"].replace(tzinfo=None)
    listed = await agent_key_service.list(mock_user.id, 10, 0)
    assert listed.items[0].created_at.tzinfo == timezone.utc
    assert listed.items[0].expires_at.tzinfo == timezone.utc


async def test_storage_failures_are_not_success_shaped(agent_key_database, mock_user):
    agent_key_database.agent_api_keys.insert_one.side_effect = RuntimeError("storage unavailable")
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Agent"))


async def test_uninitialized_key_storage_fails_closed(mock_user):
    with patch("app.services.agent_key_service.get_database", AsyncMock(return_value=None)):
        with pytest.raises(RuntimeError, match="not initialized"):
            await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Agent"))


async def test_lifecycle_logs_never_contain_secret_or_digest(
    agent_key_database, mock_user, caplog
):
    caplog.set_level("INFO", logger="app.services.agent_key_service")
    issued = await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Agent"))
    rotated = await agent_key_service.rotate(mock_user.id, issued.key.id, 1)
    await agent_key_service.revoke(mock_user.id, issued.key.id, 2)
    for token in (issued.api_key, rotated.api_key):
        assert token not in caplog.text
        assert hashlib.sha256(token.encode("ascii")).hexdigest() not in caplog.text
    assert "Created MCP API key" in caplog.text
    assert "Rotated MCP API key" in caplog.text
    assert "Revoked MCP API key" in caplog.text


async def test_account_deletion_removes_all_agent_keys(agent_key_database, agent_tokens, mock_user):
    service = UserService()
    service.db = agent_key_database
    for name in ("watchlists", "alerts", "screener_strategies", "referrals"):
        getattr(agent_key_database, name).delete_many = AsyncMock()
    agent_key_database.users.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    assert await service.delete_account_data(mock_user)
    remaining = agent_key_database.agent_api_keys.documents
    assert agent_tokens[mock_user.id].key.id not in remaining
    assert len(remaining) == 1
    assert mock_user.id not in agent_key_database.agent_api_key_quotas.documents


async def test_per_user_cap_is_enforced_and_revocation_releases_a_slot(
    agent_key_database, mock_user, mock_premium_user, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 2)
    first = await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="First"))
    second = await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Second"))
    with pytest.raises(HTTPException) as error:
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Too many"))
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "mcp_key_limit"
    assert error.value.detail["max_keys"] == 2
    await agent_key_service.create(mock_premium_user.id, AgentApiKeyCreate(name="Other account"))
    rotated = await agent_key_service.rotate(mock_user.id, second.key.id, 1)
    assert rotated.key.version == 2
    await agent_key_service.revoke(mock_user.id, first.key.id, 1)
    await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Replacement"))
    listed = await agent_key_service.list(mock_user.id, 1, 1)
    assert listed.max_keys == 2
    assert listed.non_revoked_count == 2
    assert listed.total_count == 3


async def test_expired_keys_still_count_and_rotation_needs_no_new_slot(
    agent_key_database, mock_user, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 1)
    issued = await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Expired"))
    agent_key_database.agent_api_keys.documents[issued.key.id]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    )
    with pytest.raises(HTTPException) as error:
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Blocked"))
    assert error.value.status_code == 409
    assert (await agent_key_service.rotate(mock_user.id, issued.key.id, 1)).key.status == "active"


async def test_concurrent_creations_cannot_exceed_cap(agent_key_database, mock_user, monkeypatch):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 3)
    collection = agent_key_database.agent_api_key_quotas
    atomic_update = collection.find_one_and_update.side_effect

    async def concurrent_update(*args, **kwargs):
        await asyncio.sleep(0)
        return await atomic_update(*args, **kwargs)

    collection.find_one_and_update.side_effect = concurrent_update
    results = await asyncio.gather(*(
        agent_key_service.create(mock_user.id, AgentApiKeyCreate(name=f"Agent {index}"))
        for index in range(12)
    ), return_exceptions=True)
    errors = [result for result in results if isinstance(result, HTTPException)]
    assert len(errors) == 9
    assert all(error.status_code == 409 and error.detail["code"] == "mcp_key_limit" for error in errors)
    assert len(agent_key_database.agent_api_keys.documents) == 3
    assert len(collection.documents[mock_user.id]["key_ids"]) == 3


async def test_existing_keys_are_counted_without_migration(
    agent_key_database, mock_user, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 2)
    for index in range(2):
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name=f"Legacy {index}"))
    agent_key_database.agent_api_key_quotas.documents.clear()
    with pytest.raises(HTTPException) as error:
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Blocked"))
    assert error.value.status_code == 409
    assert len(agent_key_database.agent_api_key_quotas.documents[mock_user.id]["key_ids"]) == 2


async def test_failed_insert_keeps_a_recoverable_intent_without_freeing_capacity(
    agent_key_database, mock_user, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 1)
    collection = agent_key_database.agent_api_keys
    insert = collection.insert_one.side_effect
    collection.insert_one.side_effect = RuntimeError("storage unavailable")
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Failed"))
    quota = agent_key_database.agent_api_key_quotas.documents[mock_user.id]
    assert len(quota["key_ids"]) == len(quota["pending"]) == 1
    collection.insert_one.side_effect = insert
    recovered = await agent_key_service.list(mock_user.id, 10, 0)
    assert recovered.total_count == recovered.non_revoked_count == 1
    assert quota["pending"] == {}
    await agent_key_service.revoke(mock_user.id, recovered.items[0].id, 1)
    await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Retry after reconciliation"))


async def test_lost_insert_acknowledgement_does_not_free_a_persisted_key_slot(
    agent_key_database, mock_user, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 1)
    collection = agent_key_database.agent_api_keys
    insert = collection.insert_one.side_effect

    async def acknowledged_late(document):
        await insert(document)
        raise RuntimeError("acknowledgement lost")

    collection.insert_one.side_effect = acknowledged_late
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Persisted"))
    collection.insert_one.side_effect = insert
    with pytest.raises(HTTPException) as error:
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Blocked"))
    assert error.value.status_code == 409
    assert len(collection.documents) == 1


async def test_revoked_slot_is_reconciled_after_interrupted_release(
    agent_key_database, mock_user, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 1)
    issued = await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="First"))
    quotas = agent_key_database.agent_api_key_quotas
    update = quotas.update_one.side_effect
    quotas.update_one.side_effect = RuntimeError("release interrupted")
    with pytest.raises(RuntimeError, match="release interrupted"):
        await agent_key_service.revoke(mock_user.id, issued.key.id, 1)
    assert agent_key_database.agent_api_keys.documents[issued.key.id]["revoked_at"] is not None
    quotas.update_one.side_effect = update
    replacement = await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Replacement"))
    assert quotas.documents[mock_user.id]["key_ids"] == [replacement.key.id]


async def test_cancelled_creation_can_be_recovered_from_key_management(
    agent_key_database, mock_user, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 1)
    collection = agent_key_database.agent_api_keys
    insert = collection.insert_one.side_effect
    collection.insert_one.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Interrupted"))
    collection.insert_one.side_effect = insert
    listed = await agent_key_service.list(mock_user.id, 10, 0)
    assert listed.total_count == 1
    assert listed.items[0].name == "Interrupted"
    await agent_key_service.revoke(mock_user.id, listed.items[0].id, 1)
    await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Replacement"))


async def test_uncertain_reservation_acknowledgement_is_recoverable(
    agent_key_database, mock_user, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 1)
    quotas = agent_key_database.agent_api_key_quotas
    update = quotas.find_one_and_update.side_effect

    async def lost_acknowledgement(query, changes, **kwargs):
        result = await update(query, changes, **kwargs)
        if "$addToSet" in changes:
            raise RuntimeError("reservation acknowledgement lost")
        return result

    quotas.find_one_and_update.side_effect = lost_acknowledgement
    with pytest.raises(RuntimeError, match="reservation acknowledgement lost"):
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Pending"))
    quotas.find_one_and_update.side_effect = update
    listed = await agent_key_service.list(mock_user.id, 10, 0)
    assert listed.total_count == listed.non_revoked_count == 1
    assert quotas.documents[mock_user.id]["pending"] == {}


async def test_concurrent_recovery_materializes_one_key_without_freeing_its_slot(
    agent_key_database, mock_user, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 1)
    keys = agent_key_database.agent_api_keys
    insert = keys.insert_one.side_effect
    keys.insert_one.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Pending"))

    async def concurrent_insert(document):
        await asyncio.sleep(0)
        return await insert(document)

    keys.insert_one.side_effect = concurrent_insert
    lists = await asyncio.gather(*(
        agent_key_service.list(mock_user.id, 10, 0) for _ in range(8)
    ))
    assert all(result.total_count == result.non_revoked_count == 1 for result in lists)
    assert len(keys.documents) == 1
    with pytest.raises(HTTPException) as error:
        await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Blocked"))
    assert error.value.status_code == 409


async def test_late_creator_cannot_restore_a_key_revoked_after_recovery(
    agent_key_database, mock_user, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 1)
    keys = agent_key_database.agent_api_keys
    insert = keys.insert_one.side_effect
    started = asyncio.Event()
    resume = asyncio.Event()
    first = True

    async def delayed_insert(document):
        nonlocal first
        if first:
            first = False
            started.set()
            await resume.wait()
        return await insert(document)

    keys.insert_one.side_effect = delayed_insert
    creator = asyncio.create_task(
        agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Interrupted"))
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        recovered = await agent_key_service.list(mock_user.id, 10, 0)
        await agent_key_service.revoke(mock_user.id, recovered.items[0].id, 1)
        replacement = await agent_key_service.create(mock_user.id, AgentApiKeyCreate(name="Replacement"))
        resume.set()
        with pytest.raises(HTTPException) as error:
            await creator
        assert error.value.status_code == 409
        assert keys.documents[recovered.items[0].id]["revoked_at"] is not None
        quota = agent_key_database.agent_api_key_quotas.documents[mock_user.id]
        assert quota["key_ids"] == [replacement.key.id]
        assert quota["pending"] == {}
    finally:
        if not creator.done():
            creator.cancel()
            with pytest.raises(asyncio.CancelledError):
                await creator
