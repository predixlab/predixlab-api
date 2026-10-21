"""Unit tests for the pivot picks router."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from app.routers.pivot import (
    get_latest_pivot_picks,
    get_latest_pivot_run,
    get_pivot_dates,
    get_pivot_performance,
    get_pivot_performance_breakdown,
    get_pivot_pick_for_symbol,
    get_pivot_picks,
    get_pivot_summary,
    get_pivot_symbol_history,
)
from app.models.pivot import (
    PickDirection,
    PickEvent,
    PivotPerformance,
    PivotPerformanceBreakdown,
    PivotPick,
    PivotRun,
    PivotSummary,
)


def _pick(symbol: str = "AAPL", direction: str = "long", rank: int = 1, action: str = "STRONG_BUY"):
    return PivotPick(
        date="2024-01-02",
        market="us",
        symbol=symbol,
        event="BUY_TURN" if direction == "long" else "SELL_TURN",
        kind="reversal",
        direction=direction,
        rank=rank,
        horizon=10,
        action=action,
        score=0.31 if direction == "long" else -0.31,
        confidence=0.98,
        swing_position=0.12,
        p_up=0.61,
        rationale="RSI is oversold",
        close=1.42,
        price=189.5,
    )


def _old_date() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")


def _recent_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class TestGetPivotDates:
    @pytest.mark.asyncio
    async def test_returns_dates(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_available_dates = AsyncMock(return_value=["2024-01-03", "2024-01-02"])

            result = await get_pivot_dates(mock_premium_user, market="us", limit=60)

        assert result.count == 2
        assert result.dates[0] == "2024-01-03"

    @pytest.mark.asyncio
    async def test_free_user_flag_is_forwarded(self, mock_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_available_dates = AsyncMock(return_value=[])

            await get_pivot_dates(mock_user, market="us", limit=10)

        assert service.get_available_dates.call_args.kwargs["is_premium"] is False

    @pytest.mark.asyncio
    async def test_service_failure_is_a_500(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_available_dates = AsyncMock(side_effect=RuntimeError("boom"))

            with pytest.raises(HTTPException) as excinfo:
                await get_pivot_dates(mock_premium_user, market="us", limit=60)

        assert excinfo.value.status_code == 500


class TestGetLatestPivotPicks:
    @pytest.mark.asyncio
    async def test_splits_the_two_sides(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_latest_date = AsyncMock(return_value="2024-01-02")
            service.get_picks_by_date = AsyncMock(
                return_value=[_pick(), _pick("OGN", "short", 1, "STRONG_SELL")]
            )
            service.count_events = AsyncMock(return_value={"BUY_TURN": 1, "SELL_TURN": 1})

            result = await get_latest_pivot_picks(
                mock_premium_user, market="us", direction=None, event=None, limit=100
            )

        assert result.date == "2024-01-02"
        assert result.horizon == 10
        assert result.total_count == 2
        assert [p.symbol for p in result.longs] == ["AAPL"]
        assert [p.symbol for p in result.shorts] == ["OGN"]
        payload = result.model_dump(mode="json")
        assert payload["longs"][0]["action"] == "STRONGLY_BULLISH"
        assert payload["shorts"][0]["action"] == "STRONGLY_BEARISH"

    @pytest.mark.asyncio
    async def test_direction_filter_is_forwarded(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_latest_date = AsyncMock(return_value="2024-01-02")
            service.get_picks_by_date = AsyncMock(return_value=[])
            service.count_events = AsyncMock(return_value={})

            await get_latest_pivot_picks(
                mock_premium_user,
                market="us",
                direction=PickDirection.LONG,
                event=None,
                limit=20,
            )

        assert service.get_picks_by_date.call_args.kwargs["direction"] == "long"

    @pytest.mark.asyncio
    async def test_missing_data_is_a_404(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_latest_date = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as excinfo:
                await get_latest_pivot_picks(
                    mock_premium_user, market="us", direction=None, event=None, limit=100
                )

        assert excinfo.value.status_code == 404


class TestGetPivotPicksByDate:
    @pytest.mark.asyncio
    async def test_premium_user_reads_a_recent_session(self, mock_premium_user):
        date = _recent_date()
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_picks_by_date = AsyncMock(return_value=[_pick()])
            service.count_events = AsyncMock(return_value={"BUY_TURN": 1})

            result = await get_pivot_picks(
                date=date,
                current_user=mock_premium_user,
                market="us",
                direction=None,
                event=None,
                limit=100,
            )

        assert result.date == date
        assert len(result.longs) == 1

    @pytest.mark.asyncio
    async def test_free_user_is_blocked_from_recent_sessions(self, mock_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_picks_by_date = AsyncMock(return_value=[_pick()])

            with pytest.raises(HTTPException) as excinfo:
                await get_pivot_picks(
                    date=_recent_date(),
                    current_user=mock_user,
                    market="us",
                    direction=None,
                    event=None,
                    limit=100,
                )

        assert excinfo.value.status_code == 403
        service.get_picks_by_date.assert_not_called()

    @pytest.mark.asyncio
    async def test_free_user_reads_an_old_session(self, mock_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_picks_by_date = AsyncMock(return_value=[_pick()])
            service.count_events = AsyncMock(return_value={"BUY_TURN": 1})

            result = await get_pivot_picks(
                date=_old_date(),
                current_user=mock_user,
                market="us",
                direction=None,
                event=None,
                limit=100,
            )

        assert result.total_count == 1


class TestQuietSessions:
    """Most sessions fire nothing, and that must render as a real answer."""

    @pytest.mark.asyncio
    async def test_a_session_with_no_events_is_a_200(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_picks_by_date = AsyncMock(return_value=[])
            service.count_events = AsyncMock(return_value={})

            result = await get_pivot_picks(
                date=_old_date(),
                current_user=mock_premium_user,
                market="us",
                direction=None,
                event=None,
                limit=100,
            )

        assert result.total_count == 0
        assert result.longs == [] and result.shorts == []
        assert result.counts == {}

    @pytest.mark.asyncio
    async def test_event_filter_is_forwarded(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_picks_by_date = AsyncMock(return_value=[])
            service.count_events = AsyncMock(return_value={})

            await get_pivot_picks(
                date=_old_date(),
                current_user=mock_premium_user,
                market="us",
                direction=None,
                event=PickEvent.BASE_BREAKOUT,
                limit=100,
            )

        assert service.get_picks_by_date.call_args.kwargs["event"] == "BASE_BREAKOUT"


class TestGetPivotPickForSymbol:
    @pytest.mark.asyncio
    async def test_returns_the_pick(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_pick_for_symbol = AsyncMock(return_value=_pick("MSFT"))

            result = await get_pivot_pick_for_symbol(
                date="2024-01-02", symbol="msft", current_user=mock_premium_user, market="us"
            )

        assert result.symbol == "MSFT"

    @pytest.mark.asyncio
    async def test_unpicked_symbol_is_a_404(self, mock_premium_user):
        """A symbol that was scored but not selected is genuinely not there."""
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_pick_for_symbol = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as excinfo:
                await get_pivot_pick_for_symbol(
                    date="2024-01-02", symbol="NOPE", current_user=mock_premium_user, market="us"
                )

        assert excinfo.value.status_code == 404


class TestGetPivotSymbolHistory:
    @pytest.mark.asyncio
    async def test_returns_history(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_symbol_history = AsyncMock(return_value=[_pick(), _pick()])

            result = await get_pivot_symbol_history(
                "aapl", mock_premium_user, market="us", limit=120
            )

        assert result.symbol == "AAPL"
        assert result.count == 2


class TestGetPivotSummary:
    @pytest.mark.asyncio
    async def test_returns_summary(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_summary = AsyncMock(
                return_value=PivotSummary(
                    market="us",
                    date="2024-01-02",
                    horizon=10,
                    long_count=20,
                    short_count=20,
                    top_longs=[_pick()],
                    top_shorts=[_pick("OGN", "short", 1, "STRONG_SELL")],
                )
            )

            result = await get_pivot_summary(mock_premium_user, market="us")

        assert result.long_count == 20
        assert result.top_shorts[0].symbol == "OGN"


class TestGetPivotPerformance:
    @pytest.mark.asyncio
    async def test_returns_performance(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_performance = AsyncMock(
                return_value=PivotPerformance(
                    market="us",
                    horizon=10,
                    evaluated=6738,
                    pending=92,
                    win_rate=0.503,
                    avg_return=0.0011,
                    avg_excess_return=0.0014,
                )
            )

            result = await get_pivot_performance(
                mock_premium_user, market="us", direction=None, event=None
            )

        assert result.evaluated == 6738
        assert result.pending == 92
        assert result.avg_excess_return == pytest.approx(0.0014)

    @pytest.mark.asyncio
    async def test_direction_is_forwarded(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_performance = AsyncMock(return_value=PivotPerformance(market="us"))

            await get_pivot_performance(
                mock_premium_user, market="us", direction=PickDirection.SHORT, event=None
            )

        assert service.get_performance.call_args.kwargs["direction"] == "short"


class TestGetPivotPerformanceBreakdown:
    @pytest.mark.asyncio
    async def test_returns_overall_and_per_event(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_performance_breakdown = AsyncMock(
                return_value=PivotPerformanceBreakdown(
                    market="us",
                    horizon=10,
                    by_event=[
                        PivotPerformance(
                            market="us",
                            event="BUY_TURN",
                            evaluated=3070,
                            detection_rate=0.566,
                            detection_lift=3.09,
                        ),
                        PivotPerformance(market="us", event="TREND_BUY", evaluated=439),
                    ],
                )
            )

            result = await get_pivot_performance_breakdown(mock_premium_user, market="us")

        assert [i.event for i in result.by_event] == ["BUY_TURN", "TREND_BUY"]
        assert result.by_event[0].detection_lift == pytest.approx(3.09)

    @pytest.mark.asyncio
    async def test_there_is_no_combined_figure(self, mock_premium_user):
        """Averaging longs and shorts would describe the mix, not the system."""
        assert "overall" not in PivotPerformanceBreakdown.model_fields

    @pytest.mark.asyncio
    async def test_trend_entries_carry_no_detection_score(self, mock_premium_user):
        """Scoring a breakout against pivot labels asks it the wrong question."""
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_performance_breakdown = AsyncMock(
                return_value=PivotPerformanceBreakdown(
                    market="us",
                    by_event=[PivotPerformance(market="us", event="TREND_BUY", evaluated=439)],
                )
            )

            result = await get_pivot_performance_breakdown(mock_premium_user, market="us")

        assert result.by_event[0].detection_rate is None


class TestGetLatestPivotRun:
    @pytest.mark.asyncio
    async def test_returns_run(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_latest_run = AsyncMock(
                return_value=PivotRun(
                    market="us", date="2024-01-02", status="success", published=40
                )
            )

            result = await get_latest_pivot_run(mock_premium_user, market="us")

        assert result.status == "success"
        assert result.published == 40

    @pytest.mark.asyncio
    async def test_no_runs_is_a_404(self, mock_premium_user):
        with patch("app.routers.pivot.pivot_service") as service:
            service.db = MagicMock()
            service.get_latest_run = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as excinfo:
                await get_latest_pivot_run(mock_premium_user, market="us")

        assert excinfo.value.status_code == 404
