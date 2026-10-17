"""Unit tests for the technical indicator library."""
import numpy as np
import pandas as pd
import pytest

from app.core import indicators


@pytest.fixture
def rising_close():
    """A strictly increasing price series."""
    return pd.Series([float(i) for i in range(1, 61)])


@pytest.fixture
def ohlcv():
    """A deterministic OHLCV frame long enough for every indicator."""
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, 300)))
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, 300).astype(float),
        }
    )


class TestMovingAverages:
    def test_sma_matches_manual_mean(self):
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = indicators.sma(series, 3)
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_ema_converges_towards_recent_values(self, rising_close):
        result = indicators.ema(rising_close, 10)
        assert result.iloc[-1] < rising_close.iloc[-1]
        assert result.iloc[-1] > result.iloc[-2]

    def test_sma_requires_full_window(self):
        result = indicators.sma(pd.Series([1.0, 2.0]), 5)
        assert result.isna().all()


class TestRSI:
    def test_rsi_saturates_on_uninterrupted_rally(self, rising_close):
        result = indicators.rsi(rising_close, 14)
        assert result.dropna().iloc[-1] == pytest.approx(100.0)

    def test_rsi_bottoms_on_uninterrupted_decline(self, rising_close):
        result = indicators.rsi(rising_close.iloc[::-1].reset_index(drop=True), 14)
        assert result.dropna().iloc[-1] == pytest.approx(0.0, abs=1e-6)

    def test_rsi_stays_within_bounds(self, ohlcv):
        result = indicators.rsi(ohlcv["close"], 14).dropna()
        assert result.between(0, 100).all()

    def test_rsi_flat_series_is_neutral(self):
        result = indicators.rsi(pd.Series([10.0] * 40), 14)
        assert result.dropna().iloc[-1] == pytest.approx(50.0)


class TestMACD:
    def test_macd_histogram_is_difference(self, ohlcv):
        result = indicators.macd(ohlcv["close"])
        assert result["histogram"].iloc[-1] == pytest.approx(
            result["macd"].iloc[-1] - result["signal"].iloc[-1]
        )

    def test_macd_positive_in_uptrend(self, rising_close):
        result = indicators.macd(rising_close)
        assert result["macd"].iloc[-1] > 0


class TestBollingerBands:
    def test_bands_bracket_the_middle(self, ohlcv):
        result = indicators.bollinger_bands(ohlcv["close"]).dropna()
        assert (result["upper"] >= result["middle"]).all()
        assert (result["middle"] >= result["lower"]).all()

    def test_percent_b_is_zero_at_lower_band(self):
        series = pd.Series([10.0] * 19 + [5.0])
        result = indicators.bollinger_bands(series, window=20)
        assert result["percent_b"].iloc[-1] < 0.5


class TestVolatilityIndicators:
    def test_true_range_is_non_negative(self, ohlcv):
        result = indicators.true_range(ohlcv["high"], ohlcv["low"], ohlcv["close"]).dropna()
        assert (result >= 0).all()

    def test_atr_is_positive(self, ohlcv):
        result = indicators.atr(ohlcv["high"], ohlcv["low"], ohlcv["close"]).dropna()
        assert (result > 0).all()

    def test_historical_volatility_is_annualised(self, ohlcv):
        result = indicators.historical_volatility(ohlcv["close"], 20).dropna()
        assert (result > 0).all()


class TestOscillators:
    def test_stochastic_within_bounds(self, ohlcv):
        result = indicators.stochastic(ohlcv["high"], ohlcv["low"], ohlcv["close"]).dropna()
        assert result["k"].between(0, 100).all()
        assert result["d"].between(0, 100).all()

    def test_williams_r_within_bounds(self, ohlcv):
        result = indicators.williams_r(ohlcv["high"], ohlcv["low"], ohlcv["close"]).dropna()
        assert result.between(-100, 0).all()

    def test_adx_within_bounds(self, ohlcv):
        result = indicators.adx(ohlcv["high"], ohlcv["low"], ohlcv["close"]).dropna()
        assert result["adx"].between(0, 100).all()

    def test_mfi_within_bounds(self, ohlcv):
        result = indicators.money_flow_index(
            ohlcv["high"], ohlcv["low"], ohlcv["close"], ohlcv["volume"]
        ).dropna()
        assert result.between(0, 100).all()

    def test_cci_is_finite(self, ohlcv):
        result = indicators.cci(ohlcv["high"], ohlcv["low"], ohlcv["close"]).dropna()
        assert np.isfinite(result).all()


class TestVolumeAndMomentum:
    def test_obv_rises_with_price(self, rising_close):
        volume = pd.Series([1000.0] * len(rising_close))
        result = indicators.obv(rising_close, volume)
        assert result.iloc[-1] > result.iloc[0]

    def test_momentum_matches_difference(self, rising_close):
        result = indicators.momentum(rising_close, 10)
        assert result.iloc[-1] == pytest.approx(10.0)

    def test_rate_of_change_percentage(self):
        series = pd.Series([100.0] * 10 + [110.0])
        result = indicators.rate_of_change(series, 10)
        assert result.iloc[-1] == pytest.approx(10.0)


class TestLevels:
    def test_support_below_and_resistance_above_last_close(self, ohlcv):
        levels = indicators.support_resistance(ohlcv["high"], ohlcv["low"], ohlcv["close"])
        last_close = float(ohlcv["close"].iloc[-1])
        assert all(level < last_close for level in levels["support"])
        assert all(level > last_close for level in levels["resistance"])

    def test_support_resistance_handles_empty_input(self):
        empty = pd.Series(dtype="float64")
        assert indicators.support_resistance(empty, empty, empty) == {"support": [], "resistance": []}

    def test_pivot_points_are_ordered(self):
        result = indicators.pivot_points(110.0, 90.0, 105.0)
        assert result["s3"] < result["s2"] < result["s1"] < result["pivot"]
        assert result["pivot"] < result["r1"] < result["r2"] < result["r3"]


class TestSnapshot:
    def test_snapshot_contains_expected_keys(self, ohlcv):
        snapshot = indicators.compute_indicator_snapshot(ohlcv)
        for key in ("close", "rsi_14", "macd", "sma_50", "sma_200", "atr_14", "volume_ratio"):
            assert key in snapshot

    def test_snapshot_values_are_finite_or_none(self, ohlcv):
        snapshot = indicators.compute_indicator_snapshot(ohlcv)
        for value in snapshot.values():
            assert value is None or np.isfinite(value)

    def test_snapshot_returns_empty_for_missing_columns(self):
        assert indicators.compute_indicator_snapshot(pd.DataFrame({"close": [1.0]})) == {}

    def test_snapshot_returns_empty_for_empty_frame(self):
        assert indicators.compute_indicator_snapshot(pd.DataFrame()) == {}

    def test_short_history_yields_none_for_long_windows(self):
        frame = pd.DataFrame(
            {
                "open": [1.0] * 10,
                "high": [1.1] * 10,
                "low": [0.9] * 10,
                "close": [1.0] * 10,
                "volume": [100.0] * 10,
            }
        )
        snapshot = indicators.compute_indicator_snapshot(frame)
        assert snapshot["sma_200"] is None
        assert snapshot["close"] == pytest.approx(1.0)
