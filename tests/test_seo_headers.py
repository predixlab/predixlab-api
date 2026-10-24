from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.main import X_ROBOTS_TAG, api_docs_kwargs, app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Without a context manager, TestClient skips database/service startup.
    test_client = TestClient(
        app, base_url="http://localhost", raise_server_exceptions=False
    )
    yield test_client
    test_client.close()


@pytest.mark.parametrize(
    ("path", "status_code"),
    [
        ("/health", 200),
        ("/robots.txt", 200),
        ("/__missing_seo_test_path", 404),
        ("/api/v1/user/profile", 401),
        ("/mcp/", 401),
        ("/api/v1/user/email/unsubscribe", 422),
    ],
)
def test_robots_header_on_responses(
    client: TestClient, path: str, status_code: int
) -> None:
    response = client.get(path)

    assert response.status_code == status_code
    assert response.headers["X-Robots-Tag"] == X_ROBOTS_TAG == "noindex, nofollow"


def test_robots_txt(client: TestClient) -> None:
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.text == "User-agent: *\nDisallow: /\n"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    route = next(
        route for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/robots.txt"
    )
    assert route.include_in_schema is False


def test_robots_txt_head(client: TestClient) -> None:
    response = client.head("/robots.txt")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["X-Robots-Tag"] == X_ROBOTS_TAG


def test_robots_header_on_cors_preflight(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["X-Robots-Tag"] == X_ROBOTS_TAG


@pytest.mark.parametrize("status_code", [403, 404, 500])
def test_robots_header_on_exception_handlers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    async def fail() -> None:
        if status_code == 500:
            raise RuntimeError("SEO test error")
        raise HTTPException(status_code=status_code, detail="SEO test error")

    route = APIRoute("/__seo_test_error", fail, include_in_schema=False)
    monkeypatch.setattr(app.router, "routes", [*app.router.routes, route])

    response = client.get("/__seo_test_error")

    assert response.status_code == status_code
    assert response.json()["status_code"] == status_code
    assert response.headers["X-Robots-Tag"] == X_ROBOTS_TAG


@pytest.mark.parametrize(
    ("debug", "override", "enabled"),
    [
        (False, None, False),
        (True, None, True),
        (False, "true", True),
        (True, "false", False),
    ],
)
def test_api_docs_configuration(
    monkeypatch: pytest.MonkeyPatch,
    debug: bool,
    override: str | None,
    enabled: bool,
) -> None:
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    if override is not None:
        monkeypatch.setenv("ENABLE_API_DOCS", override)
    configured = Settings(_env_file=None, debug=debug)

    assert configured.debug is debug
    assert configured.enable_api_docs is (None if override is None else enabled)
    kwargs = api_docs_kwargs(configured)
    assert kwargs == {
        "openapi_url": "/openapi.json" if enabled else None,
        "docs_url": "/docs" if enabled else None,
        "redoc_url": "/redoc" if enabled else None,
    }

    with TestClient(FastAPI(**kwargs)) as docs_client:
        for path in ("/openapi.json", "/docs", "/redoc"):
            assert docs_client.get(path).status_code == (200 if enabled else 404)


def test_app_and_root_use_effective_docs_configuration(client: TestClient) -> None:
    kwargs = api_docs_kwargs(settings)
    assert app.openapi_url == kwargs["openapi_url"]
    assert app.docs_url == kwargs["docs_url"]
    assert app.redoc_url == kwargs["redoc_url"]

    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["docs_url"] == (
        kwargs["docs_url"] or "Contact admin for API documentation"
    )


def test_api_info_advertises_mcp(client: TestClient) -> None:
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    assert response.json()["endpoints"]["mcp"] == "/mcp/"
