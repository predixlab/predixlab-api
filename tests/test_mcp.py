"""Protocol-level tests for the authenticated MCP query surface."""

import asyncio
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import LATEST_PROTOCOL_VERSION
from pydantic import SecretStr

from app.config import Settings
from app.core.security import create_access_token, create_refresh_token
from app.mcp import MCPAuthenticationMiddleware, create_mcp_server
from app.models.alpharank import AlphaRankSignalsResponse
from app.models.market import ScreenerRequest, ScreenerResponse
from app.routers import agent
from app.routers.signals import SignalsResponse

HEADERS = {
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": LATEST_PROTOCOL_VERSION,
}
TOOLS = {
    "list_signal_dates", "get_signals", "get_pivot_signals",
    "get_alpharank_signals", "get_alpharank_history",
    "get_market_snapshot", "get_market_overview", "get_market_movers",
    "get_quotes", "get_technical_analysis", "run_screener",
    "list_screener_presets", "run_screener_preset",
    "list_option_activity_dates", "get_unusual_options", "get_option_activity",
    "get_gamma_exposure", "get_options_structure",
}
USER = object()
GEX = {
    "symbol": "SPY",
    "date": "2024-01-02",
    "generated_at": "2024-01-03T00:00:00Z",
    "underlying_price": 470,
    "contract_count": 100,
    "valid_gamma_contract_count": 100,
    "oi_gamma_coverage": 1,
    "model": "call_positive_put_negative",
    "spot_move": 0,
}
STRUCTURE = {
    "symbol": "SPY",
    "date": "2024-01-02",
    "generated_at": "2024-01-03T00:00:00Z",
    "underlying_price": 470,
    "summary": {
        "front_expiration": "2024-01-05",
        "total_open_interest": 100,
        "previous_total_open_interest": 90,
        "total_oi_change": 10,
        "call_oi_change": 6,
        "put_oi_change": 4,
        "new_contracts": 2,
        "closed_contracts": 0,
    },
}


@pytest.fixture
async def mcp_client(mock_user, agent_tokens):
    config = Settings(
        public_api_url="https://api.predixlab.com",
        cors_origins=["http://localhost:3000", "http://localhost:8080"],
        session_secret_key=SecretStr("test-session-key-only-never-use-in-production"),
    )
    server = create_mcp_server(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with app.state.mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(agent.router, prefix="/api/v1")
    app.state.mcp = server
    app.mount("/mcp", MCPAuthenticationMiddleware(server.streamable_http_app()))
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def run_lifespan():
        async with app.router.lifespan_context(app):
            started.set()
            await stopped.wait()

    task = asyncio.create_task(run_lifespan())
    token = agent_tokens[mock_user.id].api_key
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={**HEADERS, "Authorization": f"Bearer {token}"},
        ) as client:
            yield client
    finally:
        stopped.set()
        await task


async def rpc(client, method, params=None, **kwargs):
    return await client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        },
        **kwargs,
    )


async def call_tool(client, name, arguments=None, **kwargs):
    response = await rpc(
        client, "tools/call", {"name": name, "arguments": arguments or {}}, **kwargs
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def test_initialize_and_tool_schemas(mcp_client):
    response = await rpc(
        mcp_client,
        "initialize",
        {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["serverInfo"]["name"] == "PredixLab"
    assert result["protocolVersion"] == LATEST_PROTOCOL_VERSION
    assert "tools" in result["capabilities"]
    assert "mcp-session-id" not in response.headers

    response = await rpc(mcp_client, "tools/list")
    tools = response.json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == TOOLS
    for tool in tools:
        assert tool["description"]
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["outputSchema"]["type"] == "object"
        properties = tool["inputSchema"]["properties"]
        assert not {"ctx", "current_user", "is_premium", "token"} & properties.keys()
    screener = next(tool for tool in tools if tool["name"] == "run_screener")
    assert "ScreenerFilters" in screener["inputSchema"]["$defs"]


async def test_official_sdk_client_can_initialize_list_and_call(mcp_client):
    with patch(
        "app.routers.market.market_service.refresh", AsyncMock()
    ), patch(
        "app.routers.market.market_service.get_snapshot",
        return_value={"date": "2024-01-02", "total_symbols": 42},
    ):
        async with streamable_http_client(
            "http://localhost/mcp/", http_client=mcp_client
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == TOOLS
                result = await session.call_tool("get_market_snapshot", {})
    assert not result.isError
    assert result.structuredContent == {"date": "2024-01-02", "total_symbols": 42}


@pytest.mark.parametrize("method", ["initialize", "tools/list", "tools/call"])
@pytest.mark.parametrize("authorization", ["", "Basic invalid", "Bearer invalid"])
async def test_auth_required_for_all_protocol_methods(mcp_client, method, authorization):
    response = await rpc(
        mcp_client, method, headers={"Authorization": authorization}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["status_code"] == 401


@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_auth_required_for_other_transport_methods(mcp_client, method):
    response = await mcp_client.request(
        method, "/mcp/", headers={"Authorization": ""}
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "authorization,reason",
    [
        ("", "missing_bearer"),
        ("Basic unsupported", "missing_bearer"),
        ("Bearer ${input:predixlab-api-key}", "unresolved_input"),
        ("Bearer ${env:PREDIXLAB_MCP_API_KEY}", "unresolved_environment"),
    ],
)
async def test_auth_failures_identify_mcp_stage_without_jwt_or_secret_logging(
    mcp_client, authorization, reason, caplog
):
    with patch(
        "app.core.dependencies.verify_token",
        side_effect=AssertionError("MCP must not use the REST JWT verifier"),
    ) as jwt_verifier:
        response = await rpc(
            mcp_client, "tools/list", headers={"Authorization": authorization}
        )
    assert response.status_code == 401
    assert f"reason={reason}" in caplog.text
    assert "MCP transport rejected request: method=POST status=401" in caplog.text
    if authorization:
        assert authorization not in caplog.text
    assert "predixlab-api-key" not in caplog.text
    assert "PREDIXLAB_MCP_API_KEY" not in caplog.text
    jwt_verifier.assert_not_called()


@pytest.mark.parametrize("kind", ["expired", "jwt", "refresh", "deleted", "inactive"])
async def test_invalid_account_and_token_states(
    mcp_client, mock_user, agent_tokens, agent_key_database, kind
):
    issued = agent_tokens[mock_user.id]
    token = issued.api_key
    if kind == "expired":
        agent_key_database.agent_api_keys.documents[issued.key.id]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )
    elif kind == "jwt":
        token = create_access_token({"sub": mock_user.id})
    elif kind == "refresh":
        token = create_refresh_token({"sub": mock_user.id})
    elif kind == "deleted":
        agent_key_database.agent_api_keys.documents[issued.key.id]["user_id"] = "missing-user"
    else:
        mock_user.is_active = False
    response = await rpc(
        mcp_client, "tools/list", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == (403 if kind == "inactive" else 401)


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"Host": "attacker.example"}, 421),
        ({"Origin": "https://attacker.example"}, 403),
        ({"Host": "api.predixlab.com", "Origin": "https://api.predixlab.com"}, 200),
        ({"Origin": "http://localhost:3000"}, 200),
    ],
)
async def test_transport_host_and_origin_checks(mcp_client, headers, expected):
    response = await rpc(mcp_client, "tools/list", headers=headers)
    assert response.status_code == expected


async def test_latest_and_dated_signals_use_existing_routes(mcp_client, mock_user):
    board = SignalsResponse(market="us", date="2024-01-02")
    with patch(
        "app.routers.signals.get_latest_signals", AsyncMock(return_value=board)
    ) as latest, patch(
        "app.routers.signals.get_signals_by_date", AsyncMock(return_value=board)
    ) as dated:
        result = await call_tool(mcp_client, "get_signals")
        assert result["structuredContent"]["date"] == "2024-01-02"
        latest.assert_awaited_once_with(mock_user, "us")
        result = await call_tool(
            mcp_client, "get_signals", {"date": "2024-01-02", "market": "us"}
        )
        assert result["isError"] is False
        dated.assert_awaited_once_with("2024-01-02", mock_user, "us")


async def test_free_account_cannot_query_recent_signals(mcp_client):
    with patch("app.routers.signals._combined_response", AsyncMock()) as combined:
        result = await call_tool(
            mcp_client, "get_signals", {"date": datetime.now().date().isoformat()}
        )
    assert result["isError"] is True
    assert "HTTP 403" in result["content"][0]["text"]
    combined.assert_not_awaited()


async def test_premium_account_can_query_recent_signals(
    mcp_client, mock_premium_user, agent_tokens
):
    date = datetime.now().date().isoformat()
    token = agent_tokens[mock_premium_user.id].api_key
    with patch(
        "app.routers.signals._combined_response",
        AsyncMock(return_value=SignalsResponse(market="us", date=date)),
    ) as combined:
        result = await call_tool(
            mcp_client,
            "get_signals",
            {"date": date},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert result["isError"] is False
    combined.assert_awaited_once_with(date, "us", True)


async def test_screener_reuses_typed_filters_and_pagination(mcp_client):
    arguments = {
        "filters": {"index": "nasdaq100", "min_rsi": 30, "above_sma50": True},
        "sort_by": "volume_ratio",
        "limit": 15,
        "offset": 30,
    }
    with patch(
        "app.routers.screener.screener_service.screen",
        AsyncMock(return_value=ScreenerResponse(limit=15, offset=30, universe_size=0)),
    ) as screen:
        result = await call_tool(mcp_client, "run_screener", {"request": arguments})
    assert not result["isError"]
    assert result["structuredContent"]["offset"] == 30
    screen.assert_awaited_once_with(ScreenerRequest.model_validate(arguments))


async def test_presets_can_be_discovered_and_run(mcp_client):
    result = await call_tool(mcp_client, "list_screener_presets")
    assert not result["isError"]
    preset_id = result["structuredContent"]["presets"][0]["id"]
    with patch(
        "app.routers.screener.screener_service.screen",
        AsyncMock(return_value=ScreenerResponse(universe_size=0)),
    ) as screen:
        result = await call_tool(
            mcp_client, "run_screener_preset",
            {"preset_id": preset_id, "index": "sp500", "limit": 25, "offset": 5},
        )
    assert not result["isError"]
    assert screen.await_args is not None
    request = screen.await_args.args[0]
    assert request.filters.index.value == "sp500"
    assert request.limit == 25
    assert request.offset == 5


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("list_signal_dates", {"limit": 0}),
        ("get_signals", {"date": "2024-02-30"}),
        ("get_pivot_signals", {"limit": 201}),
        ("get_pivot_signals", {"direction": "invalid"}),
        ("get_market_movers", {"category": "invalid"}),
        ("get_quotes", {"symbols": []}),
        ("get_quotes", {"symbols": ["AAPL"] * 51}),
        ("get_quotes", {"symbols": ["AAPL,MSFT"]}),
        ("get_option_activity", {"limit": 21}),
        ("get_option_activity", {"offset": -1}),
        ("get_gamma_exposure", {"symbol": ""}),
        ("run_screener", {"request": {"limit": 201}}),
        ("run_screener", {"request": {"filters": {"min_rsi": 101}}}),
        ("run_screener", {"request": {"filters": {"index": "invalid"}}}),
    ],
)
async def test_invalid_arguments_return_mcp_errors(mcp_client, tool, arguments):
    result = await call_tool(mcp_client, tool, arguments)
    assert result["isError"] is True
    assert "validation error" in result["content"][0]["text"].lower()


@pytest.mark.parametrize("symbol", ["../PRIVATE", "/tmp/PRIVATE", r"..\PRIVATE", "C:PRIVATE", "AAPL\x00", "AAPL\x1f", "AAPL\x7f"])
async def test_symbol_paths_and_control_characters_never_reach_data_handlers(mcp_client, symbol):
    with patch("app.routers.analysis.get_technical_analysis", AsyncMock()) as handler:
        result = await call_tool(mcp_client, "get_technical_analysis", {"symbol": symbol})
    assert result["isError"] is True
    handler.assert_not_awaited()


@pytest.mark.parametrize("symbol", ["AAPL", "BRK.B", "RDS-A", "^GSPC", "ABC_1"])
async def test_legitimate_ticker_punctuation_is_preserved(mcp_client, symbol):
    with patch("app.routers.market.get_quotes", AsyncMock(return_value=[{"symbol": symbol}])):
        result = await call_tool(mcp_client, "get_quotes", {"symbols": [symbol]})
    assert result["isError"] is False
    assert result["structuredContent"]["result"][0]["symbol"] == symbol


async def test_unknown_tool_and_missing_preset_are_explicit_errors(mcp_client):
    result = await call_tool(mcp_client, "delete_account")
    assert result["isError"] is True
    result = await call_tool(
        mcp_client, "run_screener_preset", {"preset_id": "does-not-exist"}
    )
    assert result["isError"] is True
    assert "HTTP 404" in result["content"][0]["text"]


async def test_internal_errors_are_logged_but_not_exposed(mcp_client, caplog):
    with patch(
        "app.routers.market.market_service.refresh",
        AsyncMock(side_effect=RuntimeError("private-database-detail")),
    ):
        result = await call_tool(mcp_client, "get_market_snapshot")
    assert result["isError"] is True
    assert "internal server error" in result["content"][0]["text"]
    assert "private-database-detail" not in result["content"][0]["text"]
    assert "MCP tool get_market_snapshot failed" in caplog.text


async def test_concurrent_requests_keep_account_permissions_isolated(
    mcp_client, mock_user, mock_premium_user, agent_tokens
):
    async def anomalies(*, is_premium, limit):
        assert limit == 20
        await asyncio.sleep(0)
        return [{
            "symbol": "PREMIUM" if is_premium else "DELAYED",
            "date": "2024-01-02",
            "items": [],
        }]

    service = MagicMock()
    service.get_latest_anomalies = AsyncMock(side_effect=anomalies)
    with patch("app.routers.options.options_service", service):
        free_result, premium_result = await asyncio.gather(*(
            call_tool(
                mcp_client,
                "get_unusual_options",
                headers={
                    "Authorization": f"Bearer {agent_tokens[user.id].api_key}"
                },
            )
            for user in (mock_user, mock_premium_user)
        ))
    assert free_result["structuredContent"]["result"][0]["symbol"] == "DELAYED"
    assert premium_result["structuredContent"]["result"][0]["symbol"] == "PREMIUM"


async def test_latest_alpharank_uses_structured_response(mcp_client):
    response = AlphaRankSignalsResponse(market="us", date="2024-01-02")
    with patch(
        "app.routers.signals.get_latest_alpharank_signals",
        AsyncMock(return_value=response),
    ):
        result = await call_tool(mcp_client, "get_alpharank_signals")
    assert result["structuredContent"] == response.model_dump(mode="json")
    assert result["isError"] is False


@pytest.mark.parametrize(
    "tool,arguments,target,payload,expected_args",
    [
        (
            "list_signal_dates", {"market": "us", "limit": 10},
            "signals.get_signal_dates",
            {"market": "us", "dates": ["2024-01-02"], "count": 1},
            (USER, "us", 10),
        ),
        (
            "get_pivot_signals", {"direction": "short", "limit": 5},
            "signals.get_signals_pivot",
            {"market": "us", "date": "2024-01-02"},
            (USER, "us", "short", None, 5),
        ),
        (
            "get_pivot_signals", {"date": "2024-01-02", "event": "BUY_TURN"},
            "signals.get_signals_pivot_by_date",
            {"market": "us", "date": "2024-01-02"},
            ("2024-01-02", USER, "us", None, "BUY_TURN", 100),
        ),
        (
            "get_alpharank_signals", {"date": "2024-01-02"},
            "signals.get_alpharank_signals_by_date",
            {"market": "us", "date": "2024-01-02"},
            ("2024-01-02", USER, "us"),
        ),
        (
            "get_alpharank_history", {"symbol": "AAPL", "limit": 20},
            "signals.get_alpharank_ticker_history",
            {"market": "us", "symbol": "AAPL"},
            ("AAPL", USER, "us", 20),
        ),
        (
            "get_market_overview", {"movers_limit": 3},
            "market.get_market_overview",
            {"breadth": {}, "sentiment": {"score": 50, "label": "Neutral"}},
            (USER, 3),
        ),
        (
            "get_market_movers", {"category": "losers", "limit": 3},
            "market.get_market_movers", {"category": "losers"},
            (USER, "losers", 3),
        ),
        (
            "get_quotes", {"symbols": ["AAPL", "MSFT"]},
            "market.get_quotes", [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
            (USER, "AAPL,MSFT"),
        ),
        (
            "get_technical_analysis", {"symbol": "AAPL"},
            "analysis.get_technical_analysis",
            {
                "symbol": "AAPL", "quote": {"symbol": "AAPL"},
                "trend": "neutral", "signal": "neutral", "signal_score": 0,
            },
            ("AAPL", USER),
        ),
        (
            "list_option_activity_dates", {"limit": 10},
            "options.get_option_anomaly_dates",
            {"dates": ["2024-01-02"], "count": 1}, (10, USER),
        ),
        (
            "get_unusual_options", {"date": "2024-01-02"},
            "options.get_option_anomalies_by_date",
            [{"symbol": "AAPL", "date": "2024-01-02"}],
            ("2024-01-02", USER),
        ),
        (
            "get_option_activity", {"limit": 5, "offset": 10},
            "options.get_option_anomalies",
            [{"symbol": "AAPL", "date": "2024-01-02"}],
            (10, 5, USER),
        ),
        (
            "get_gamma_exposure", {"symbol": "SPY"},
            "options.get_latest_gex", GEX, ("SPY", USER),
        ),
        (
            "get_gamma_exposure", {"symbol": "SPY", "date": "2024-01-02"},
            "options.get_gex_by_date", GEX, ("2024-01-02", "SPY", USER),
        ),
        (
            "get_options_structure", {"symbol": "SPY"},
            "options.get_latest_options_structure", STRUCTURE, ("SPY", USER),
        ),
    ],
)
async def test_tool_route_mapping_and_output_serialization(
    mcp_client, mock_user, tool, arguments, target, payload, expected_args
):
    with patch(f"app.routers.{target}", AsyncMock(return_value=payload)) as endpoint:
        result = await call_tool(mcp_client, tool, arguments)
    assert result["isError"] is False, result
    assert isinstance(result["structuredContent"], dict)
    endpoint.assert_awaited_once_with(*(
        mock_user if value is USER else value for value in expected_args
    ))
    if isinstance(payload, list):
        assert len(result["structuredContent"]["result"]) == len(payload)
    else:
        for key, value in payload.items():
            if not isinstance(value, (dict, list)):
                assert result["structuredContent"][key] == value


@pytest.mark.parametrize("tool", ["get_signals", "get_alpharank_signals"])
async def test_expired_membership_does_not_unlock_recent_signals(
    mcp_client, mock_premium_user, agent_tokens, tool
):
    mock_premium_user.premium_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    token = agent_tokens[mock_premium_user.id].api_key
    result = await call_tool(
        mcp_client,
        tool,
        {"date": datetime.now().date().isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert result["isError"] is True
    assert "HTTP 403" in result["content"][0]["text"]


async def test_account_is_rechecked_on_every_request(mcp_client, mock_user):
    response = await rpc(mcp_client, "tools/list")
    assert response.status_code == 200
    mock_user.is_active = False
    response = await rpc(mcp_client, "tools/list")
    assert response.status_code == 403


async def test_jwt_management_to_mcp_rotation_and_revocation_end_to_end(mcp_client, mock_user):
    jwt_headers = {"Authorization": f"Bearer {create_access_token({'sub': mock_user.id})}"}
    response = await mcp_client.post(
        "/api/v1/agent/api-keys", json={"name": "VS Code"}, headers=jwt_headers
    )
    assert response.status_code == 201
    issued = response.json()
    key_id = issued["key"]["id"]
    old_headers = {"Authorization": f"Bearer {issued['api_key']}"}
    assert (await rpc(mcp_client, "tools/list", headers=old_headers)).status_code == 200

    response = await mcp_client.post(
        f"/api/v1/agent/api-keys/{key_id}/rotate",
        json={"version": 1}, headers=jwt_headers,
    )
    assert response.status_code == 200
    rotated = response.json()
    new_headers = {"Authorization": f"Bearer {rotated['api_key']}"}
    assert (await rpc(mcp_client, "tools/list", headers=old_headers)).status_code == 401
    assert (await rpc(mcp_client, "tools/list", headers=new_headers)).status_code == 200
    response = await mcp_client.delete(
        f"/api/v1/agent/api-keys/{key_id}", params={"version": 2}, headers=jwt_headers
    )
    assert response.status_code == 204
    assert (await rpc(mcp_client, "tools/list", headers=new_headers)).status_code == 401


async def test_key_cannot_manage_keys_or_authenticate_in_a_url(mcp_client, mock_user, agent_tokens):
    response = await mcp_client.get("/api/v1/agent/api-keys")
    assert response.status_code == 401
    response = await mcp_client.post(
        "/mcp/",
        params={"api_key": agent_tokens[mock_user.id].api_key},
        headers={"Authorization": ""},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "setting", ["mcp_ip_requests_per_minute", "mcp_key_requests_per_minute",
                "mcp_user_requests_per_minute"]
)
async def test_mcp_http_quota_errors_preserve_retry_after(mcp_client, monkeypatch, setting):
    from app.config import settings

    monkeypatch.setattr(settings, setting, 1)
    assert (await rpc(mcp_client, "tools/list")).status_code == 200
    response = await rpc(mcp_client, "tools/list")
    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert response.json()["status_code"] == 429


async def test_production_app_mount_and_lifespan_with_official_client(mock_user, agent_tokens):
    from app import main

    services = (
        "user_service", "stock_service", "picks_service", "options_service",
        "stripe_service", "predictions_service", "market_service", "analysis_service",
        "screener_service", "screener_strategy_service", "watchlist_service",
        "alpharank_service", "referral_service",
    )
    token = agent_tokens[mock_user.id].api_key
    with ExitStack() as stack:
        for name in services:
            stack.enter_context(patch.object(
                getattr(main, name), "initialize", AsyncMock()
            ))
        for name in ("connect_to_mongo", "close_mongo_connection", "run_expiry_cleanup"):
            stack.enter_context(patch.object(main, name, AsyncMock()))
        stack.enter_context(patch.object(main, "AsyncIOScheduler", MagicMock()))
        stack.enter_context(patch(
            "app.core.dependencies.user_service.get_user_by_id",
            AsyncMock(return_value=mock_user),
        ))
        stack.enter_context(patch.object(main.market_service, "refresh", AsyncMock()))
        jwt_verifier = stack.enter_context(patch(
            "app.core.dependencies.verify_token",
            side_effect=AssertionError("MCP must not use the REST JWT verifier"),
        ))
        key_verifier = stack.enter_context(patch(
            "app.mcp.agent_key_service.authenticate",
            wraps=main.agent.agent_key_service.authenticate,
        ))
        stack.enter_context(patch.object(
            main.market_service, "get_snapshot", return_value={"date": "2024-01-02"}
        ))
        async with main.app.router.lifespan_context(main.app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app),
                base_url="http://localhost",
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                response = await client.get("/health")
                assert response.status_code == 200
                async with streamable_http_client(
                    "http://localhost/mcp/", http_client=client
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        discovered = await session.list_tools()
                        assert {tool.name for tool in discovered.tools} == TOOLS
                        result = await session.call_tool("get_market_snapshot", {})
                assert key_verifier.await_count >= 3
                jwt_verifier.assert_not_called()
                rest = await client.get("/api/v1/user/profile")
                assert rest.status_code == 401
                jwt_verifier.assert_called_once_with(token)
    assert not result.isError
    assert result.structuredContent == {"date": "2024-01-02"}
