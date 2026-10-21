from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.services.user_service import UserService


@pytest.mark.parametrize("seconds", [-1, 0, 1])
@pytest.mark.parametrize(
    "collection_name,method,lookup",
    [
        ("refresh_tokens", "get_refresh_token", "token"),
        ("oauth_tokens", "get_oauth_token", "token"),
        ("login_codes", "consume_login_code", "code"),
    ],
)
async def test_token_reads_require_future_expiry_without_ttl(
    mongo_auth_db, seconds, collection_name, method, lookup
):
    now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    collection = getattr(mongo_auth_db, collection_name)
    await collection.insert_one({
        "_id": "token-id",
        lookup: "token-value",
        "expires_at": now + timedelta(seconds=seconds),
        "user_id": "user-id",
        "is_revoked": False,
        "consumed": False,
        "provider": "google",
        "user_info": {
            "email": "test@example.com",
            "name": "Test",
            "provider": "google",
            "provider_id": "provider-id",
        },
    })
    service = UserService()
    service.db = mongo_auth_db
    with patch("app.services.user_service.datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        result = await getattr(service, method)("token-value")
    assert (result is not None) is (seconds > 0)
    assert collection.find_one.call_args.args[0]["expires_at"] == {"$gt": now}
    assert "token-id" in collection.documents


@pytest.mark.parametrize("seconds", [-1, 0])
async def test_verification_code_at_or_before_expiry_is_invalid(mongo_auth_db, seconds):
    now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    await mongo_auth_db.verification_codes.insert_one({
        "_id": "code-id",
        "email": "test@example.com",
        "code": "123456",
        "expires_at": now + timedelta(seconds=seconds),
        "failed_attempts": 0,
    })
    service = UserService()
    service.db = mongo_auth_db
    with patch("app.services.user_service.datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        assert not await service.verify_email_code("test@example.com", "123456")
    assert "code-id" in mongo_auth_db.verification_codes.documents
    mongo_auth_db.users.update_one.assert_not_awaited()
