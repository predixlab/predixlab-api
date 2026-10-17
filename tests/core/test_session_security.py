import base64
import json
from pathlib import Path
from secrets import token_hex

import pytest
from itsdangerous import TimestampSigner
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.config import Settings, settings


@pytest.fixture(autouse=True)
def isolate_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)


def test_session_secret_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError, match="session_secret_key"):
        Settings()


@pytest.mark.parametrize("value", ["", "short", "x" * 31, " " * 32, " " + "x" * 30 + " "])
def test_session_secret_key_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("SESSION_SECRET_KEY", value)

    with pytest.raises(ValidationError, match="session_secret_key"):
        Settings()


@pytest.mark.parametrize("length", [32, 64])
def test_session_secret_key_reads_environment(
    monkeypatch: pytest.MonkeyPatch, length: int
) -> None:
    value = token_hex(length // 2)
    monkeypatch.setenv("SESSION_SECRET_KEY", value)

    configured = Settings()

    assert configured.session_secret_key.get_secret_value() == value
    assert value not in repr(configured)
    assert value not in configured.model_dump_json()


def test_session_secret_key_reads_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    value = token_hex(32)
    env_file = tmp_path / ".env"
    env_file.write_text(f"SESSION_SECRET_KEY={value}\n")

    assert Settings().session_secret_key.get_secret_value() == value


def test_invalid_settings_do_not_print_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt_key = token_hex(32)
    invalid_session_key = token_hex(8)
    monkeypatch.setenv("SECRET_KEY", jwt_key)
    monkeypatch.setenv("SESSION_SECRET_KEY", invalid_session_key)

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert jwt_key not in str(exc_info.value)
    assert invalid_session_key not in str(exc_info.value)


async def oauth_session(request: Request) -> JSONResponse:
    if request.method == "POST":
        request.session["state"] = "expected-oauth-state"
    return JSONResponse(dict(request.session))


@pytest.fixture
def session_app() -> Starlette:
    from app.main import app

    middleware = [
        item for item in app.user_middleware if item.cls is SessionMiddleware
    ]
    assert len(middleware) == 1
    assert middleware[0].kwargs["secret_key"] == settings.session_secret_key.get_secret_value()
    return Starlette(
        routes=[Route("/", oauth_session, methods=["GET", "POST"])],
        middleware=middleware,
    )


def test_configured_session_cookie_survives_a_new_worker(session_app: Starlette) -> None:
    with TestClient(session_app) as client:
        response = client.post("/")
        assert response.json() == {"state": "expected-oauth-state"}
        cookie = response.cookies["session"]

    another_worker = Starlette(
        routes=session_app.routes, middleware=session_app.user_middleware
    )
    with TestClient(another_worker) as client:
        client.cookies.set("session", cookie)
        assert client.get("/").json() == {"state": "expected-oauth-state"}


def test_session_cookie_signed_with_a_different_key_is_rejected(
    session_app: Starlette,
) -> None:
    payload = base64.b64encode(json.dumps({"state": "forged-state"}).encode())
    cookie = TimestampSigner("retired-insecure-session-key").sign(payload).decode()

    with TestClient(session_app) as client:
        client.cookies.set("session", cookie)
        assert client.get("/").json() == {}


def test_session_key_rotation_invalidates_old_cookies(session_app: Starlette) -> None:
    with TestClient(session_app) as client:
        cookie = client.post("/").cookies["session"]

    rotated_app = Starlette(
        routes=session_app.routes,
        middleware=[Middleware(SessionMiddleware, secret_key=token_hex(32))],
    )
    with TestClient(rotated_app) as client:
        client.cookies.set("session", cookie)
        assert client.get("/").json() == {}
