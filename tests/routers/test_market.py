"""Unit tests for the market, screener and watchlist routers."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user
from app.models.market import (
    Alert,
    AlertCheckResponse,
    AlertCreateRequest,
    AlertType,
    HeatmapResponse,
    MarketBreadth,
    MarketOverviewResponse,
    MarketSentiment,
    MoverCategory,
    MoversResponse,
    PerformanceDistributionResponse,
    ScreenerRequest,
    ScreenerResponse,
    SymbolQuote,
    Watchlist,
    WatchlistCreateRequest,
    WatchlistSymbolRequest,
)
from app.routers.market import (
    _parse_symbols,
    get_market_breadth,
    get_market_heatmap,
    get_market_movers,
    get_market_overview,
    get_market_snapshot,
    get_market_sentiment,
    get_performance_distribution,
    get_quotes,
    router as market_router,
)
from app.routers.screener import list_presets, run_preset, run_screen
from app.routers.watchlist import (
    add_symbol,
    check_alerts,
    create_alert,
    create_watchlist,
    delete_alert,
    delete_watchlist,
    get_watchlist,
    get_watchlist_quotes,
    list_alerts,
    list_watchlists,
    remove_symbol,
)


def _breadth() -> MarketBreadth:
    return MarketBreadth(date="2024-06-28", total_symbols=4, advancers=3, decliners=1)


def _sentiment() -> MarketSentiment:
    return MarketSentiment(score=62.0, label="greed", components={}, summary="ok")


class TestMarketRouter:
    @pytest.mark.asyncio
    async def test_snapshot_returns_complete_summary(self, mock_user):
        payload = {"date": "2024-06-28", "movers": {}, "heatmaps": {}}
        with patch("app.routers.market.market_service") as service:
            service.refresh = AsyncMock(return_value=False)
            service.get_snapshot.return_value = payload

            result = await get_market_snapshot(current_user=mock_user)

        assert result == payload
        service.refresh.assert_awaited_once_with()

    @pytest.mark.parametrize(
        "path",
        [
            "/market/overview?movers_limit=11",
            "/market/movers?limit=11",
            "/market/heatmap?limit=11",
        ],
    )
    def test_display_limits_above_ten_are_rejected(self, mock_user, path):
        app = FastAPI()
        app.include_router(market_router)
        app.dependency_overrides[get_current_user] = lambda: mock_user

        response = TestClient(app).get(path)

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_overview_returns_service_payload(self, mock_user):
        payload = MarketOverviewResponse(date="2024-06-28", breadth=_breadth(), sentiment=_sentiment())
        with patch("app.routers.market.market_service") as service:
            service.get_overview = AsyncMock(return_value=payload)
            result = await get_market_overview(current_user=mock_user, movers_limit=5)
        assert result.date == "2024-06-28"

    @pytest.mark.asyncio
    async def test_overview_wraps_errors(self, mock_user):
        with patch("app.routers.market.market_service") as service:
            service.get_overview = AsyncMock(side_effect=RuntimeError("boom"))
            with pytest.raises(HTTPException) as exc:
                await get_market_overview(current_user=mock_user, movers_limit=5)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_breadth_endpoint(self, mock_user):
        with patch("app.routers.market.market_service") as service:
            service.get_breadth = AsyncMock(return_value=_breadth())
            result = await get_market_breadth(current_user=mock_user)
        assert result.advancers == 3

    @pytest.mark.asyncio
    async def test_breadth_wraps_errors(self, mock_user):
        with patch("app.routers.market.market_service") as service:
            service.get_breadth = AsyncMock(side_effect=RuntimeError("boom"))
            with pytest.raises(HTTPException):
                await get_market_breadth(current_user=mock_user)

    @pytest.mark.asyncio
    async def test_sentiment_endpoint(self, mock_user):
        with patch("app.routers.market.market_service") as service:
            service.get_sentiment = AsyncMock(return_value=_sentiment())
            result = await get_market_sentiment(current_user=mock_user)
        assert result.label == "greed"

    @pytest.mark.asyncio
    async def test_movers_passes_category(self, mock_user):
        with patch("app.routers.market.market_service") as service:
            service.get_movers = AsyncMock(
                return_value=MoversResponse(category=MoverCategory.LOSERS)
            )
            await get_market_movers(
                current_user=mock_user, category=MoverCategory.LOSERS, limit=7
            )
            service.get_movers.assert_awaited_once_with(category=MoverCategory.LOSERS, limit=7)

    @pytest.mark.asyncio
    async def test_performance_distribution_endpoint(self, mock_user):
        with patch("app.routers.market.market_service") as service:
            service.get_performance_distribution = AsyncMock(
                return_value=PerformanceDistributionResponse(window_days=7)
            )
            result = await get_performance_distribution(current_user=mock_user, window_days=7)
        assert result.window_days == 7

    @pytest.mark.parametrize("window_days", [1, 7, 30])
    def test_performance_distribution_parses_supported_query_windows(
        self,
        mock_user,
        window_days,
    ):
        app = FastAPI()
        app.include_router(market_router)
        app.dependency_overrides[get_current_user] = lambda: mock_user
        payload = PerformanceDistributionResponse(window_days=window_days)

        with patch("app.routers.market.market_service") as service:
            service.get_performance_distribution = AsyncMock(return_value=payload)
            response = TestClient(app).get(
                f"/market/performance-distribution?window_days={window_days}"
            )

        assert response.status_code == 200
        assert response.json()["window_days"] == window_days

    def test_performance_distribution_rejects_unsupported_query_window(
        self,
        mock_user,
    ):
        app = FastAPI()
        app.include_router(market_router)
        app.dependency_overrides[get_current_user] = lambda: mock_user

        response = TestClient(app).get(
            "/market/performance-distribution?window_days=5"
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "window_days must be one of: 1, 7, 30"

    @pytest.mark.asyncio
    async def test_heatmap_endpoint(self, mock_user):
        with patch("app.routers.market.market_service") as service:
            service.get_heatmap = AsyncMock(return_value=HeatmapResponse(window_days=1))
            result = await get_market_heatmap(current_user=mock_user, window_days=1, limit=10)
        assert result.window_days == 1

    @pytest.mark.asyncio
    async def test_quotes_endpoint(self, mock_user):
        with patch("app.routers.market.analysis_service") as service:
            service.get_quotes = AsyncMock(return_value=[SymbolQuote(symbol="AAA", close=1.0)])
            result = await get_quotes(current_user=mock_user, symbols="aaa, bbb")
            service.get_quotes.assert_awaited_once_with(["AAA", "BBB"])
        assert result[0].symbol == "AAA"

    @pytest.mark.asyncio
    async def test_quotes_rejects_empty_symbols(self, mock_user):
        with pytest.raises(HTTPException) as exc:
            await get_quotes(current_user=mock_user, symbols=" , ")
        assert exc.value.status_code == 400


class TestParseSymbols:
    def test_normalizes_and_dedupes(self):
        assert _parse_symbols("aapl, msft ,AAPL") == ["AAPL", "MSFT"]

    def test_respects_limit(self):
        assert _parse_symbols("a,b,c,d", limit=2) == ["A", "B"]

    def test_handles_none(self):
        assert _parse_symbols(None) == []

    def test_handles_blank(self):
        assert _parse_symbols("  ") == []


class TestScreenerRouter:
    @pytest.mark.asyncio
    async def test_run_screen_delegates(self, mock_user):
        request = ScreenerRequest()
        with patch("app.routers.screener.screener_service") as service:
            service.screen = AsyncMock(return_value=ScreenerResponse(total_count=3))
            result = await run_screen(request, current_user=mock_user)
            service.screen.assert_awaited_once_with(request)
        assert result.total_count == 3

    @pytest.mark.asyncio
    async def test_run_screen_wraps_errors(self, mock_user):
        with patch("app.routers.screener.screener_service") as service:
            service.screen = AsyncMock(side_effect=RuntimeError("boom"))
            with pytest.raises(HTTPException) as exc:
                await run_screen(ScreenerRequest(), current_user=mock_user)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_list_presets(self, mock_user):
        result = await list_presets(current_user=mock_user)
        assert result.presets
        assert all(p.id for p in result.presets)

    @pytest.mark.asyncio
    async def test_run_unknown_preset_returns_404(self, mock_user):
        with pytest.raises(HTTPException) as exc:
            await run_preset("does_not_exist", current_user=mock_user, limit=10, offset=0)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_run_known_preset(self, mock_user):
        from app.services.screener_service import screener_service as real_service

        preset_id = real_service.get_presets()[0].id
        with patch("app.routers.screener.screener_service") as service:
            service.get_presets = real_service.get_presets
            service.screen = AsyncMock(return_value=ScreenerResponse(total_count=1))
            result = await run_preset(preset_id, current_user=mock_user, limit=10, offset=0)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_preset_runs_over_the_requested_index(self, mock_user):
        """A preset describes a setup shape; the index it runs over is separate."""
        from app.models.market import StockIndex
        from app.services.screener_service import screener_service as real_service

        preset = real_service.get_presets()[0]
        with patch("app.routers.screener.screener_service") as service:
            service.get_presets = real_service.get_presets
            service.screen = AsyncMock(return_value=ScreenerResponse())
            await run_preset(
                preset.id,
                current_user=mock_user,
                index=StockIndex.NASDAQ100,
                limit=10,
                offset=0,
            )

        sent = service.screen.await_args.args[0]
        assert sent.filters.index == StockIndex.NASDAQ100
        # The preset's own criteria must survive the override.
        assert sent.filters.above_sma200 == preset.filters.above_sma200
        assert preset.filters.index == StockIndex.ALL


class TestWatchlistRouter:
    def _watchlist(self) -> Watchlist:
        return Watchlist(id="abc", user_id="test_user_id", name="Default", items=[])

    @pytest.mark.asyncio
    async def test_list_watchlists(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.list_watchlists = AsyncMock(return_value=[self._watchlist()])
            result = await list_watchlists(current_user=mock_user)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_create_watchlist(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.create_watchlist = AsyncMock(return_value=self._watchlist())
            result = await create_watchlist(
                WatchlistCreateRequest(name="Default", symbols=["AAPL"]), current_user=mock_user
            )
        assert result.name == "Default"

    @pytest.mark.asyncio
    async def test_create_watchlist_conflict_is_400(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.create_watchlist = AsyncMock(side_effect=ValueError("already exists"))
            with pytest.raises(HTTPException) as exc:
                await create_watchlist(WatchlistCreateRequest(), current_user=mock_user)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_get_watchlist_missing_is_404(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.get_watchlist = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await get_watchlist("abc", current_user=mock_user)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_watchlist_missing_is_404(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.delete_watchlist = AsyncMock(return_value=False)
            with pytest.raises(HTTPException) as exc:
                await delete_watchlist("abc", current_user=mock_user)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_add_symbol(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.add_symbol = AsyncMock(return_value=self._watchlist())
            result = await add_symbol(
                "abc", WatchlistSymbolRequest(symbol="AAPL"), current_user=mock_user
            )
        assert result.id == "abc"

    @pytest.mark.asyncio
    async def test_add_symbol_capacity_is_400(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.add_symbol = AsyncMock(side_effect=ValueError("at most"))
            with pytest.raises(HTTPException) as exc:
                await add_symbol("abc", WatchlistSymbolRequest(symbol="AAPL"), current_user=mock_user)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_remove_symbol_missing_is_404(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.remove_symbol = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await remove_symbol("abc", "AAPL", current_user=mock_user)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_watchlist_quotes(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.get_watchlist_quotes = AsyncMock(
                return_value=(self._watchlist(), [SymbolQuote(symbol="AAA", close=1.0)])
            )
            result = await get_watchlist_quotes("abc", current_user=mock_user)
        assert result.total_count == 1
        assert result.watchlist_id == "abc"

    @pytest.mark.asyncio
    async def test_watchlist_quotes_missing_is_404(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.get_watchlist_quotes = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await get_watchlist_quotes("abc", current_user=mock_user)
        assert exc.value.status_code == 404


class TestAlertRouter:
    def _alert(self) -> Alert:
        return Alert(
            id="a1", user_id="test_user_id", symbol="AAPL",
            alert_type=AlertType.PRICE_ABOVE, threshold=200.0,
        )

    @pytest.mark.asyncio
    async def test_list_alerts(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.list_alerts = AsyncMock(return_value=[self._alert()])
            result = await list_alerts(current_user=mock_user, active_only=True)
            service.list_alerts.assert_awaited_once_with("test_user_id", active_only=True)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_create_alert(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.create_alert = AsyncMock(return_value=self._alert())
            request = AlertCreateRequest(
                symbol="AAPL", alert_type=AlertType.PRICE_ABOVE, threshold=200
            )
            result = await create_alert(request, current_user=mock_user)
        assert result.symbol == "AAPL"

    @pytest.mark.asyncio
    async def test_create_alert_validation_is_400(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.create_alert = AsyncMock(side_effect=ValueError("requires a threshold"))
            request = AlertCreateRequest(symbol="AAPL", alert_type=AlertType.PRICE_ABOVE)
            with pytest.raises(HTTPException) as exc:
                await create_alert(request, current_user=mock_user)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_alert_missing_is_404(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.delete_alert = AsyncMock(return_value=False)
            with pytest.raises(HTTPException) as exc:
                await delete_alert("a1", current_user=mock_user)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_check_alerts(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.check_alerts = AsyncMock(return_value=AlertCheckResponse(checked=2))
            result = await check_alerts(current_user=mock_user)
        assert result.checked == 2

    @pytest.mark.asyncio
    async def test_check_alerts_wraps_errors(self, mock_user):
        with patch("app.routers.watchlist.watchlist_service") as service:
            service.check_alerts = AsyncMock(side_effect=RuntimeError("boom"))
            with pytest.raises(HTTPException) as exc:
                await check_alerts(current_user=mock_user)
        assert exc.value.status_code == 500
