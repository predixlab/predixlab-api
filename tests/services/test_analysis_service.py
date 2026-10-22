"""Unit tests for the per-symbol analysis service."""
import pytest

from app.models.market import RiskLevel, SignalStrength, TechnicalAnalysisResponse, TrendDirection
from app.services.analysis_service import (
    AnalysisService,
    aggregate_signal_score,
    build_signals,
    classify_risk_level,
    classify_signal,
    classify_trend,
)
from app.models.market import SymbolQuote


@pytest.mark.parametrize("legacy,new", [
    ("strong_buy", "strongly_bullish"),
    ("buy", "bullish"),
    ("neutral", "neutral"),
    ("sell", "bearish"),
    ("strong_sell", "strongly_bearish"),
])
def test_technical_signal_inputs_normalize_and_emit_directional_values(legacy, new):
    for value in (legacy, new):
        result = TechnicalAnalysisResponse(
            symbol="AAPL",
            quote=SymbolQuote(symbol="AAPL"),
            trend=TrendDirection.NEUTRAL,
            signal=value,
            signal_score=0,
        )
        assert result.model_dump(mode="json")["signal"] == new
    assert TechnicalAnalysisResponse.model_json_schema()["$defs"]["SignalStrength"]["enum"] == [
        "strongly_bullish", "bullish", "neutral", "bearish", "strongly_bearish"
    ]


@pytest.fixture
def service(synthetic_market):
    repository, _ = synthetic_market
    return AnalysisService(repository=repository)


class TestQuotes:
    @pytest.mark.asyncio
    async def test_quote_fields_populated(self, service, synthetic_market):
        _, symbols = synthetic_market
        quote = await service.get_quote(symbols[0])
        assert quote.symbol == symbols[0]
        assert quote.close > 0
        assert quote.change_percent is not None
        assert quote.week52_high >= quote.week52_low

    @pytest.mark.asyncio
    async def test_quote_none_for_unknown_symbol(self, service):
        assert await service.get_quote("ZZZZ") is None

    @pytest.mark.asyncio
    async def test_get_quotes_skips_unknown_symbols(self, service, synthetic_market):
        _, symbols = synthetic_market
        quotes = await service.get_quotes(symbols[:2] + ["ZZZZ"])
        assert [q.symbol for q in quotes] == symbols[:2]


class TestIndicators:
    @pytest.mark.asyncio
    async def test_snapshot_contains_core_indicators(self, service, synthetic_market):
        _, symbols = synthetic_market
        snapshot = await service.get_indicator_snapshot(symbols[0])
        assert snapshot.symbol == symbols[0]
        assert snapshot.values["rsi_14"] is not None
        assert snapshot.values["sma_200"] is not None

    @pytest.mark.asyncio
    async def test_snapshot_none_for_unknown_symbol(self, service):
        assert await service.get_indicator_snapshot("ZZZZ") is None

    @pytest.mark.asyncio
    async def test_series_returns_requested_indicators(self, service, synthetic_market):
        _, symbols = synthetic_market
        series = await service.get_indicator_series(symbols[0], ["sma_20", "rsi_14"], limit=30)
        assert series.indicators == ["sma_20", "rsi_14"]
        assert len(series.points) == 30
        assert set(series.points[-1].values) == {"sma_20", "rsi_14"}

    @pytest.mark.asyncio
    async def test_series_falls_back_to_defaults(self, service, synthetic_market):
        _, symbols = synthetic_market
        series = await service.get_indicator_series(symbols[0], ["not_an_indicator"], limit=10)
        assert series.indicators == ["sma_20", "sma_50", "rsi_14", "macd"]

    @pytest.mark.asyncio
    async def test_series_none_for_unknown_symbol(self, service):
        assert await service.get_indicator_series("ZZZZ", ["sma_20"]) is None


class TestTechnicalAnalysis:
    @pytest.mark.asyncio
    async def test_analysis_structure(self, service, synthetic_market):
        _, symbols = synthetic_market
        result = await service.get_technical_analysis(symbols[0])
        assert result.symbol == symbols[0]
        assert isinstance(result.trend, TrendDirection)
        assert isinstance(result.signal, SignalStrength)
        assert -1.0 <= result.signal_score <= 1.0
        assert result.signals
        assert result.narrative

    @pytest.mark.asyncio
    async def test_levels_bracket_last_close(self, service, synthetic_market):
        _, symbols = synthetic_market
        result = await service.get_technical_analysis(symbols[0])
        close = result.quote.close
        assert all(level < close for level in result.levels.support)
        assert all(level > close for level in result.levels.resistance)
        assert result.levels.pivot["pivot"] > 0

    @pytest.mark.asyncio
    async def test_performance_windows_present(self, service, synthetic_market):
        _, symbols = synthetic_market
        result = await service.get_technical_analysis(symbols[0])
        assert set(result.performance) == {"1d", "5d", "1m", "3m", "6m", "1y"}

    @pytest.mark.asyncio
    async def test_narrative_includes_disclaimer(self, service, synthetic_market):
        _, symbols = synthetic_market
        result = await service.get_technical_analysis(symbols[0])
        assert "not investment advice" in result.narrative

    @pytest.mark.asyncio
    async def test_analysis_none_for_unknown_symbol(self, service):
        assert await service.get_technical_analysis("ZZZZ") is None


class TestRisk:
    @pytest.mark.asyncio
    async def test_risk_metrics_populated(self, service, synthetic_market):
        _, symbols = synthetic_market
        risk = await service.get_risk_metrics(symbols[0], window_days=252)
        assert risk.metrics["annualized_volatility"] > 0
        assert risk.metrics["max_drawdown"] <= 0
        assert isinstance(risk.risk_level, RiskLevel)

    @pytest.mark.asyncio
    async def test_risk_metrics_with_benchmark(self, service, synthetic_market):
        _, symbols = synthetic_market
        risk = await service.get_risk_metrics(symbols[0], 252, benchmark=symbols[1])
        assert risk.benchmark == symbols[1]
        assert risk.metrics["beta"] is not None

    @pytest.mark.asyncio
    async def test_risk_metrics_none_for_unknown_symbol(self, service):
        assert await service.get_risk_metrics("ZZZZ") is None


class TestPeersAndCorrelation:
    @pytest.mark.asyncio
    async def test_peer_comparison_excludes_self(self, service, synthetic_market):
        _, symbols = synthetic_market
        result = await service.compare_peers(symbols[0], symbols, window_days=252)
        assert symbols[0] not in [p.symbol for p in result.peers]
        assert result.base.symbol == symbols[0]

    @pytest.mark.asyncio
    async def test_peer_relative_strength_is_computed(self, service, synthetic_market):
        _, symbols = synthetic_market
        result = await service.compare_peers(symbols[0], symbols[1:], window_days=252)
        assert result.base.relative_strength is not None
        assert all(p.relative_strength is not None for p in result.peers)

    @pytest.mark.asyncio
    async def test_peer_comparison_none_for_unknown_symbol(self, service, synthetic_market):
        _, symbols = synthetic_market
        assert await service.compare_peers("ZZZZ", symbols) is None

    @pytest.mark.asyncio
    async def test_correlation_matrix_is_symmetric(self, service, synthetic_market):
        _, symbols = synthetic_market
        result = await service.get_correlation_matrix(symbols, window_days=120)
        for a in result.symbols:
            assert result.matrix[a][a] == pytest.approx(1.0)
            for b in result.symbols:
                assert result.matrix[a][b] == pytest.approx(result.matrix[b][a])

    @pytest.mark.asyncio
    async def test_correlation_matrix_empty_for_unknown_symbols(self, service):
        result = await service.get_correlation_matrix(["ZZZZ", "YYYY"])
        assert result.matrix == {}


class TestSeasonality:
    @pytest.mark.asyncio
    async def test_monthly_seasonality_labels(self, service, synthetic_market):
        _, symbols = synthetic_market
        result = await service.get_seasonality(symbols[0], "month")
        assert result.granularity == "month"
        assert all(bucket.label.isalpha() for bucket in result.buckets)

    @pytest.mark.asyncio
    async def test_weekday_seasonality_labels(self, service, synthetic_market):
        _, symbols = synthetic_market
        result = await service.get_seasonality(symbols[0], "weekday")
        assert {b.label for b in result.buckets} <= {
            "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
        }

    @pytest.mark.asyncio
    async def test_seasonality_none_for_unknown_symbol(self, service):
        assert await service.get_seasonality("ZZZZ") is None


class TestProfile:
    @pytest.mark.asyncio
    async def test_profile_bundles_sections(self, service, synthetic_market):
        _, symbols = synthetic_market
        profile = await service.get_symbol_profile(symbols[0])
        assert profile.quote.symbol == symbols[0]
        assert profile.technical.narrative
        assert profile.risk.metrics
        assert profile.performance

    @pytest.mark.asyncio
    async def test_profile_attaches_prediction(self, service, synthetic_market):
        _, symbols = synthetic_market
        payload = {"instrument": symbols[0], "us_rise_1d": 0.7}
        profile = await service.get_symbol_profile(symbols[0], prediction=payload)
        assert profile.prediction == payload

    @pytest.mark.asyncio
    async def test_profile_none_for_unknown_symbol(self, service):
        assert await service.get_symbol_profile("ZZZZ") is None


class TestRuleEngine:
    def test_trend_uptrend_when_price_above_all_averages(self):
        snapshot = {"close": 120, "sma_20": 110, "sma_50": 105, "sma_200": 100, "adx_14": 30}
        assert classify_trend(snapshot) == TrendDirection.STRONG_UPTREND

    def test_trend_downtrend_when_price_below_all_averages(self):
        snapshot = {"close": 80, "sma_20": 90, "sma_50": 95, "sma_200": 100, "adx_14": 30}
        assert classify_trend(snapshot) == TrendDirection.STRONG_DOWNTREND

    def test_trend_neutral_without_averages(self):
        assert classify_trend({"close": 100}) == TrendDirection.NEUTRAL

    def test_trend_neutral_without_close(self):
        assert classify_trend({"sma_20": 100}) == TrendDirection.NEUTRAL

    def test_weak_adx_downgrades_to_plain_uptrend(self):
        snapshot = {"close": 120, "sma_20": 110, "sma_50": 105, "sma_200": 100, "adx_14": 10}
        assert classify_trend(snapshot) == TrendDirection.UPTREND

    def test_signals_flag_overbought_rsi(self):
        snapshot = {"close": 100, "rsi_14": 80}
        quote = SymbolQuote(symbol="AAA", close=100)
        signals = build_signals(snapshot, quote)
        rsi_signal = next(s for s in signals if s.name == "RSI(14)")
        assert rsi_signal.verdict == "overbought"
        assert rsi_signal.bullish is False

    def test_signals_flag_oversold_rsi(self):
        signals = build_signals({"close": 100, "rsi_14": 20}, SymbolQuote(symbol="AAA", close=100))
        rsi_signal = next(s for s in signals if s.name == "RSI(14)")
        assert rsi_signal.verdict == "oversold"
        assert rsi_signal.bullish is True

    def test_signals_detect_golden_cross(self):
        snapshot = {"close": 100, "sma_50": 95, "sma_200": 90}
        signals = build_signals(snapshot, SymbolQuote(symbol="AAA", close=100))
        cross = next(s for s in signals if s.name == "Golden/Death cross")
        assert cross.verdict == "golden_cross"

    def test_signals_detect_volume_spike(self):
        snapshot = {"close": 100, "volume_ratio": 3.0}
        quote = SymbolQuote(symbol="AAA", close=100, change_percent=0.05)
        signals = build_signals(snapshot, quote)
        spike = next(s for s in signals if s.name == "Volume")
        assert spike.verdict == "spike"
        assert spike.bullish is True

    def test_aggregate_score_all_bullish(self):
        signals = build_signals(
            {"close": 120, "sma_20": 110, "sma_50": 105, "sma_200": 100},
            SymbolQuote(symbol="AAA", close=120),
        )
        assert aggregate_signal_score(signals) == pytest.approx(1.0)

    def test_aggregate_score_empty_is_zero(self):
        assert aggregate_signal_score([]) == 0.0

    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.8, SignalStrength.STRONGLY_BULLISH),
            (0.3, SignalStrength.BULLISH),
            (0.0, SignalStrength.NEUTRAL),
            (-0.3, SignalStrength.BEARISH),
            (-0.9, SignalStrength.STRONGLY_BEARISH),
        ],
    )
    def test_classify_signal_buckets(self, score, expected):
        assert classify_signal(score) == expected

    @pytest.mark.parametrize(
        "volatility,expected",
        [
            (0.10, RiskLevel.LOW),
            (0.25, RiskLevel.MODERATE),
            (0.45, RiskLevel.HIGH),
            (0.80, RiskLevel.VERY_HIGH),
            (None, None),
        ],
    )
    def test_classify_risk_level_buckets(self, volatility, expected):
        assert classify_risk_level(volatility) == expected
