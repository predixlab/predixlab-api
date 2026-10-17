"""Risk and performance analytics.

Pure ``pandas``/``numpy`` helpers that turn a price series into the risk and
performance metrics an investor actually cares about: how much did it make,
how bumpy was the ride, and how does it behave relative to a benchmark.

Every helper tolerates short or missing history by returning ``None`` rather
than raising, so callers can render partial results.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252

__all__ = [
    "TRADING_DAYS_PER_YEAR",
    "daily_returns",
    "cumulative_return",
    "annualized_return",
    "annualized_volatility",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "calmar_ratio",
    "value_at_risk",
    "conditional_value_at_risk",
    "beta_alpha",
    "correlation",
    "downside_deviation",
    "win_rate",
    "best_worst_day",
    "period_returns",
    "compute_risk_metrics",
    "monthly_seasonality",
    "weekday_seasonality",
]


def _as_series(values) -> pd.Series:
    if isinstance(values, pd.Series):
        return values.astype("float64")
    return pd.Series(values, dtype="float64")


def _round(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    if not np.isfinite(value):
        return None
    return round(float(value), digits)


def daily_returns(prices) -> pd.Series:
    """Simple period-over-period returns."""
    series = _as_series(prices)
    return series.pct_change().dropna()


def cumulative_return(prices) -> Optional[float]:
    """Total return over the whole series, expressed as a fraction."""
    series = _as_series(prices).dropna()
    if len(series) < 2 or series.iloc[0] == 0:
        return None
    return _round(series.iloc[-1] / series.iloc[0] - 1.0)


def annualized_return(prices, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> Optional[float]:
    """Compound annual growth rate implied by the series."""
    series = _as_series(prices).dropna()
    if len(series) < 2 or series.iloc[0] <= 0:
        return None
    years = (len(series) - 1) / periods_per_year
    if years <= 0:
        return None
    growth = series.iloc[-1] / series.iloc[0]
    if growth <= 0:
        return None
    return _round(growth ** (1.0 / years) - 1.0)


def annualized_volatility(returns, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> Optional[float]:
    """Annualised standard deviation of returns."""
    series = _as_series(returns).dropna()
    if len(series) < 2:
        return None
    return _round(series.std(ddof=1) * np.sqrt(periods_per_year))


def downside_deviation(
    returns,
    target: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> Optional[float]:
    """Annualised standard deviation of returns below ``target``."""
    series = _as_series(returns).dropna()
    if series.empty:
        return None
    shortfall = np.minimum(series - target, 0.0)
    if not np.any(shortfall < 0):
        return 0.0
    return _round(float(np.sqrt(np.mean(np.square(shortfall)))) * np.sqrt(periods_per_year))


def sharpe_ratio(
    returns,
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> Optional[float]:
    """Annualised Sharpe ratio. ``risk_free_rate`` is an annual rate."""
    series = _as_series(returns).dropna()
    if len(series) < 2:
        return None
    std = series.std(ddof=1)
    if std == 0 or not np.isfinite(std):
        return None
    excess = series - risk_free_rate / periods_per_year
    return _round(excess.mean() / std * np.sqrt(periods_per_year))


def sortino_ratio(
    returns,
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> Optional[float]:
    """Annualised Sortino ratio (downside-risk adjusted return)."""
    series = _as_series(returns).dropna()
    if len(series) < 2:
        return None
    periodic_rf = risk_free_rate / periods_per_year
    excess = series - periodic_rf
    dd = downside_deviation(series, target=periodic_rf, periods_per_year=periods_per_year)
    if not dd:
        return None
    return _round(excess.mean() * periods_per_year / dd)


def max_drawdown(prices) -> Dict[str, Optional[float]]:
    """Largest peak-to-trough decline and where it happened.

    Returns a dict with ``max_drawdown`` (negative fraction), ``peak_index``,
    ``trough_index`` and ``recovery_index`` (``None`` when not yet recovered).
    """
    series = _as_series(prices).dropna()
    if len(series) < 2:
        return {"max_drawdown": None, "peak_index": None, "trough_index": None, "recovery_index": None}

    running_max = series.cummax()
    drawdown = series / running_max - 1.0
    trough_pos = int(drawdown.values.argmin())
    worst = float(drawdown.iloc[trough_pos])

    peak_pos = int(series.iloc[: trough_pos + 1].values.argmax())
    peak_value = float(series.iloc[peak_pos])

    recovery_pos: Optional[int] = None
    after = series.iloc[trough_pos + 1 :]
    recovered = after[after >= peak_value]
    if not recovered.empty:
        recovery_pos = int(series.index.get_indexer([recovered.index[0]])[0])

    return {
        "max_drawdown": _round(worst),
        "peak_index": peak_pos,
        "trough_index": trough_pos,
        "recovery_index": recovery_pos,
    }


def calmar_ratio(prices, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> Optional[float]:
    """Annualised return divided by the absolute max drawdown."""
    ann = annualized_return(prices, periods_per_year)
    dd = max_drawdown(prices)["max_drawdown"]
    if ann is None or dd is None or dd == 0:
        return None
    return _round(ann / abs(dd))


def value_at_risk(returns, confidence: float = 0.95) -> Optional[float]:
    """Historical VaR: the return threshold breached ``1 - confidence`` of the time."""
    series = _as_series(returns).dropna()
    if series.empty:
        return None
    return _round(float(np.percentile(series, (1.0 - confidence) * 100.0)))


def conditional_value_at_risk(returns, confidence: float = 0.95) -> Optional[float]:
    """Expected shortfall — the mean return in the worst tail."""
    series = _as_series(returns).dropna()
    if series.empty:
        return None
    threshold = np.percentile(series, (1.0 - confidence) * 100.0)
    tail = series[series <= threshold]
    if tail.empty:
        return _round(float(threshold))
    return _round(float(tail.mean()))


def beta_alpha(
    returns,
    benchmark_returns,
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> Dict[str, Optional[float]]:
    """Beta and annualised Jensen's alpha against a benchmark."""
    asset = _as_series(returns)
    bench = _as_series(benchmark_returns)
    aligned = pd.concat([asset, bench], axis=1, join="inner").dropna()
    if len(aligned) < 3:
        return {"beta": None, "alpha": None, "r_squared": None}

    asset_vals = aligned.iloc[:, 0]
    bench_vals = aligned.iloc[:, 1]
    variance = bench_vals.var(ddof=1)
    if variance == 0 or not np.isfinite(variance):
        return {"beta": None, "alpha": None, "r_squared": None}

    beta = asset_vals.cov(bench_vals) / variance
    periodic_rf = risk_free_rate / periods_per_year
    alpha = (asset_vals.mean() - periodic_rf) - beta * (bench_vals.mean() - periodic_rf)
    corr = asset_vals.corr(bench_vals)
    r_squared = corr ** 2 if corr is not None and np.isfinite(corr) else None

    return {
        "beta": _round(beta),
        "alpha": _round(alpha * periods_per_year),
        "r_squared": _round(r_squared),
    }


def correlation(returns, other_returns) -> Optional[float]:
    """Pearson correlation between two return series."""
    aligned = pd.concat([_as_series(returns), _as_series(other_returns)], axis=1, join="inner").dropna()
    if len(aligned) < 3:
        return None
    value = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    return _round(value)


def win_rate(returns) -> Optional[float]:
    """Share of periods with a positive return."""
    series = _as_series(returns).dropna()
    if series.empty:
        return None
    return _round(float((series > 0).mean()))


def best_worst_day(returns) -> Dict[str, Optional[float]]:
    """Largest single-period gain and loss."""
    series = _as_series(returns).dropna()
    if series.empty:
        return {"best": None, "worst": None}
    return {"best": _round(float(series.max())), "worst": _round(float(series.min()))}


def period_returns(prices, windows: Optional[Dict[str, int]] = None) -> Dict[str, Optional[float]]:
    """Trailing returns over a set of named lookback windows (in bars)."""
    series = _as_series(prices).dropna()
    windows = windows or {"1d": 1, "5d": 5, "1m": 21, "3m": 63, "6m": 126, "1y": 252}
    result: Dict[str, Optional[float]] = {}
    for name, bars in windows.items():
        if len(series) <= bars:
            result[name] = None
            continue
        past = float(series.iloc[-(bars + 1)])
        if past == 0:
            result[name] = None
            continue
        result[name] = _round(float(series.iloc[-1]) / past - 1.0)
    return result


def compute_risk_metrics(
    prices,
    benchmark_prices=None,
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> Dict[str, Optional[float]]:
    """Aggregate every risk/performance metric for a price series."""
    series = _as_series(prices).dropna()
    if len(series) < 2:
        return {}

    rets = daily_returns(series)
    drawdown = max_drawdown(series)

    metrics: Dict[str, Optional[float]] = {
        "observations": len(series),
        "cumulative_return": cumulative_return(series),
        "annualized_return": annualized_return(series, periods_per_year),
        "annualized_volatility": annualized_volatility(rets, periods_per_year),
        "sharpe_ratio": sharpe_ratio(rets, risk_free_rate, periods_per_year),
        "sortino_ratio": sortino_ratio(rets, risk_free_rate, periods_per_year),
        "max_drawdown": drawdown["max_drawdown"],
        "calmar_ratio": calmar_ratio(series, periods_per_year),
        "downside_deviation": downside_deviation(rets, 0.0, periods_per_year),
        "value_at_risk_95": value_at_risk(rets, 0.95),
        "conditional_var_95": conditional_value_at_risk(rets, 0.95),
        "win_rate": win_rate(rets),
    }
    metrics.update({f"{k}_day": v for k, v in best_worst_day(rets).items()})

    if benchmark_prices is not None:
        bench_returns = daily_returns(benchmark_prices)
        metrics.update(beta_alpha(rets, bench_returns, risk_free_rate, periods_per_year))
        metrics["benchmark_correlation"] = correlation(rets, bench_returns)

    return metrics


def monthly_seasonality(dates, prices) -> List[Dict[str, Optional[float]]]:
    """Average return per calendar month across the supplied history."""
    frame = pd.DataFrame({"date": pd.to_datetime(pd.Series(dates)), "close": _as_series(prices).values})
    frame = frame.dropna().sort_values("date")
    if len(frame) < 2:
        return []
    frame["ret"] = frame["close"].pct_change()
    frame = frame.dropna()
    if frame.empty:
        return []

    grouped = frame.groupby(frame["date"].dt.month)["ret"]
    result: List[Dict[str, Optional[float]]] = []
    for month in range(1, 13):
        if month not in grouped.groups:
            continue
        values = grouped.get_group(month)
        result.append(
            {
                "period": month,
                "average_return": _round(float(values.mean())),
                "median_return": _round(float(values.median())),
                "positive_rate": _round(float((values > 0).mean())),
                "sample_size": int(len(values)),
            }
        )
    return result


def weekday_seasonality(dates, prices) -> List[Dict[str, Optional[float]]]:
    """Average return per weekday (0 = Monday) across the supplied history."""
    frame = pd.DataFrame({"date": pd.to_datetime(pd.Series(dates)), "close": _as_series(prices).values})
    frame = frame.dropna().sort_values("date")
    if len(frame) < 2:
        return []
    frame["ret"] = frame["close"].pct_change()
    frame = frame.dropna()
    if frame.empty:
        return []

    grouped = frame.groupby(frame["date"].dt.weekday)["ret"]
    result: List[Dict[str, Optional[float]]] = []
    for weekday in range(0, 7):
        if weekday not in grouped.groups:
            continue
        values = grouped.get_group(weekday)
        result.append(
            {
                "period": weekday,
                "average_return": _round(float(values.mean())),
                "median_return": _round(float(values.median())),
                "positive_rate": _round(float((values > 0).mean())),
                "sample_size": int(len(values)),
            }
        )
    return result
