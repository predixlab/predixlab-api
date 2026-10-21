"""Regression tests for verification-code attempts and atomic consumption."""

import asyncio
import hmac
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.user_service import UserService


@pytest.fixture
async def code_service(mongo_auth_db):
    service = UserService()
    service.db = mongo_auth_db
    await mongo_auth_db.verification_codes.insert_one({
        "_id": "code-id",
        "email": "test@example.com",
        "code": "123456",
        "failed_attempts": 0,
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
    })
    return service


async def test_five_wrong_attempts_invalidate_even_the_correct_code(code_service):
    for expected_attempts in range(1, 6):
        assert not await code_service.verify_email_code("test@example.com", "000000")
        document = code_service.db.verification_codes.documents["code-id"]
        assert document["failed_attempts"] == expected_attempts

    assert not await code_service.verify_email_code("test@example.com", "123456")
    assert not await code_service.verify_email_code("test@example.com", "000000")
    code_service.db.users.update_one.assert_not_awaited()


async def test_correct_code_is_single_use(code_service):
    assert await code_service.verify_email_code("test@example.com", "123456")
    assert not await code_service.verify_email_code("test@example.com", "123456")
    code_service.db.users.update_one.assert_awaited_once_with(
        {"email": "test@example.com"}, {"$set": {"email_verified": True}}
    )


@pytest.mark.parametrize("supplied", ["123456", "000000", "\u2603"])
async def test_constant_time_compare_and_email_lookup(code_service, supplied):
    with patch(
        "app.services.user_service.hmac.compare_digest", wraps=hmac.compare_digest
    ) as compare:
        result = await code_service.verify_email_code("test@example.com", supplied)

    assert result is (supplied == "123456")
    compare.assert_called_once_with(supplied.encode("utf-8"), b"123456")
    query = code_service.db.verification_codes.find_one.call_args.args[0]
    assert query["email"] == "test@example.com"
    assert "code" not in query
    if supplied != "123456":
        update = code_service.db.verification_codes.update_one.call_args.args
        assert update[1] == {"$inc": {"failed_attempts": 1}}


async def test_expired_code_is_rejected_before_ttl_cleanup(code_service):
    code_service.db.verification_codes.documents["code-id"]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    assert not await code_service.verify_email_code("test@example.com", "123456")
    code_service.db.users.update_one.assert_not_awaited()


async def test_legacy_code_without_failure_counter_is_bounded(code_service):
    del code_service.db.verification_codes.documents["code-id"]["failed_attempts"]
    for _ in range(5):
        assert not await code_service.verify_email_code("test@example.com", "000000")
    assert not await code_service.verify_email_code("test@example.com", "123456")


async def test_concurrent_correct_code_claim_has_only_one_winner(code_service):
    collection = code_service.db.verification_codes
    find_one = collection.find_one.side_effect
    both_read = asyncio.Event()
    readers = 0

    async def simultaneous_read(query):
        nonlocal readers
        document = await find_one(query)
        readers += 1
        if readers == 2:
            both_read.set()
        await both_read.wait()
        return document

    collection.find_one.side_effect = simultaneous_read
    results = await asyncio.gather(
        code_service.verify_email_code("test@example.com", "123456"),
        code_service.verify_email_code("test@example.com", "123456"),
    )
    assert sorted(results) == [False, True]
    code_service.db.users.update_one.assert_awaited_once()


async def test_stale_read_cannot_claim_after_fifth_failure(code_service):
    collection = code_service.db.verification_codes
    collection.documents["code-id"]["failed_attempts"] = 4
    find_one = collection.find_one.side_effect

    async def final_failure_after_read(query):
        document = await find_one(query)
        collection.documents["code-id"]["failed_attempts"] = 5
        return document

    collection.find_one.side_effect = final_failure_after_read
    assert not await code_service.verify_email_code("test@example.com", "123456")
    code_service.db.users.update_one.assert_not_awaited()


async def test_concurrent_failures_stop_at_five(code_service):
    collection = code_service.db.verification_codes
    find_one = collection.find_one.side_effect
    all_read = asyncio.Event()
    readers = 0

    async def simultaneous_read(query):
        nonlocal readers
        document = await find_one(query)
        readers += 1
        if readers == 8:
            all_read.set()
        await all_read.wait()
        return document

    collection.find_one.side_effect = simultaneous_read
    results = await asyncio.gather(*(
        code_service.verify_email_code("test@example.com", "000000")
        for _ in range(8)
    ))
    assert results == [False] * 8
    assert collection.documents["code-id"]["failed_attempts"] == 5


async def test_resend_replaces_old_code_and_resets_attempts(code_service):
    for _ in range(5):
        assert not await code_service.verify_email_code("test@example.com", "000000")

    with (
        patch("app.services.user_service.generate_verification_code", return_value="654321"),
        patch(
            "app.services.user_service.email_service.send_verification_code",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        assert await code_service.send_verification_code("test@example.com")

    documents = list(code_service.db.verification_codes.documents.values())
    assert len(documents) == 1
    assert documents[0]["failed_attempts"] == 0
    assert not await code_service.verify_email_code("test@example.com", "123456")
    assert await code_service.verify_email_code("test@example.com", "654321")
