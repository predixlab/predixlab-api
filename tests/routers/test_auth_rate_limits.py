"""HTTP coverage for shared limits on all email-code authentication routes."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from pymongo.errors import OperationFailure

from app.config import settings
from app.core import rate_limits
from app.routers import auth


@pytest.fixture
async def limited_auth(mongo_auth_db, monkeypatch):
    for name, value in {
        "otp_request_email_limit": 5,
        "otp_request_ip_limit": 20,
        "otp_request_window_seconds": 3600,
        "otp_verify_email_limit": 10,
        "otp_verify_ip_limit": 30,
        "otp_verify_window_seconds": 900,
        "otp_resend_cooldown_seconds": 60,
        "trusted_proxy_hops": 1,
        "trust_x_real_ip": False,
    }.items():
        monkeypatch.setattr(settings, name, value)

    app = FastAPI()
    app.include_router(auth.router)
    with (
        patch.object(rate_limits, "get_database", AsyncMock(return_value=mongo_auth_db)),
        patch.object(rate_limits, "datetime", wraps=datetime) as clock,
        patch.object(auth.user_service, "db", mongo_auth_db),
        patch.object(auth.user_service, "get_user_by_email", AsyncMock(return_value=None)) as lookup,
        patch.object(auth.user_service, "send_verification_code", AsyncMock(return_value=True)) as send,
    ):
        clock.now.return_value = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, clock, lookup, send


def payload(email):
    return {
        "email": email,
        "code": "000000",
        "user_notice_version": settings.user_notice_version,
    }


@pytest.mark.parametrize(
    "path,action,allowed_status",
    [
        ("/auth/login/request-code", "request", 200),
        ("/auth/register/request-code", "request", 200),
        ("/auth/login", "verify", 400),
        ("/auth/register", "verify", 400),
    ],
)
@pytest.mark.parametrize("dimension", ["email", "ip"])
async def test_every_endpoint_enforces_both_limits(
    limited_auth, monkeypatch, path, action, allowed_status, dimension
):
    client, clock, _, _ = limited_auth
    monkeypatch.setattr(settings, f"otp_{action}_{dimension}_limit", 2)
    for index in range(3):
        email = "test@example.com" if dimension == "email" else f"user{index}@example.com"
        ip = f"192.0.2.{index + 1}" if dimension == "email" else "192.0.2.1"
        response = await client.post(
            path,
            json=payload(email),
            headers={"X-Forwarded-For": f"198.51.100.{index + 1}, {ip}"},
        )
        if index < 2:
            assert response.status_code == allowed_status
            clock.now.return_value += timedelta(seconds=60)
        else:
            assert response.status_code == 429
            assert response.json() == {"detail": "Too many requests. Please try again later."}
            assert int(response.headers["Retry-After"]) > 0


@pytest.mark.parametrize(
    "paths,action",
    [
        (["/auth/login/request-code", "/auth/register/request-code"], "request"),
        (["/auth/login", "/auth/register"], "verify"),
    ],
)
async def test_login_and_registration_share_email_limits(
    limited_auth, monkeypatch, paths, action
):
    client, clock, _, _ = limited_auth
    monkeypatch.setattr(settings, f"otp_{action}_email_limit", 1)
    first = await client.post(paths[0], json=payload("test@example.com"))
    assert first.status_code in (200, 400)
    clock.now.return_value += timedelta(seconds=60)
    second = await client.post(paths[1], json=payload("test@example.com"))
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0


async def test_cooldown_applies_to_both_request_routes_before_account_lookup(limited_auth):
    client, clock, lookup, send = limited_auth
    first = await client.post("/auth/register/request-code", json=payload("test@example.com"))
    assert first.status_code == 200
    clock.now.return_value += timedelta(seconds=10)
    second = await client.post("/auth/login/request-code", json=payload("test@example.com"))
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "50"
    lookup.assert_awaited_once()
    send.assert_awaited_once()


@pytest.mark.parametrize("path", ["/auth/login/request-code", "/auth/register/request-code"])
async def test_request_response_does_not_disclose_account_presence(limited_auth, mock_user, path):
    client, clock, lookup, _ = limited_auth
    absent = await client.post(path, json=payload("test@example.com"))
    lookup.return_value = mock_user
    clock.now.return_value += timedelta(seconds=60)
    present = await client.post(path, json=payload("test@example.com"))
    assert absent.status_code == present.status_code == 200
    assert absent.json() == present.json() == {
        "message": "Verification code sent to email",
        "email": "test@example.com",
    }


async def test_database_failure_returns_generic_error_not_bypass(limited_auth, mongo_auth_db):
    client, _, lookup, send = limited_auth
    mongo_auth_db.rate_limits.find_one_and_update.side_effect = OperationFailure("unavailable")
    response = await client.post("/auth/login/request-code", json=payload("test@example.com"))
    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to send verification code"}
    lookup.assert_not_awaited()
    send.assert_not_awaited()


async def test_registration_success_shape_is_preserved(limited_auth, mock_user):
    client, _, _, _ = limited_auth
    with (
        patch.object(auth.user_service, "register_user", AsyncMock(return_value=mock_user)),
        patch.object(auth.user_service, "update_last_login", AsyncMock()),
        patch.object(
            auth.user_service,
            "create_refresh_token",
            AsyncMock(return_value=MagicMock(token="refresh-token")),
        ),
    ):
        response = await client.post("/auth/register", json=payload("test@example.com"))
    assert response.status_code == 200
    assert set(response.json()) == {
        "access_token", "refresh_token", "token_type", "expires_in", "user",
    }
    assert response.json()["refresh_token"] == "refresh-token"
    assert response.json()["user"]["email"] == "test@example.com"
