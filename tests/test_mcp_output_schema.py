"""Strict schema and RFC 3339 checks at the Streamable HTTP output boundary."""

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from jsonschema import FormatChecker, ValidationError
from jsonschema.validators import validator_for
from mcp.types import ContentBlock, ImageContent, TextContent
from rfc3339_validator import validate_rfc3339

from app.mcp import _normalize_mcp_timestamps
from app.models.alpharank import (
    AlphaRankSignalsResponse,
    AlphaRankTickerHistoryResponse,
    alpha_rank_from_document,
)
from app.models.market import MarketOverviewResponse
from app.models.options import GexAnalysis, OptionsStructureAnalysis
from app.models.pivot import PivotPicksResponse
from app.routers.signals import SignalsResponse
from tests.test_mcp import GEX, STRUCTURE, call_tool, mcp_client, rpc

FORMAT_CHECKER = FormatChecker()
SESSION = "2026-10-23"
UTC_TIMESTAMP = "2026-10-24T02:39:43.525000Z"
TOOL_CASES = {
    "list_signal_dates": ({}, "signals.get_signal_dates"),
    "get_signals": ({}, "signals.get_latest_signals"),
    "get_pivot_signals": ({}, "signals.get_signals_pivot"),
    "get_alpharank_signals": ({}, "signals.get_latest_alpharank_signals"),
    "get_alpharank_history": (
        {"symbol": "AAPL"}, "signals.get_alpharank_ticker_history",
    ),
    "get_market_snapshot": ({}, "market.get_market_snapshot"),
    "get_market_overview": ({}, "market.get_market_overview"),
    "get_market_movers": ({}, "market.get_market_movers"),
    "get_quotes": ({"symbols": ["AAPL"]}, "market.get_quotes"),
    "get_technical_analysis": ({"symbol": "AAPL"}, "analysis.get_technical_analysis"),
    "run_screener": ({"request": {}}, "screener.run_screen"),
    "list_screener_presets": ({}, "screener.list_presets"),
    "run_screener_preset": (
        {"preset_id": "test-preset"}, "screener.run_preset",
    ),
    "list_option_activity_dates": ({}, "options.get_option_anomaly_dates"),
    "get_unusual_options": ({}, "options.get_latest_option_anomalies"),
    "get_option_activity": ({}, "options.get_option_anomalies"),
    "get_gamma_exposure": ({"symbol": "SPY"}, "options.get_latest_gex"),
    "get_options_structure": (
        {"symbol": "SPY"}, "options.get_latest_options_structure",
    ),
}


@FORMAT_CHECKER.checks("date-time")
def is_rfc3339(value: Any) -> bool:
    return not isinstance(value, str) or bool(validate_rfc3339(value))


def strict_validator(schema: dict[str, Any]) -> Any:
    validator = validator_for(schema)
    validator.check_schema(schema)
    return validator(schema, format_checker=FORMAT_CHECKER)


@pytest.fixture
def timestamp(request: pytest.FixtureRequest) -> datetime:
    if hasattr(request, "param"):
        return request.param
    return datetime(2026, 10, 24, 2, 39, 43, 525000)


@pytest.fixture
def payloads(timestamp: datetime) -> dict[str, Any]:
    quote = {"symbol": "AAPL", "date": SESSION, "close": 100}
    pivot = PivotPicksResponse(
        market="us",
        date=SESSION,
        longs=[{
            "date": SESSION, "symbol": "AAPL", "event": "BUY_TURN",
            "kind": "reversal", "direction": "long", "rank": 1,
            "action": "BULLISH", "score": 0.8, "confidence": 0.9,
        }],
        total_count=1,
    )
    # Model construction follows the Mongo document mapping used in production.
    alpha = AlphaRankSignalsResponse(
        market="us",
        date=SESSION,
        items=[
            alpha_rank_from_document({
                "date": SESSION, "symbol": f"TEST{rank}", "rank": rank,
                "horizon": 10, "tag": "test", "generated_at": timestamp,
                "score": 0.8, "consensus": 0.7, "predicted_excess_return": 0.1,
                "predicted_rank": 0.9, "top20_probability": 0.8,
            })
            for rank in range(1, 16)
        ],
        total_count=15,
    )
    screen = {
        "date": SESSION, "matches": [quote], "total_count": 1, "universe_size": 1,
    }
    anomalies = [{
        "symbol": "AAPL", "date": date.fromisoformat(SESSION),
        "items": [{
            "symbol": "AAPL", "contract": "AAPL261030C00100000",
            "option_type": "calls", "strike": 100,
            "expiration": date(2026, 10, 30),
        }],
    }]
    return {
        "list_signal_dates": {"market": "us", "dates": [SESSION], "count": 1},
        "get_signals": SignalsResponse(
            market="us", date=SESSION, pivot=pivot, alpharank=alpha,
        ),
        "get_pivot_signals": pivot,
        "get_alpharank_signals": alpha,
        "get_alpharank_history": AlphaRankTickerHistoryResponse(
            market="us", symbol="AAPL", items=[alpha.items[0]], count=1,
        ),
        "get_market_snapshot": {
            "date": SESSION,
            # The snapshot service already calls isoformat() on generated_at.
            "generated_at": timestamp.isoformat(),
            "metadata": {
                "refreshed_at": timestamp,
                "history": [{"finished_at": timestamp.isoformat()}],
                "session": date.fromisoformat(SESSION),
                "description": "Daily snapshot, not a timestamp",
                "optional": None,
            },
        },
        "get_market_overview": MarketOverviewResponse(
            date=SESSION, breadth={"date": SESSION, "total_symbols": 1},
            sentiment={"score": 50, "label": "Neutral"}, top_gainers=[quote],
            generated_at=timestamp,
        ),
        "get_market_movers": {
            "category": "gainers", "date": SESSION, "entries": [quote], "total_count": 1,
        },
        "get_quotes": [quote],
        "get_technical_analysis": {
            "symbol": "AAPL", "date": SESSION, "quote": quote,
            "trend": "neutral", "signal": "neutral", "signal_score": 0,
        },
        "run_screener": screen,
        "list_screener_presets": {"presets": [{
            "id": "test-preset", "name": "Test", "description": "Test preset",
            "filters": {"min_price": 10},
        }]},
        "run_screener_preset": screen,
        "list_option_activity_dates": {"dates": [SESSION], "count": 1},
        "get_unusual_options": anomalies,
        "get_option_activity": anomalies,
        "get_gamma_exposure": GexAnalysis.model_validate({
            **GEX, "date": SESSION, "previous_date": "2026-10-22",
            "generated_at": timestamp,
            "scopes": [{
                "key": "all", "net_gex": 10, "call_gex": 20, "put_gex": -10,
                "previous_net_gex": 8, "net_gex_change": 2, "regime": "positive",
                "decomposition": {
                    "oi_change": 2, "greeks": 0, "new_contracts": 0,
                    "removed_contracts": 0,
                },
                "top_drivers": [{
                    "contract": "SPY261030C00470000", "option_type": "calls",
                    "strike": 470, "expiration": "2026-10-30",
                    "open_interest_today": 100, "open_interest_previous": 90,
                    "oi_change": 10, "gex_today": 20, "gex_previous": 18,
                    "gex_change": 2, "share_of_absolute_change": 1,
                    "attribution": "oi_change", "is_unusual": False,
                }],
            }],
        }),
        "get_options_structure": OptionsStructureAnalysis.model_validate({
            **STRUCTURE, "date": SESSION, "previous_date": "2026-10-22",
            "generated_at": timestamp,
            "summary": {**STRUCTURE["summary"], "front_expiration": "2026-10-30"},
            "term_structure": [{
                "expiration": "2026-10-30", "days_to_expiry": 7,
                "call_open_interest": 60, "put_open_interest": 40,
                "call_oi_change": 6, "put_oi_change": 4,
            }],
            "skew_expirations": [{
                "expiration": "2026-10-30", "days_to_expiry": 7, "points": [],
            }],
        }),
    }


@pytest.fixture
async def tool_schemas(mcp_client) -> dict[str, dict[str, Any]]:
    response = await rpc(mcp_client, "tools/list")
    assert response.status_code == 200, response.text
    tools = response.json()["result"]["tools"]
    # Newly registered tools must get a fixture and a strict protocol-level test.
    assert {tool["name"] for tool in tools} == TOOL_CASES.keys()
    return {tool["name"]: tool["outputSchema"] for tool in tools}


def assert_utc_timestamps(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_at"):
                assert item.endswith(("Z", "+00:00")), (key, item)
                FORMAT_CHECKER.check(item, "date-time")
                assert datetime.fromisoformat(item) == datetime.fromisoformat(UTC_TIMESTAMP)
                assert datetime.fromisoformat(item).utcoffset() == timedelta(0)
            else:
                assert_utc_timestamps(item)
    elif isinstance(value, list):
        for item in value:
            assert_utc_timestamps(item)


def test_date_time_format_checker_requires_an_offset() -> None:
    validator = strict_validator({"type": "string", "format": "date-time"})
    with pytest.raises(ValidationError):
        validator.validate("2026-10-24T02:39:43.525000")
    validator.validate(UTC_TIMESTAMP)
    validator.validate("2026-10-24T02:39:43.525000+00:00")


@pytest.mark.parametrize(
    "block",
    [
        TextContent(type="text", text="Not JSON: 2026-10-24T02:39:43.525000"),
        TextContent(type="text", text="2026-10-24T02:39:43.525000"),
        ImageContent(type="image", data="dGVzdA==", mimeType="image/png"),
    ],
)
def test_normalization_leaves_non_json_text_and_non_text_blocks_untouched(
    block: ContentBlock,
) -> None:
    assert _normalize_mcp_timestamps([block])[0] is block


@pytest.mark.parametrize("tool", TOOL_CASES)
@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 10, 24, 2, 39, 43, 525000),
        datetime(2026, 10, 24, 8, 9, 43, 525000, timezone(timedelta(hours=5, minutes=30))),
        datetime(2026, 10, 23, 19, 39, 43, 525000, timezone(timedelta(hours=-7))),
    ],
    indirect=True,
    ids=["naive-utc", "east-of-utc", "west-of-utc"],
)
async def test_every_tool_strictly_matches_its_output_schema(
    mcp_client, tool_schemas, payloads, tool: str,
) -> None:
    arguments, target = TOOL_CASES[tool]
    with patch(f"app.routers.{target}", AsyncMock(return_value=payloads[tool])):
        result = await call_tool(mcp_client, tool, arguments)
    assert not result["isError"], result
    content = result["structuredContent"]
    strict_validator(tool_schemas[tool]).validate(content)
    assert_utc_timestamps(content)

    if tool == "get_market_snapshot":
        assert content["metadata"]["session"] == SESSION
        assert content["metadata"]["description"] == "Daily snapshot, not a timestamp"
        assert content["metadata"]["optional"] is None
    elif tool in {"get_unusual_options", "get_option_activity"}:
        assert content["result"][0]["date"] == SESSION
        assert content["result"][0]["items"][0]["expiration"] == "2026-10-30"
    elif tool in {"get_gamma_exposure", "get_options_structure"}:
        assert content["date"] == SESSION
        assert content["previous_date"] == "2026-10-22"


async def test_latest_combined_board_normalizes_all_fifteen_mongo_timestamps(
    mcp_client, tool_schemas, payloads,
) -> None:
    board = payloads["get_signals"]
    assert board.alpharank.items[0].generated_at.tzinfo is None
    validator = strict_validator(tool_schemas["get_signals"])
    with pytest.raises(ValidationError):
        validator.validate(board.model_dump(mode="json"))

    with patch(
        "app.routers.signals.get_latest_signals", AsyncMock(return_value=board),
    ):
        result = await call_tool(mcp_client, "get_signals")
    assert not result["isError"], result
    content = result["structuredContent"]
    validator.validate(content)
    assert content["date"] == SESSION
    assert len(content["alpharank"]["items"]) == 15
    assert all(
        item["generated_at"] == UTC_TIMESTAMP
        for item in content["alpharank"]["items"]
    )
    assert json.loads(result["content"][0]["text"]) == content
    # MCP normalization must not mutate shared REST response instances.
    assert board.alpharank.items[0].generated_at.tzinfo is None
