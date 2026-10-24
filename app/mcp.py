"""Authenticated, read-only MCP access to the existing query endpoints."""

import logging
from collections.abc import Awaitable, Callable
from datetime import date as TradingDate
from functools import wraps
from typing import Annotated, Any, ParamSpec, TypeVar
from urllib.parse import urlsplit

from fastapi import HTTPException
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings, settings
from app.core.dependencies import get_client_ip, security
from app.core.rate_limits import enforce_mcp_ip_rate_limit
from app.models.alpharank import (
    AlphaRankAvailableDatesResponse,
    AlphaRankSignalsResponse,
    AlphaRankTickerHistoryResponse,
)
from app.models.market import (
    MarketOverviewResponse,
    MoverCategory,
    MoversResponse,
    ScreenerPresetsResponse,
    ScreenerRequest,
    ScreenerResponse,
    StockIndex,
    SymbolQuote,
    TechnicalAnalysisResponse,
)
from app.models.options import (
    AnomalyOption,
    GexAnalysis,
    OptionsAnomalyDatesResponse,
    OptionsStructureAnalysis,
)
from app.models.pivot import PickDirection, PickEvent, PivotPicksResponse
from app.models.user import UserInDB
from app.routers import analysis, market, options, screener, signals
from app.routers.signals import SignalsResponse
from app.services.market_service import MARKET_RESULT_LIMIT
from app.services.options_service import MAX_OPTIONS_ACTIVITY_TICKERS
from app.services.agent_key_service import agent_key_service

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")
PageSize = Annotated[int, Field(ge=1, le=200)]
DateLimit = Annotated[int, Field(ge=1, le=800)]
Offset = Annotated[int, Field(ge=0)]
Symbol = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9.^_-]+$")]
Market = Annotated[str, Field(min_length=1, max_length=20)]


class MCPAuthenticationMiddleware:
    """Authenticate every transport request, including initialize and discovery."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        try:
            credentials = await security(request)
            if credentials is None:
                logger.warning("MCP authentication rejected: reason=missing_bearer")
                raise HTTPException(
                    status_code=401,
                    detail="MCP API key required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            await enforce_mcp_ip_rate_limit(await get_client_ip(request))
            request.state.mcp_user = await agent_key_service.authenticate(credentials.credentials)
        except HTTPException as exc:
            logger.warning(
                "MCP transport rejected request: method=%s status=%s",
                request.method, exc.status_code,
            )
            response = JSONResponse(
                {"error": True, "message": exc.detail, "status_code": exc.status_code},
                status_code=exc.status_code,
                headers=exc.headers,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _user(ctx: Context) -> UserInDB:
    request = ctx.request_context.request
    user = getattr(request.state, "mcp_user", None) if isinstance(request, Request) else None
    if not isinstance(user, UserInDB):
        raise ToolError("Authentication required")
    return user


def _query_tool(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    @wraps(fn)
    async def query(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except HTTPException as exc:
            logger.warning("MCP tool %s returned HTTP %s", fn.__name__, exc.status_code)
            raise ToolError(f"HTTP {exc.status_code}: {exc.detail}") from exc
        except ToolError:
            raise
        except Exception as exc:
            logger.exception("MCP tool %s failed", fn.__name__)
            raise ToolError("Query failed due to an internal server error") from exc

    return query


@_query_tool
async def list_signal_dates(
    ctx: Context, market: Market = "us", limit: DateLimit = 60
) -> AlphaRankAvailableDatesResponse:
    """List published signal sessions visible to this account, newest first."""
    return await signals.get_signal_dates(_user(ctx), market, limit)


@_query_tool
async def get_signals(
    ctx: Context, date: TradingDate | None = None, market: Market = "us"
) -> SignalsResponse:
    """Get the combined Pivot/AlphaRank board; omit date for the latest visible session.

    Free accounts retain the REST API's delayed-data window. Dates are YYYY-MM-DD.
    """
    user = _user(ctx)
    if date is None:
        return await signals.get_latest_signals(user, market)
    return await signals.get_signals_by_date(date.isoformat(), user, market)


@_query_tool
async def get_pivot_signals(
    ctx: Context,
    date: TradingDate | None = None,
    market: Market = "us",
    direction: PickDirection | None = None,
    event: PickEvent | None = None,
    limit: PageSize = 100,
) -> PivotPicksResponse:
    """Get Pivot signals by session, direction and event; omit date for latest visible."""
    user = _user(ctx)
    if date is None:
        return await signals.get_signals_pivot(user, market, direction, event, limit)
    return await signals.get_signals_pivot_by_date(
        date.isoformat(), user, market, direction, event, limit
    )


@_query_tool
async def get_alpharank_signals(
    ctx: Context, date: TradingDate | None = None, market: Market = "us"
) -> AlphaRankSignalsResponse:
    """Get AlphaRank top-15 for a session; omit date for the latest visible session."""
    user = _user(ctx)
    if date is None:
        return await signals.get_latest_alpharank_signals(user, market)
    return await signals.get_alpharank_signals_by_date(date.isoformat(), user, market)


@_query_tool
async def get_alpharank_history(
    ctx: Context,
    symbol: Symbol,
    market: Market = "us",
    limit: Annotated[int, Field(ge=1, le=750)] = 120,
) -> AlphaRankTickerHistoryResponse:
    """Get visible AlphaRank publications for a ticker, oldest first."""
    return await signals.get_alpharank_ticker_history(symbol, _user(ctx), market, limit)


@_query_tool
async def get_market_snapshot(ctx: Context) -> dict[str, Any]:
    """Get the complete latest daily market snapshot, not a live tick feed."""
    return await market.get_market_snapshot(_user(ctx))


@_query_tool
async def get_market_overview(
    ctx: Context,
    movers_limit: Annotated[int, Field(ge=1, le=MARKET_RESULT_LIMIT)] = 5,
) -> MarketOverviewResponse:
    """Get breadth, sentiment and leading movers in one daily overview."""
    return await market.get_market_overview(_user(ctx), movers_limit)


@_query_tool
async def get_market_movers(
    ctx: Context,
    category: MoverCategory = MoverCategory.GAINERS,
    limit: Annotated[int, Field(ge=1, le=MARKET_RESULT_LIMIT)] = MARKET_RESULT_LIMIT,
) -> MoversResponse:
    """Get daily gainers, losers, activity, unusual volume, gaps or 52-week extremes."""
    return await market.get_market_movers(_user(ctx), category, limit)


@_query_tool
async def get_quotes(
    ctx: Context, symbols: Annotated[list[Symbol], Field(min_length=1, max_length=50)]
) -> list[SymbolQuote]:
    """Get latest session quotes for 1-50 symbols, not real-time streaming prices."""
    return await market.get_quotes(_user(ctx), ",".join(symbols))


@_query_tool
async def get_technical_analysis(ctx: Context, symbol: Symbol) -> TechnicalAnalysisResponse:
    """Get a symbol's trend, momentum, key levels and technical interpretation."""
    return await analysis.get_technical_analysis(symbol, _user(ctx))


@_query_tool
async def run_screener(ctx: Context, request: ScreenerRequest) -> ScreenerResponse:
    """Screen the daily snapshot using typed filters, sorting and pagination.

    Supports all, sp500 and nasdaq100 universes. Results are capped at 200 per page.
    """
    return await screener.run_screen(request, _user(ctx))


@_query_tool
async def list_screener_presets(ctx: Context) -> ScreenerPresetsResponse:
    """Discover built-in screener strategy IDs and their criteria."""
    return await screener.list_presets(_user(ctx))


@_query_tool
async def run_screener_preset(
    ctx: Context,
    preset_id: Annotated[str, Field(min_length=1, max_length=100)],
    index: StockIndex = StockIndex.ALL,
    limit: PageSize = 50,
    offset: Offset = 0,
) -> ScreenerResponse:
    """Run a built-in screener preset in an index universe; no strategy is saved."""
    return await screener.run_preset(preset_id, _user(ctx), index, limit, offset)


@_query_tool
async def list_option_activity_dates(
    ctx: Context, limit: DateLimit = 60
) -> OptionsAnomalyDatesResponse:
    """List unusual-options activity sessions visible to this account."""
    return await options.get_option_anomaly_dates(limit, _user(ctx))


@_query_tool
async def get_unusual_options(
    ctx: Context, date: TradingDate | None = None
) -> list[AnomalyOption]:
    """Get unusual option activity grouped by symbol, up to 20 tickers per session.

    Omit date for the latest visible session. Free-account data delays still apply.
    Activity is descriptive market data, not a recommendation or a trade instruction.
    """
    user = _user(ctx)
    if date is None:
        return await options.get_latest_option_anomalies(user)
    return await options.get_option_anomalies_by_date(date.isoformat(), user)


@_query_tool
async def get_option_activity(
    ctx: Context,
    limit: Annotated[int, Field(ge=1, le=MAX_OPTIONS_ACTIVITY_TICKERS)] = MAX_OPTIONS_ACTIVITY_TICKERS,
    offset: Offset = 0,
) -> list[AnomalyOption]:
    """Browse visible unusual-options activity with ticker-level pagination."""
    return await options.get_option_anomalies(offset, limit, _user(ctx))


@_query_tool
async def get_gamma_exposure(
    ctx: Context, symbol: Symbol, date: TradingDate | None = None
) -> GexAnalysis:
    """Get gamma exposure for a symbol; omit date for the latest visible analysis."""
    user = _user(ctx)
    if date is None:
        return await options.get_latest_gex(symbol, user)
    return await options.get_gex_by_date(date.isoformat(), symbol, user)


@_query_tool
async def get_options_structure(ctx: Context, symbol: Symbol) -> OptionsStructureAnalysis:
    """Get the latest visible implied-volatility and positioning structure for a symbol."""
    return await options.get_latest_options_structure(symbol, _user(ctx))


def create_mcp_server(config: Settings = settings) -> FastMCP:
    public_url = urlsplit(config.public_api_url)
    public_origin = f"{public_url.scheme}://{public_url.netloc}"
    server = FastMCP(
        "PredixLab",
        instructions=(
            "Read-only market research tools. Authenticate using a dedicated PredixLab MCP API key. "
            "Start with list_signal_dates, list_option_activity_dates or "
            "list_screener_presets to discover available data. "
            "Free accounts have the same delayed-data access as the REST API. "
            "Snapshots and quotes are daily data, not live prices. "
            "Signals and options activity are not investment advice."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=[
                "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*",
                "[::1]", "[::1]:*", public_url.netloc,
            ],
            allowed_origins=[
                "http://localhost",
                "http://localhost:*",
                "http://127.0.0.1",
                "http://127.0.0.1:*",
                "http://[::1]",
                "http://[::1]:*",
                public_origin,
                *config.cors_origins,
            ],
        ),
    )
    for tool in (
        list_signal_dates,
        get_signals,
        get_pivot_signals,
        get_alpharank_signals,
        get_alpharank_history,
        get_market_snapshot,
        get_market_overview,
        get_market_movers,
        get_quotes,
        get_technical_analysis,
        run_screener,
        list_screener_presets,
        run_screener_preset,
        list_option_activity_dates,
        get_unusual_options,
        get_option_activity,
        get_gamma_exposure,
        get_options_structure,
    ):
        server.add_tool(
            tool,
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
    return server
