"""Unit tests for the risk and performance analytics library."""
import numpy as np
import pandas as pd
import pytest

from app.core import analytics


@pytest.fixture
def steady_growth():
    """A price series that compounds by exactly 1% per period."""
    return pd.Series([100.0 * (1.01 ** i) for i in range(253)])


@pytest.fixture
def noisy_prices():
    rng = np.random.default_rng(7)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, 500))))


class TestReturns:
    def test_cumulative_return(self):
        assert analytics.cumulative_return(pd.Series([100.0, 110.0])) == pytest.approx(0.10)

    def test_cumulative_return_needs_two_points(self):
        assert analytics.cumulative_return(pd.Series([100.0])) is None

    def test_annualized_return_of_steady_growth(self, steady_growth):
        result = analytics.annualized_return(steady_growth)
        # 252 compounding periods at 1% each -> 1.01**252 - 1
        assert result == pytest.approx(1.01 ** 252 - 1, rel=1e-3)

    def test_daily_returns_length(self, noisy_prices):
        assert len(analytics.daily_returns(noisy_prices)) == len(noisy_prices) - 1

    def test_period_returns_keys(self, noisy_prices):
        result = analytics.period_returns(noisy_prices)
        assert set(result) == {"1d", "5d", "1m", "3m", "6m", "1y"}

    def test_period_returns_none_when_history_too_short(self):
        result = analytics.period_returns(pd.Series([1.0, 2.0, 3.0]))
        assert result["1y"] is None
        assert result["1d"] == pytest.approx(0.5)


class TestVolatilityAndRatios:
    def test_volatility_of_constant_series_is_zero(self):
        returns = analytics.daily_returns(pd.Series([100.0] * 50))
        assert analytics.annualized_volatility(returns) == pytest.approx(0.0)

    def test_volatility_is_positive_for_noisy_series(self, noisy_prices):
        returns = analytics.daily_returns(noisy_prices)
        assert analytics.annualized_volatility(returns) > 0

    def test_sharpe_ratio_none_for_zero_variance(self):
        returns = analytics.daily_returns(pd.Series([100.0] * 50))
        assert analytics.sharpe_ratio(returns) is None

    def test_sharpe_positive_for_upward_drift(self, noisy_prices):
        returns = analytics.daily_returns(noisy_prices)
        assert analytics.sharpe_ratio(returns) is not None

    def test_sortino_ignores_upside_volatility(self):
        returns = pd.Series([0.01, 0.02, 0.03, -0.001, 0.05])
        sortino = analytics.sortino_ratio(returns)
        sharpe = analytics.sharpe_ratio(returns)
        assert sortino is not None and sharpe is not None
        assert sortino > sharpe

    def test_downside_deviation_zero_when_no_losses(self):
        assert analytics.downside_deviation(pd.Series([0.01, 0.02, 0.03])) == 0.0


class TestDrawdown:
    def test_max_drawdown_of_known_path(self):
        prices = pd.Series([100.0, 120.0, 60.0, 90.0, 130.0])
        result = analytics.max_drawdown(prices)
        assert result["max_drawdown"] == pytest.approx(-0.5)
        assert result["peak_index"] == 1
        assert result["trough_index"] == 2
        assert result["recovery_index"] == 4

    def test_max_drawdown_without_recovery(self):
        result = analytics.max_drawdown(pd.Series([100.0, 120.0, 60.0]))
        assert result["recovery_index"] is None

    def test_max_drawdown_monotonic_series_is_zero(self, steady_growth):
        assert analytics.max_drawdown(steady_growth)["max_drawdown"] == pytest.approx(0.0)

    def test_calmar_ratio_none_without_drawdown(self, steady_growth):
        assert analytics.calmar_ratio(steady_growth) is None

    def test_calmar_ratio_computed_when_drawdown_exists(self, noisy_prices):
        assert analytics.calmar_ratio(noisy_prices) is not None


class TestTailRisk:
    def test_var_is_negative_for_noisy_returns(self, noisy_prices):
        returns = analytics.daily_returns(noisy_prices)
        assert analytics.value_at_risk(returns) < 0

    def test_cvar_is_worse_than_var(self, noisy_prices):
        returns = analytics.daily_returns(noisy_prices)
        assert analytics.conditional_value_at_risk(returns) <= analytics.value_at_risk(returns)

    def test_var_none_for_empty_series(self):
        assert analytics.value_at_risk(pd.Series(dtype="float64")) is None


class TestBenchmarkRelative:
    def test_beta_of_identical_series_is_one(self, noisy_prices):
        returns = analytics.daily_returns(noisy_prices)
        result = analytics.beta_alpha(returns, returns)
        assert result["beta"] == pytest.approx(1.0)
        assert result["r_squared"] == pytest.approx(1.0)

    def test_beta_of_doubled_series_is_two(self, noisy_prices):
        returns = analytics.daily_returns(noisy_prices)
        result = analytics.beta_alpha(returns * 2, returns)
        assert result["beta"] == pytest.approx(2.0)

    def test_beta_none_for_short_history(self):
        result = analytics.beta_alpha(pd.Series([0.01]), pd.Series([0.01]))
        assert result["beta"] is None

    def test_correlation_of_identical_series(self, noisy_prices):
        returns = analytics.daily_returns(noisy_prices)
        assert analytics.correlation(returns, returns) == pytest.approx(1.0)

    def test_correlation_none_for_short_history(self):
        assert analytics.correlation(pd.Series([0.1]), pd.Series([0.2])) is None


class TestAggregate:
    def test_compute_risk_metrics_keys(self, noisy_prices):
        metrics = analytics.compute_risk_metrics(noisy_prices)
        for key in (
            "cumulative_return", "annualized_return", "annualized_volatility",
            "sharpe_ratio", "sortino_ratio", "max_drawdown", "value_at_risk_95", "win_rate",
        ):
            assert key in metrics

    def test_compute_risk_metrics_with_benchmark_adds_beta(self, noisy_prices):
        metrics = analytics.compute_risk_metrics(noisy_prices, benchmark_prices=noisy_prices * 1.5)
        assert metrics["beta"] is not None
        assert metrics["benchmark_correlation"] == pytest.approx(1.0)

    def test_compute_risk_metrics_empty_for_short_history(self):
        assert analytics.compute_risk_metrics(pd.Series([1.0])) == {}

    def test_win_rate_between_zero_and_one(self, noisy_prices):
        rate = analytics.win_rate(analytics.daily_returns(noisy_prices))
        assert 0.0 <= rate <= 1.0

    def test_best_worst_day_ordering(self, noisy_prices):
        result = analytics.best_worst_day(analytics.daily_returns(noisy_prices))
        assert result["best"] > result["worst"]


class TestSeasonality:
    @pytest.fixture
    def dated_prices(self):
        dates = pd.bdate_range("2020-01-01", periods=750)
        rng = np.random.default_rng(3)
        prices = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, len(dates))))
        return dates, pd.Series(prices)

    def test_monthly_seasonality_covers_all_months(self, dated_prices):
        dates, prices = dated_prices
        result = analytics.monthly_seasonality(dates, prices)
        assert len(result) == 12
        assert {item["period"] for item in result} == set(range(1, 13))

    def test_weekday_seasonality_covers_business_days(self, dated_prices):
        dates, prices = dated_prices
        result = analytics.weekday_seasonality(dates, prices)
        assert {item["period"] for item in result} == {0, 1, 2, 3, 4}

    def test_seasonality_positive_rate_bounds(self, dated_prices):
        dates, prices = dated_prices
        for item in analytics.monthly_seasonality(dates, prices):
            assert 0.0 <= item["positive_rate"] <= 1.0
            assert item["sample_size"] > 0

    def test_seasonality_empty_for_short_history(self):
        assert analytics.monthly_seasonality(pd.bdate_range("2020-01-01", periods=1), pd.Series([1.0])) == []
