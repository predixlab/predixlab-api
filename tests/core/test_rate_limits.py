"""Shared MongoDB rate-limit and cooldown behavior."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.config import Settings, settings
from app.core import rate_limits


@pytest.fixture
def limiter(mongo_auth_db, monkeypatch):
    defaults = {
        "otp_request_email_limit": 5,
        "otp_request_ip_limit": 20,
        "otp_request_window_seconds": 3600,
        "otp_verify_email_limit": 10,
        "otp_verify_ip_limit": 30,
        "otp_verify_window_seconds": 900,
        "otp_resend_cooldown_seconds": 60,
    }
    for key, value in defaults.items():
        monkeypatch.setattr(settings, key, value)
    with (
        patch.object(rate_limits, "get_database", AsyncMock(return_value=mongo_auth_db)),
        patch.object(rate_limits, "datetime", wraps=datetime) as clock,
    ):
        clock.now.return_value = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
        yield clock


@pytest.mark.parametrize(
    "action,dimension,limit,window",
    [
        ("request", "email", 5, 3600),
        ("request", "ip", 20, 3600),
        ("verify", "email", 10, 900),
        ("verify", "ip", 30, 900),
    ],
)
async def test_default_limits_exact_thresholds(limiter, action, dimension, limit, window):
    for index in range(limit):
        email = "test@example.com" if dimension == "email" else f"user{index}@example.com"
        ip = f"192.0.2.{index + 1}" if dimension == "email" else "192.0.2.1"
        await rate_limits.enforce_code_rate_limit(email, ip, action=action)
        if action == "request":
            limiter.now.return_value += timedelta(seconds=60)

    with pytest.raises(HTTPException) as error:
        await rate_limits.enforce_code_rate_limit(
            "test@example.com", "192.0.2.1", action=action
        )

    assert error.value.status_code == 429
    assert error.value.detail == "Too many requests. Please try again later."
    assert 1 <= int(error.value.headers["Retry-After"]) <= window


async def test_resend_cooldown_is_rolling_not_a_fixed_minute(limiter):
    limiter.now.return_value += timedelta(seconds=59)
    await rate_limits.enforce_code_rate_limit(
        "test@example.com", "192.0.2.1", action="request"
    )
    limiter.now.return_value += timedelta(seconds=1)
    with pytest.raises(HTTPException) as error:
        await rate_limits.enforce_code_rate_limit(
            "TEST@example.com", "192.0.2.2", action="request"
        )
    assert error.value.status_code == 429
    assert error.value.headers == {"Retry-After": "59"}

    limiter.now.return_value += timedelta(seconds=59)
    await rate_limits.enforce_code_rate_limit(
        "test@example.com", "192.0.2.1", action="request"
    )


async def test_cooldown_is_configurable(limiter, monkeypatch):
    monkeypatch.setattr(settings, "otp_resend_cooldown_seconds", 120)
    await rate_limits.enforce_code_rate_limit(
        "test@example.com", "192.0.2.1", action="request"
    )
    limiter.now.return_value += timedelta(seconds=60)
    with pytest.raises(HTTPException) as error:
        await rate_limits.enforce_code_rate_limit(
            "test@example.com", "192.0.2.1", action="request"
        )
    assert error.value.headers == {"Retry-After": "60"}


async def test_fixed_window_resets_without_ttl_cleanup(limiter, monkeypatch):
    monkeypatch.setattr(settings, "otp_verify_email_limit", 1)
    await rate_limits.enforce_code_rate_limit(
        "test@example.com", "192.0.2.1", action="verify"
    )
    limiter.now.return_value += timedelta(seconds=899)
    with pytest.raises(HTTPException) as error:
        await rate_limits.enforce_code_rate_limit(
            "test@example.com", "192.0.2.1", action="verify"
        )
    assert error.value.headers == {"Retry-After": "1"}
    limiter.now.return_value += timedelta(seconds=1)
    await rate_limits.enforce_code_rate_limit(
        "test@example.com", "192.0.2.1", action="verify"
    )


async def test_email_case_variants_share_limit(limiter, monkeypatch):
    monkeypatch.setattr(settings, "otp_verify_email_limit", 1)
    await rate_limits.enforce_code_rate_limit(
        "Test@example.com", "192.0.2.1", action="verify"
    )
    with pytest.raises(HTTPException) as error:
        await rate_limits.enforce_code_rate_limit(
            "test@example.com", "192.0.2.2", action="verify"
        )
    assert error.value.status_code == 429


async def test_email_rejections_still_count_against_ip(limiter, mongo_auth_db, monkeypatch):
    monkeypatch.setattr(settings, "otp_verify_email_limit", 1)
    monkeypatch.setattr(settings, "otp_verify_ip_limit", 2)
    await rate_limits.enforce_code_rate_limit(
        "test@example.com", "192.0.2.1", action="verify"
    )
    for email in ["test@example.com", "another@example.com"]:
        with pytest.raises(HTTPException) as error:
            await rate_limits.enforce_code_rate_limit(email, "192.0.2.1", action="verify")
        assert error.value.status_code == 429
    assert max(doc["count"] for doc in mongo_auth_db.rate_limits.documents.values()) == 3


async def test_counters_use_atomic_upsert_and_expiry(limiter, mongo_auth_db):
    await rate_limits.enforce_code_rate_limit(
        "test@example.com", "192.0.2.1", action="verify"
    )
    for call in mongo_auth_db.rate_limits.find_one_and_update.call_args_list:
        query, update = call.args
        assert set(query) == {"_id"}
        assert update == {
            "$inc": {"count": 1},
            "$setOnInsert": {"expires_at": limiter.now.return_value + timedelta(seconds=900)},
        }
        assert call.kwargs == {"upsert": True, "return_document": ReturnDocument.AFTER}


async def test_first_counter_upsert_race_is_retried(limiter, mongo_auth_db):
    update = mongo_auth_db.rate_limits.find_one_and_update.side_effect
    first = True

    async def racing_update(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            raise DuplicateKeyError("Concurrent counter insertion", code=11000)
        return await update(*args, **kwargs)

    mongo_auth_db.rate_limits.find_one_and_update.side_effect = racing_update
    await rate_limits.enforce_code_rate_limit(
        "test@example.com", "192.0.2.1", action="verify"
    )
    assert mongo_auth_db.rate_limits.find_one_and_update.await_count == 3


async def test_concurrent_replicas_share_counter(limiter, monkeypatch):
    monkeypatch.setattr(settings, "otp_verify_email_limit", 3)
    results = await asyncio.gather(*(
        rate_limits.enforce_code_rate_limit(
            "test@example.com", f"192.0.2.{index}", action="verify"
        )
        for index in range(10)
    ), return_exceptions=True)
    assert results.count(None) == 3
    assert sum(isinstance(result, HTTPException) and result.status_code == 429 for result in results) == 7


async def test_concurrent_resend_has_only_one_winner(limiter):
    results = await asyncio.gather(*(
        rate_limits.enforce_code_rate_limit(
            "test@example.com", f"192.0.2.{index}", action="request"
        )
        for index in range(4)
    ), return_exceptions=True)
    assert results.count(None) == 1
    errors = [result for result in results if isinstance(result, HTTPException)]
    assert len(errors) == 3
    assert all(error.status_code == 429 for error in errors)
    assert all(error.headers == {"Retry-After": "60"} for error in errors)


async def test_database_failure_is_not_a_successful_fallback(limiter, mongo_auth_db):
    mongo_auth_db.rate_limits.find_one_and_update.side_effect = OperationFailure("unavailable")
    with pytest.raises(OperationFailure):
        await rate_limits.enforce_code_rate_limit(
            "test@example.com", "192.0.2.1", action="verify"
        )


@pytest.mark.parametrize(
    "field",
    [
        "otp_request_email_limit", "otp_request_ip_limit", "otp_request_window_seconds",
        "otp_verify_email_limit", "otp_verify_ip_limit", "otp_verify_window_seconds",
        "otp_resend_cooldown_seconds",
    ],
)
def test_limits_must_be_positive(field):
    with pytest.raises(ValidationError):
        Settings(**{field: 0})


def test_proxy_hops_cannot_be_negative():
    with pytest.raises(ValidationError):
        Settings(trusted_proxy_hops=-1)
