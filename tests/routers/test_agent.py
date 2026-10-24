"""JWT-only management routes and scoped key lifecycle over HTTP."""

import httpx
import pytest
from fastapi import FastAPI

from app.config import settings
from app.core.security import create_access_token
from app.routers import agent


@pytest.fixture
async def agent_client(agent_key_database, mock_user):
    app = FastAPI()
    app.include_router(agent.router, prefix="/api/v1")
    token = create_access_token({"sub": mock_user.id})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


async def test_full_http_lifecycle_and_secret_cache_headers(agent_client):
    response = await agent_client.post(
        "/api/v1/agent/api-keys", json={"name": "VS Code", "expires_in_days": 30}
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    created = response.json()
    token = created["api_key"]
    key = created["key"]
    assert key["version"] == 1
    assert key["scope"] == "mcp:read"
    response = await agent_client.get("/api/v1/agent/api-keys")
    assert response.json()["total_count"] == 1
    assert token not in response.text
    assert "key_hash" not in response.text

    response = await agent_client.post(
        f"/api/v1/agent/api-keys/{key['id']}/rotate", json={"version": 1}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    rotated = response.json()
    assert rotated["key"]["version"] == 2
    assert rotated["api_key"] != token
    response = await agent_client.delete(
        f"/api/v1/agent/api-keys/{key['id']}", params={"version": 2}
    )
    assert response.status_code == 204
    assert response.content == b""
    response = await agent_client.get("/api/v1/agent/api-keys")
    assert response.json()["items"][0]["status"] == "revoked"
    assert response.json()["non_revoked_count"] == 0
    assert response.json()["max_keys"] == settings.mcp_api_keys_per_user


async def test_creation_cap_is_enforced_over_http(agent_client, monkeypatch):
    monkeypatch.setattr(settings, "mcp_api_keys_per_user", 1)
    created = await agent_client.post("/api/v1/agent/api-keys", json={"name": "First"})
    assert created.status_code == 201
    blocked = await agent_client.post("/api/v1/agent/api-keys", json={"name": "Second"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "mcp_key_limit"
    key = created.json()["key"]
    revoked = await agent_client.delete(
        f"/api/v1/agent/api-keys/{key['id']}", params={"version": 1}
    )
    assert revoked.status_code == 204
    replacement = await agent_client.post("/api/v1/agent/api-keys", json={"name": "Replacement"})
    assert replacement.status_code == 201


@pytest.mark.parametrize("kind", ["none", "api_key", "refresh"])
async def test_management_does_not_accept_mcp_credentials(
    agent_client, agent_tokens, mock_user, kind
):
    from app.core.security import create_refresh_token

    token = ""
    if kind == "api_key":
        token = agent_tokens[mock_user.id].api_key
    elif kind == "refresh":
        token = create_refresh_token({"sub": mock_user.id})
    response = await agent_client.post(
        "/api/v1/agent/api-keys",
        json={"name": "Untrusted"},
        headers={"Authorization": f"Bearer {token}" if token else ""},
    )
    assert response.status_code == 401


async def test_rotation_and_revocation_use_owner_and_version(
    agent_client, agent_tokens, mock_user, mock_premium_user
):
    other = agent_tokens[mock_premium_user.id]
    for method, suffix, kwargs in [
        ("POST", "/rotate", {"json": {"version": 1}}),
        ("DELETE", "", {"params": {"version": 1}}),
    ]:
        response = await agent_client.request(
            method, f"/api/v1/agent/api-keys/{other.key.id}{suffix}", **kwargs
        )
        assert response.status_code == 404
    owned = agent_tokens[mock_user.id]
    response = await agent_client.post(
        f"/api/v1/agent/api-keys/{owned.key.id}/rotate", json={"version": 1}
    )
    assert response.status_code == 200
    response = await agent_client.delete(
        f"/api/v1/agent/api-keys/{owned.key.id}", params={"version": 1}
    )
    assert response.status_code == 409
    assert agent_tokens[mock_premium_user.id].api_key not in response.text


async def test_revoke_is_available_when_issue_quota_is_exhausted(
    agent_client, monkeypatch
):
    monkeypatch.setattr(settings, "mcp_key_issues_per_hour", 1)
    first = await agent_client.post("/api/v1/agent/api-keys", json={"name": "Agent"})
    assert first.status_code == 201
    key = first.json()["key"]
    limited = await agent_client.post(
        f"/api/v1/agent/api-keys/{key['id']}/rotate", json={"version": 1}
    )
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0
    revoke = await agent_client.delete(
        f"/api/v1/agent/api-keys/{key['id']}", params={"version": 1}
    )
    assert revoke.status_code == 204


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api-keys", {"name": " "}),
        ("/api-keys", {"name": "a" * 65}),
        ("/api-keys", {"name": "Agent", "expires_in_days": 999}),
        ("/api-keys", {"name": "Agent", "scope": "admin"}),
        ("/api-keys", {"name": "Agent", "user_id": "other-account"}),
        (f"/api-keys/{'a' * 32}/rotate", {"version": 0}),
        (f"/api-keys/{'a' * 32}/rotate", {"version": True}),
        (f"/api-keys/{'a' * 32}/rotate", {"version": 1, "scope": "admin"}),
        ("/api-keys/not-an-id/rotate", {"version": 1}),
    ],
)
async def test_invalid_inputs_are_rejected(agent_client, path, body):
    response = await agent_client.post(f"/api/v1/agent{path}", json=body)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "query", [{"limit": 0}, {"limit": 101}, {"offset": -1}]
)
async def test_list_pagination_bounds(agent_client, query):
    response = await agent_client.get("/api/v1/agent/api-keys", params=query)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://api.predixlab.com", 200),
        ("http://localhost:8000/", 200),
        ("http://127.0.0.1:8000", 200),
        ("http://[::1]:8000", 200),
        ("http://api.predixlab.com", 503),
        ("https://user:password@api.predixlab.com", 503),
        ("https://api.predixlab.com?api_key=bad", 503),
        ("https://api.predixlab.com/#secret", 503),
        ("https://api.predixlab.com/path", 503),
        ("javascript:alert(1)", 503),
        ("https://api.predixlab.com:invalid", 503),
        ("https://api.predixlab.com:65536", 503),
        ("https://api.predixlab.com:0", 503),
        ("https://[invalid", 503),
    ],
)
async def test_connection_configuration_never_embeds_credentials(
    agent_client, monkeypatch, url, expected
):
    monkeypatch.setattr(settings, "public_api_url", url)
    response = await agent_client.get("/api/v1/agent/connection")
    assert response.status_code == expected
    if expected == 200:
        body = response.json()
        assert body["mcp_url"].endswith("/mcp/")
        assert body["scope"] == "mcp:read"
        assert body["expires_in_days_options"] == [30, 90, 365]
        assert "api_key" not in response.text


@pytest.mark.parametrize("public_url", [
    "http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000",
    "https://localhost.", "https://127.0.0.2", "https://service.localhost",
])
async def test_remote_connection_never_advertises_loopback(agent_client, monkeypatch, public_url):
    monkeypatch.setattr(settings, "public_api_url", public_url)
    response = await agent_client.get(
        "https://api.predixlab.com/api/v1/agent/connection",
        headers={"X-Forwarded-Host": "localhost:8000"},
    )
    assert response.status_code == 503
    assert "PUBLIC_API_URL" in response.json()["detail"]


async def test_production_connection_keeps_https_behind_ingress(agent_client, monkeypatch):
    monkeypatch.setattr(settings, "public_api_url", "https://api.predixlab.com")
    response = await agent_client.get("http://api.predixlab.com/api/v1/agent/connection")
    assert response.status_code == 200
    assert response.json()["mcp_url"] == "https://api.predixlab.com/mcp/"
