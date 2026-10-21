"""JWT wire-format and claim-validation compatibility with python-jose."""

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.config import settings
from app.core.security import (
    create_access_token,
    create_email_verification_token,
    create_refresh_token,
    verify_email_verification_token,
    verify_refresh_token,
    verify_token,
)

TokenCase = tuple[Callable[[str], dict | str | None], str, str | None]


def legacy_hs256_token(payload: dict) -> str:
    """Sign the compact JSON/base64url format used by python-jose, independently."""
    def encode(value: bytes) -> bytes:
        return base64.urlsafe_b64encode(value).rstrip(b"=")

    header = encode(b'{"alg":"HS256","typ":"JWT"}')
    body = encode(json.dumps(payload, separators=(",", ":")).encode())
    message = header + b"." + body
    signature = hmac.new(
        settings.secret_key.encode(), message, hashlib.sha256
    ).digest()
    return (message + b"." + encode(signature)).decode()


@pytest.fixture(
    params=[
        (verify_token, "access", "Could not validate credentials"),
        (verify_refresh_token, "refresh", "Could not validate refresh token"),
        (verify_email_verification_token, "email_verification", None),
    ],
    ids=["access", "refresh", "email-verification"],
)
def token_case(request: pytest.FixtureRequest) -> TokenCase:
    return request.param


def assert_rejected(token: str, token_case: TokenCase) -> None:
    verifier, _, error_detail = token_case
    if error_detail is None:
        assert verifier(token) is None
    else:
        with pytest.raises(HTTPException) as exc_info:
            verifier(token)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == error_detail
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


def test_decodes_independent_legacy_hs256_token(token_case: TokenCase) -> None:
    verifier, purpose, _ = token_case
    payload = {
        "sub": "legacy@example.com",
        "email": "legacy@example.com",
        "exp": 4102444800,
        "type": purpose,
        "custom": "unchanged",
    }

    decoded = verifier(legacy_hs256_token(payload))

    assert decoded == (payload["sub"] if purpose == "email_verification" else payload)


def test_rejects_expired_legacy_token(token_case: TokenCase) -> None:
    _, purpose, _ = token_case
    token = legacy_hs256_token(
        {"sub": "legacy@example.com", "exp": 946684800, "type": purpose}
    )

    assert_rejected(token, token_case)


@pytest.mark.parametrize("tamper", ["payload", "signature"])
def test_rejects_tampered_legacy_token(token_case: TokenCase, tamper: str) -> None:
    _, purpose, _ = token_case
    payload = {"sub": "legacy@example.com", "exp": 4102444800, "type": purpose}
    header, body, signature = legacy_hs256_token(payload).split(".")
    if tamper == "payload":
        payload["sub"] = "another@example.com"
        body = legacy_hs256_token(payload).split(".")[1]
    else:
        signature = ("A" if signature[0] != "A" else "B") + signature[1:]

    assert_rejected(".".join((header, body, signature)), token_case)


def test_accepts_future_issued_at_like_python_jose(token_case: TokenCase) -> None:
    verifier, purpose, _ = token_case
    payload = {
        "sub": "legacy@example.com",
        "exp": 4102444800,
        "iat": 4102444700,
        "type": purpose,
    }

    assert verifier(legacy_hs256_token(payload)) is not None


def test_accepts_numeric_date_strings_like_python_jose(token_case: TokenCase) -> None:
    verifier, purpose, _ = token_case
    payload = {
        "sub": "legacy@example.com",
        "exp": "4102444800",
        "iat": "946684800",
        "nbf": "946684800",
        "type": purpose,
    }

    assert verifier(legacy_hs256_token(payload)) is not None


def test_expiry_remains_optional_like_python_jose(token_case: TokenCase) -> None:
    verifier, purpose, _ = token_case
    payload = {"sub": "legacy@example.com", "type": purpose}

    assert verifier(legacy_hs256_token(payload)) is not None


@pytest.mark.parametrize("claim", ["exp", "nbf"])
@pytest.mark.parametrize("seconds", [-1, 0, 1])
@pytest.mark.parametrize("microsecond", [0, 999999])
def test_preserves_time_claim_boundaries(
    token_case: TokenCase, claim: str, seconds: int, microsecond: int
) -> None:
    verifier, purpose, _ = token_case
    now = datetime(2026, 1, 1, 12, 0, 0, microsecond, tzinfo=timezone.utc)
    payload = {
        "sub": "legacy@example.com",
        "exp": 4102444800,
        "type": purpose,
        claim: int(now.timestamp()) + seconds,
    }
    token = legacy_hs256_token(payload)
    with (
        patch("app.core.security.datetime") as clock,
        patch("jwt.api_jwt.datetime") as jwt_clock,
    ):
        clock.now.return_value = now
        jwt_clock.now.return_value = now
        valid = seconds >= 0 if claim == "exp" else seconds <= 0
        if valid:
            assert verifier(token) is not None
        else:
            assert_rejected(token, token_case)


@pytest.mark.parametrize(
    "claims",
    [
        {"iat": "not-a-number"},
        {"nbf": 4102444800},
        {"nbf": "not-a-number"},
        {"exp": "not-a-number"},
        {"aud": "other-service"},
        {"aud": []},
        {"aud": None},
        {"sub": 123},
        {"jti": 123},
        {"at_hash": "unverifiable-without-access-token"},
    ],
)
def test_rejects_invalid_legacy_claims(token_case: TokenCase, claims: dict) -> None:
    _, purpose, _ = token_case
    payload = {
        "sub": "legacy@example.com",
        "exp": 4102444800,
        "type": purpose,
        **claims,
    }

    assert_rejected(legacy_hs256_token(payload), token_case)


@pytest.mark.parametrize("algorithm", ["none", "HS384"])
def test_rejects_unconfigured_algorithm(
    token_case: TokenCase, algorithm: str
) -> None:
    _, purpose, _ = token_case
    token = legacy_hs256_token(
        {"sub": "legacy@example.com", "exp": 4102444800, "type": purpose}
    )
    _, body, signature = token.split(".")
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": algorithm, "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()

    assert_rejected(".".join((header, body, signature)), token_case)


@pytest.mark.parametrize(
    ("creator", "purpose", "lifetime"),
    [
        (create_access_token, "access", timedelta(minutes=15)),
        (create_access_token, "access", None),
        (create_access_token, "access", timedelta(0)),
        (create_refresh_token, "refresh", timedelta(days=2)),
        (create_refresh_token, "refresh", None),
        (create_refresh_token, "refresh", timedelta(0)),
    ],
)
def test_issued_tokens_preserve_claims_and_expiry(
    creator: Callable[..., str], purpose: str, lifetime: timedelta | None
) -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
    data = {"sub": "legacy@example.com", "custom": "unchanged"}
    default_lifetime = (
        timedelta(minutes=settings.access_token_expire_minutes)
        if purpose == "access"
        else timedelta(days=settings.refresh_token_expire_days)
    )
    with patch("app.core.security.datetime") as clock:
        clock.now.return_value = now
        token = creator(data, expires_delta=lifetime)

    expected = {
        **data,
        "exp": int((now + (lifetime or default_lifetime)).timestamp()),
        "type": purpose,
    }
    assert token == legacy_hs256_token(expected)
    assert data == {"sub": "legacy@example.com", "custom": "unchanged"}


def test_email_verification_tokens_preserve_claims_and_expiry() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
    with patch("app.core.security.datetime") as clock:
        clock.now.return_value = now
        token = create_email_verification_token("legacy@example.com")

    assert token == legacy_hs256_token(
        {
            "sub": "legacy@example.com",
            "type": "email_verification",
            "exp": int((now + timedelta(hours=24)).timestamp()),
        }
    )
