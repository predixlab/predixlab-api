"""Focused router tests for the combined signal board."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user
from app.models.alpharank import AlphaRankSignal
from app.models.pivot import PivotPick
from app.routers import signals
from app.routers.signals import (
    get_alpharank_signals_by_date,
    get_alpharank_ticker_history,
    get_latest_alpharank_signals,
    get_latest_signals,
    get_signals_by_date,
)


def _alpha(symbol: str = "AAPL", rank: int = 1) -> AlphaRankSignal:
    return AlphaRankSignal(
        market="us",
        date="2024-01-02",
        symbol=symbol,
        rank=rank,
        horizon=10,
        tag="daily",
        generated_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
        score=0.95,
        consensus=0.81,
        predicted_excess_return=0.034,
        predicted_rank=0.873,
        top20_probability=0.93,
        close=189.5,
    )


def _pivot(symbol: str = "MSFT", rank: int = 1) -> PivotPick:
    return PivotPick(
        market="us",
        date="2024-01-02",
        symbol=symbol,
        event="BUY_TURN",
        kind="reversal",
        direction="long",
        rank=rank,
        horizon=10,
        action="STRONG_BUY",
        score=0.3,
        confidence=0.9,
    )


def _services(picks=None, alpha_signals=None):
    pivot = MagicMock()
    pivot.db = MagicMock()
    pivot.get_picks_by_date = AsyncMock(return_value=picks or [])
    pivot.count_events = AsyncMock(
        return_value={"BUY_TURN": len(picks)} if picks else {}
    )
    pivot.get_latest_date = AsyncMock(return_value="2024-01-02")
    pivot.get_available_dates = AsyncMock(return_value=["2024-01-02"])

    alpharank = MagicMock()
    alpharank.db = MagicMock()
    alpharank.get_signals_by_date = AsyncMock(return_value=alpha_signals or [])
    alpharank.get_latest_date = AsyncMock(return_value="2024-01-02")
    alpharank.get_available_dates = AsyncMock(return_value=["2024-01-02"])
    alpharank.get_ticker_history = AsyncMock(return_value=alpha_signals or [])
    return pivot, alpharank


class TestCombinedSignals:
    @pytest.mark.asyncio
    async def test_latest_combines_both_typed_sections(self, mock_premium_user):
        pivot, alpharank = _services([_pivot()], [_alpha()])
        with (
            patch("app.routers.signals.pivot_service", pivot),
            patch("app.routers.signals.alpharank_service", alpharank),
        ):
            result = await get_latest_signals(mock_premium_user, market="us")

        assert result.date == "2024-01-02"
        assert result.pivot.longs[0].symbol == "MSFT"
        assert result.model_dump(mode="json")["pivot"]["longs"][0]["action"] == "STRONGLY_BULLISH"
        assert result.alpharank.items[0].symbol == "AAPL"
        assert pivot.get_picks_by_date.call_args.kwargs["strict"] is True
        assert pivot.count_events.call_args.kwargs["strict"] is True

    @pytest.mark.asyncio
    async def test_alpha_date_with_no_pivot_events_returns_real_empty_pivot_section(
        self, mock_premium_user
    ):
        pivot, alpharank = _services([], [_alpha()])
        with (
            patch("app.routers.signals.pivot_service", pivot),
            patch("app.routers.signals.alpharank_service", alpharank),
        ):
            result = await get_signals_by_date(
                "2024-01-02", mock_premium_user, market="us"
            )

        assert result.pivot.total_count == 0
        assert result.pivot.longs == []
        assert result.alpharank.total_count == 1

    @pytest.mark.asyncio
    async def test_missing_alpha_is_null_not_an_empty_success_shaped_section(
        self, mock_premium_user
    ):
        pivot, alpharank = _services([_pivot()], [])
        with (
            patch("app.routers.signals.pivot_service", pivot),
            patch("app.routers.signals.alpharank_service", alpharank),
        ):
            result = await get_signals_by_date(
                "2024-01-02", mock_premium_user, market="us"
            )

        assert result.pivot.total_count == 1
        assert result.alpharank is None

    @pytest.mark.asyncio
    async def test_latest_returns_404_when_both_sources_are_missing(self, mock_premium_user):
        pivot, alpharank = _services()
        pivot.get_latest_date = AsyncMock(return_value=None)
        alpharank.get_latest_date = AsyncMock(return_value=None)
        with (
            patch("app.routers.signals.pivot_service", pivot),
            patch("app.routers.signals.alpharank_service", alpharank),
            pytest.raises(HTTPException) as excinfo,
        ):
            await get_latest_signals(mock_premium_user, market="us")

        assert excinfo.value.status_code == 404

    @pytest.mark.asyncio
    async def test_combined_alpha_query_failure_is_a_500(self, mock_premium_user):
        pivot, alpharank = _services([_pivot()], [_alpha()])
        alpharank.get_signals_by_date = AsyncMock(side_effect=RuntimeError("mongo down"))
        with (
            patch("app.routers.signals.pivot_service", pivot),
            patch("app.routers.signals.alpharank_service", alpharank),
            pytest.raises(HTTPException) as excinfo,
        ):
            await get_signals_by_date("2024-01-02", mock_premium_user, market="us")

        assert excinfo.value.status_code == 500

    @pytest.mark.asyncio
    async def test_combined_pivot_query_failure_is_a_500(self, mock_premium_user):
        pivot, alpharank = _services([_pivot()], [_alpha()])
        pivot.get_picks_by_date = AsyncMock(side_effect=RuntimeError("mongo down"))
        with (
            patch("app.routers.signals.pivot_service", pivot),
            patch("app.routers.signals.alpharank_service", alpharank),
            pytest.raises(HTTPException) as excinfo,
        ):
            await get_signals_by_date("2024-01-02", mock_premium_user, market="us")

        assert excinfo.value.status_code == 500

    @pytest.mark.asyncio
    async def test_combined_pivot_count_failure_is_a_500(self, mock_premium_user):
        pivot, alpharank = _services([_pivot()], [_alpha()])
        pivot.count_events = AsyncMock(side_effect=RuntimeError("mongo down"))
        with (
            patch("app.routers.signals.pivot_service", pivot),
            patch("app.routers.signals.alpharank_service", alpharank),
            pytest.raises(HTTPException) as excinfo,
        ):
            await get_signals_by_date("2024-01-02", mock_premium_user, market="us")

        assert excinfo.value.status_code == 500


class TestSignalVisibility:
    @pytest.mark.asyncio
    async def test_latest_alpha_uses_the_latest_visible_date(self, mock_premium_user):
        _, alpharank = _services(alpha_signals=[_alpha()])
        alpharank.get_latest_date = AsyncMock(return_value="2024-01-02")
        with patch("app.routers.signals.alpharank_service", alpharank):
            result = await get_latest_alpharank_signals(mock_premium_user, market="us")

        assert result.date == "2024-01-02"
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_latest_alpha_query_failure_is_a_500(self, mock_premium_user):
        _, alpharank = _services()
        alpharank.get_latest_date = AsyncMock(side_effect=RuntimeError("mongo down"))
        with (
            patch("app.routers.signals.alpharank_service", alpharank),
            pytest.raises(HTTPException) as excinfo,
        ):
            await get_latest_alpharank_signals(mock_premium_user, market="us")

        assert excinfo.value.status_code == 500

    @pytest.mark.asyncio
    async def test_free_user_cannot_read_recent_combined_date(self, mock_user):
        pivot, alpharank = _services([_pivot()], [_alpha()])
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with (
            patch("app.routers.signals.pivot_service", pivot),
            patch("app.routers.signals.alpharank_service", alpharank),
            pytest.raises(HTTPException) as excinfo,
        ):
            await get_signals_by_date(recent, mock_user, market="us")

        assert excinfo.value.status_code == 403
        pivot.get_picks_by_date.assert_not_called()
        alpharank.get_signals_by_date.assert_not_called()

    @pytest.mark.asyncio
    async def test_premium_user_can_read_recent_alpha_date(self, mock_premium_user):
        _, alpharank = _services(alpha_signals=[_alpha()])
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with patch("app.routers.signals.alpharank_service", alpharank):
            result = await get_alpharank_signals_by_date(
                recent, mock_premium_user, market="us"
            )

        assert result.total_count == 1
        assert alpharank.get_signals_by_date.call_args.args[2] is True


class TestAlphaRankTickerHistory:
    @pytest.mark.asyncio
    async def test_returns_typed_oldest_first_history(self, mock_premium_user):
        _, alpharank = _services(alpha_signals=[_alpha("AAPL", 2), _alpha("AAPL", 1)])
        alpharank.get_ticker_history = AsyncMock(
            return_value=[
                _alpha("AAPL", 1),
                _alpha("AAPL", 2),
            ]
        )
        with patch("app.routers.signals.alpharank_service", alpharank):
            result = await get_alpharank_ticker_history(
                "aapl", mock_premium_user, market="us", limit=120
            )

        assert result.symbol == "AAPL"
        assert result.count == 2
        assert alpharank.get_ticker_history.call_args.kwargs["is_premium"] is True

    @pytest.mark.asyncio
    async def test_history_failure_is_a_500(self, mock_premium_user):
        _, alpharank = _services()
        alpharank.get_ticker_history = AsyncMock(side_effect=RuntimeError("mongo down"))
        with (
            patch("app.routers.signals.alpharank_service", alpharank),
            pytest.raises(HTTPException) as excinfo,
        ):
            await get_alpharank_ticker_history(
                "AAPL", mock_premium_user, market="us", limit=120
            )

        assert excinfo.value.status_code == 500


def test_static_dates_route_precedes_parameterized_date_route(mock_premium_user):
    app = FastAPI()
    app.include_router(signals.router)
    app.dependency_overrides[get_current_user] = lambda: mock_premium_user
    pivot, alpharank = _services()
    with (
        patch("app.routers.signals.pivot_service", pivot),
        patch("app.routers.signals.alpharank_service", alpharank),
    ):
        response = TestClient(app).get("/signals/dates")

    assert response.status_code == 200
    assert response.json()["dates"] == ["2024-01-02"]


def test_ticker_history_route_precedes_alpharank_date_route(mock_premium_user):
    app = FastAPI()
    app.include_router(signals.router)
    app.dependency_overrides[get_current_user] = lambda: mock_premium_user
    _, alpharank = _services(alpha_signals=[_alpha()])
    with patch("app.routers.signals.alpharank_service", alpharank):
        response = TestClient(app).get("/signals/alpharank/tickers/aapl/history")

    assert response.status_code == 200
    assert response.json()["symbol"] == "AAPL"


def test_legacy_pivot_route_remains_registered():
    from app.main import app

    assert "/api/v1/pivot/picks/latest" in app.openapi()["paths"]
