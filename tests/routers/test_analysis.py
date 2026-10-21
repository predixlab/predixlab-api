"""Unit tests for the analysis router."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.market import (
    CorrelationResponse,
    IndicatorSeriesResponse,
    IndicatorSnapshot,
    PeerComparisonResponse,
    RiskMetrics,
    SeasonalityResponse,
    SignalStrength,
    SymbolProfileResponse,
    SymbolQuote,
    TechnicalAnalysisResponse,
    TrendDirection,
)
from app.routers.analysis import (
    _load_prediction,
    compare_peers,
    get_correlation_matrix,
    get_indicator_series,
    get_indicator_snapshot,
    get_risk_metrics,
    get_seasonality,
    get_symbol_profile,
    get_technical_analysis,
)


def _technical() -> TechnicalAnalysisResponse:
    quote = SymbolQuote(symbol="AAPL", close=100.0)
    return TechnicalAnalysisResponse(
        symbol="AAPL",
        date="2024-06-28",
        quote=quote,
        trend=TrendDirection.UPTREND,
        signal=SignalStrength.BULLISH,
        signal_score=0.4,
        narrative="looks fine",
    )


class TestTechnicalEndpoint:
    @pytest.mark.asyncio
    async def test_returns_analysis(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_technical_analysis = AsyncMock(return_value=_technical())
            result = await get_technical_analysis("aapl", current_user=mock_user)
            service.get_technical_analysis.assert_awaited_once_with("AAPL")
        assert result.signal == SignalStrength.BULLISH
        assert result.model_dump(mode="json")["signal"] == "bullish"

    @pytest.mark.asyncio
    async def test_missing_symbol_is_404(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_technical_analysis = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await get_technical_analysis("zzzz", current_user=mock_user)
        assert exc.value.status_code == 404


class TestIndicatorEndpoints:
    @pytest.mark.asyncio
    async def test_snapshot(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_indicator_snapshot = AsyncMock(
                return_value=IndicatorSnapshot(symbol="AAPL", values={"rsi_14": 55.0})
            )
            result = await get_indicator_snapshot("aapl", current_user=mock_user)
        assert result.values["rsi_14"] == 55.0

    @pytest.mark.asyncio
    async def test_snapshot_missing_is_404(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_indicator_snapshot = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await get_indicator_snapshot("zzzz", current_user=mock_user)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_series_lowercases_names(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_indicator_series = AsyncMock(
                return_value=IndicatorSeriesResponse(symbol="AAPL")
            )
            await get_indicator_series(
                "aapl", current_user=mock_user, names="SMA_20,RSI_14", limit=100
            )
            service.get_indicator_series.assert_awaited_once_with(
                "AAPL", ["sma_20", "rsi_14"], limit=100
            )

    @pytest.mark.asyncio
    async def test_series_without_names(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_indicator_series = AsyncMock(
                return_value=IndicatorSeriesResponse(symbol="AAPL")
            )
            await get_indicator_series("aapl", current_user=mock_user, names=None, limit=50)
            service.get_indicator_series.assert_awaited_once_with("AAPL", [], limit=50)

    @pytest.mark.asyncio
    async def test_series_missing_is_404(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_indicator_series = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await get_indicator_series("zzzz", current_user=mock_user, names=None, limit=50)
        assert exc.value.status_code == 404


class TestRiskEndpoint:
    @pytest.mark.asyncio
    async def test_risk_metrics(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_risk_metrics = AsyncMock(
                return_value=RiskMetrics(symbol="AAPL", window_days=252, metrics={"beta": 1.1})
            )
            result = await get_risk_metrics(
                "aapl", current_user=mock_user, window_days=252, benchmark="spy"
            )
            service.get_risk_metrics.assert_awaited_once_with(
                "AAPL", window_days=252, benchmark="SPY"
            )
        assert result.metrics["beta"] == 1.1

    @pytest.mark.asyncio
    async def test_risk_missing_is_404(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_risk_metrics = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await get_risk_metrics("zzzz", current_user=mock_user, window_days=252, benchmark=None)
        assert exc.value.status_code == 404


class TestPeerAndCorrelation:
    @pytest.mark.asyncio
    async def test_peers(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.compare_peers = AsyncMock(
                return_value=PeerComparisonResponse(symbol="AAPL", window_days=252)
            )
            await compare_peers("aapl", current_user=mock_user, peers="msft,goog", window_days=252)
            service.compare_peers.assert_awaited_once_with("AAPL", ["MSFT", "GOOG"], window_days=252)

    @pytest.mark.asyncio
    async def test_peers_requires_input(self, mock_user):
        with pytest.raises(HTTPException) as exc:
            await compare_peers("aapl", current_user=mock_user, peers=" ", window_days=252)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_peers_missing_symbol_is_404(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.compare_peers = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await compare_peers("zzzz", current_user=mock_user, peers="msft", window_days=252)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_correlation_requires_two_symbols(self, mock_user):
        with pytest.raises(HTTPException) as exc:
            await get_correlation_matrix(current_user=mock_user, symbols="aapl", window_days=252)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_correlation_delegates(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_correlation_matrix = AsyncMock(
                return_value=CorrelationResponse(symbols=["AAPL", "MSFT"], window_days=252)
            )
            result = await get_correlation_matrix(
                current_user=mock_user, symbols="aapl,msft", window_days=252
            )
        assert result.symbols == ["AAPL", "MSFT"]


class TestSeasonalityEndpoint:
    @pytest.mark.asyncio
    async def test_seasonality(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_seasonality = AsyncMock(
                return_value=SeasonalityResponse(symbol="AAPL", granularity="month")
            )
            result = await get_seasonality("aapl", current_user=mock_user, granularity="month")
        assert result.granularity == "month"

    @pytest.mark.asyncio
    async def test_seasonality_missing_is_404(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_seasonality = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await get_seasonality("zzzz", current_user=mock_user, granularity="month")
        assert exc.value.status_code == 404


class TestProfileEndpoint:
    @pytest.mark.asyncio
    async def test_profile_without_prediction(self, mock_user):
        technical = _technical()
        profile = SymbolProfileResponse(
            symbol="AAPL",
            quote=technical.quote,
            technical=technical,
            risk=RiskMetrics(symbol="AAPL", window_days=252),
        )
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_symbol_profile = AsyncMock(return_value=profile)
            result = await get_symbol_profile(
                "aapl", current_user=mock_user, benchmark=None, include_prediction=False
            )
            service.get_symbol_profile.assert_awaited_once_with(
                "AAPL", benchmark=None, prediction=None
            )
        assert result.symbol == "AAPL"

    @pytest.mark.asyncio
    async def test_profile_missing_is_404(self, mock_user):
        with patch("app.routers.analysis.analysis_service") as service:
            service.get_symbol_profile = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await get_symbol_profile(
                    "zzzz", current_user=mock_user, benchmark=None, include_prediction=False
                )
        assert exc.value.status_code == 404


class TestLoadPrediction:
    @pytest.mark.asyncio
    async def test_returns_matching_prediction(self, mock_user, mock_prediction):
        with patch("app.routers.analysis.predictions_service") as service:
            service.db = MagicMock()
            service.initialize = AsyncMock()
            service.get_latest_predictions = AsyncMock(return_value=[mock_prediction])
            result = await _load_prediction("AAPL", mock_user)
        assert result["instrument"] == "AAPL"

    @pytest.mark.asyncio
    async def test_returns_none_when_absent(self, mock_user, mock_prediction):
        with patch("app.routers.analysis.predictions_service") as service:
            service.db = MagicMock()
            service.get_latest_predictions = AsyncMock(return_value=[mock_prediction])
            assert await _load_prediction("MSFT", mock_user) is None

    @pytest.mark.asyncio
    async def test_swallows_service_errors(self, mock_user):
        with patch("app.routers.analysis.predictions_service") as service:
            service.db = MagicMock()
            service.get_latest_predictions = AsyncMock(side_effect=RuntimeError("boom"))
            assert await _load_prediction("AAPL", mock_user) is None
