from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest
from itsdangerous import URLSafeSerializer, URLSafeTimedSerializer

from app.config import Settings, settings
from app.core.email import (
    DAILY_SIGNALS_UNSUBSCRIBE_SALT,
    MARKETING_UNSUBSCRIBE_SALT,
    UNSUBSCRIBE_MAX_AGE,
    EmailService,
)
from app.models.user import Language


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(settings, "email_backend", "console")
    monkeypatch.setattr(settings, "secret_key", "test-jwt-key")
    monkeypatch.setattr(settings, "unsubscribe_secret", "test-unsubscribe-key")
    return EmailService()


@pytest.fixture(params=[
    (MARKETING_UNSUBSCRIBE_SALT, "parse_unsubscribe_token"),
    (DAILY_SIGNALS_UNSUBSCRIBE_SALT, "parse_daily_signals_unsubscribe_token"),
])
def token_scope(request, service):
    salt, parser = request.param
    return salt, getattr(service, parser)


@pytest.mark.parametrize("secret", ["test-jwt-key", "test-unsubscribe-key"])
def test_timed_tokens_accept_both_keys(token_scope, secret):
    salt, parse = token_scope
    token = URLSafeTimedSerializer(secret, salt=salt).dumps(
        {"user_id": "user-123", "language": "zh"}
    )
    assert parse(token) == ("user-123", Language.CHINESE)


def test_marketing_signs_with_dedicated_key(service):
    url = service.create_unsubscribe_url("user-123", Language.ENGLISH)
    token = parse_qs(urlparse(url).query)["token"][0]
    assert URLSafeTimedSerializer(
        settings.unsubscribe_secret, salt=MARKETING_UNSUBSCRIBE_SALT
    ).loads(token, max_age=UNSUBSCRIBE_MAX_AGE) == {
        "user_id": "user-123", "language": "en"
    }


def test_legacy_untimed_secret_key_token(token_scope):
    salt, parse = token_scope
    token = URLSafeSerializer(settings.secret_key, salt=salt).dumps(
        {"user_id": "user-123", "language": "en"}
    )
    assert parse(token) == ("user-123", Language.ENGLISH)


@pytest.mark.parametrize("secret", ["test-jwt-key", "test-unsubscribe-key"])
def test_expired_timed_token_never_uses_legacy_fallback(token_scope, secret):
    salt, parse = token_scope
    with patch("itsdangerous.timed.TimestampSigner.get_timestamp", return_value=1):
        token = URLSafeTimedSerializer(secret, salt=salt).dumps(
            {"user_id": "user-123", "language": "en"}
        )
    with patch("app.core.email.URLSafeSerializer") as legacy:
        with pytest.raises(ValueError, match="unsubscribe link"):
            parse(token)
        legacy.assert_not_called()


@pytest.mark.parametrize("age,valid", [
    (UNSUBSCRIBE_MAX_AGE, True),
    (UNSUBSCRIBE_MAX_AGE + 1, False),
])
def test_exact_365_day_expiry_boundary(token_scope, age, valid):
    salt, parse = token_scope
    with patch("itsdangerous.timed.TimestampSigner.get_timestamp", return_value=1000):
        token = URLSafeTimedSerializer(settings.unsubscribe_secret, salt=salt).dumps(
            {"user_id": "user-123", "language": "en"}
        )
    with patch(
        "itsdangerous.timed.TimestampSigner.get_timestamp", return_value=1000 + age
    ):
        if valid:
            assert parse(token) == ("user-123", Language.ENGLISH)
        else:
            with pytest.raises(ValueError, match="unsubscribe link"):
                parse(token)


@pytest.mark.parametrize("serializer", [URLSafeSerializer, URLSafeTimedSerializer])
def test_wrong_key_rejected(token_scope, serializer):
    salt, parse = token_scope
    token = serializer("wrong-key", salt=salt).dumps(
        {"user_id": "user-123", "language": "en"}
    )
    with pytest.raises(ValueError, match="unsubscribe link"):
        parse(token)


def test_unset_secret_falls_back_to_secret_key(service, monkeypatch):
    monkeypatch.delenv("UNSUBSCRIBE_SECRET", raising=False)
    config = Settings(_env_file=None)
    assert config.unsubscribe_secret == ""
    monkeypatch.setattr(settings, "unsubscribe_secret", config.unsubscribe_secret)
    url = EmailService().create_unsubscribe_url("user-123", Language.ENGLISH)
    token = parse_qs(urlparse(url).query)["token"][0]
    assert URLSafeTimedSerializer(
        settings.secret_key, salt=MARKETING_UNSUBSCRIBE_SALT
    ).loads(token, max_age=UNSUBSCRIBE_MAX_AGE)["user_id"] == "user-123"
    assert service.parse_unsubscribe_token(token) == ("user-123", Language.ENGLISH)


def test_campaign_shared_spec_token(service):
    serializer = URLSafeTimedSerializer(
        settings.unsubscribe_secret or settings.secret_key,
        salt="predixlab-daily-signals-email-unsubscribe",
    )
    token = serializer.dumps({"user_id": "campaign-user", "language": "en"})
    assert service.parse_daily_signals_unsubscribe_token(token) == (
        "campaign-user", Language.ENGLISH
    )


def test_duplicate_signing_keys_are_accepted(service, monkeypatch):
    monkeypatch.setattr(settings, "unsubscribe_secret", settings.secret_key)
    token = URLSafeTimedSerializer(
        settings.secret_key, salt=MARKETING_UNSUBSCRIBE_SALT
    ).dumps({"user_id": "user-123", "language": "en"})
    with patch(
        "app.core.email.URLSafeTimedSerializer", wraps=URLSafeTimedSerializer
    ) as serializer:
        assert service.parse_unsubscribe_token(token) == ("user-123", Language.ENGLISH)
    assert serializer.call_args.args[0] == [settings.secret_key]


def test_dedicated_key_reads_environment(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "test-dedicated-key")
    assert Settings(_env_file=None).unsubscribe_secret == "test-dedicated-key"


@pytest.mark.parametrize("payload", [{}, {"user_id": "user", "language": "invalid"}, []])
def test_invalid_payload_rejected(token_scope, payload):
    salt, parse = token_scope
    token = URLSafeTimedSerializer(settings.unsubscribe_secret, salt=salt).dumps(payload)
    with pytest.raises(ValueError, match="unsubscribe link"):
        parse(token)


def test_token_scopes_are_not_interchangeable(service):
    token = service.unsubscribe_serializer.dumps({"user_id": "user", "language": "en"})
    with pytest.raises(ValueError, match="Daily Signals"):
        service.parse_daily_signals_unsubscribe_token(token)
