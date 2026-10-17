"""Technical indicator computation library.

Pure ``pandas``/``numpy`` implementations of the most widely used technical
indicators. The module intentionally has no dependency on the database,
FastAPI or any service so that it can be reused and unit tested in isolation.

All public helpers accept ``pandas`` objects ordered **ascending by date**
(oldest first) and return objects with the same index.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

__all__ = [
    "sma",
    "ema",
    "rsi",
    "macd",
    "bollinger_bands",
    "true_range",
    "atr",
    "stochastic",
    "obv",
    "adx",
    "cci",
    "williams_r",
    "momentum",
    "rate_of_change",
    "money_flow_index",
    "historical_volatility",
    "support_resistance",
    "pivot_points",
    "compute_indicator_snapshot",
]


def _as_series(values: pd.Series | List[float]) -> pd.Series:
    """Coerce the input into a float ``pandas.Series``."""
    if isinstance(values, pd.Series):
        return values.astype("float64")
    return pd.Series(values, dtype="float64")


def sma(values: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    series = _as_series(values)
    return series.rolling(window=window, min_periods=window).mean()


def ema(values: pd.Series, window: int) -> pd.Series:
    """Exponential moving average."""
    series = _as_series(values)
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def rsi(values: pd.Series, window: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing."""
    series = _as_series(values)
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))
    # A zero average loss means an uninterrupted rally -> fully overbought.
    result = result.where(avg_loss != 0.0, 100.0)
    result = result.where(~((avg_loss == 0.0) & (avg_gain == 0.0)), 50.0)
    return result.where(avg_gain.notna())


def macd(
    values: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """Moving Average Convergence Divergence.

    Returns a frame with ``macd``, ``signal`` and ``histogram`` columns.
    """
    series = _as_series(values)
    fast_ema = series.ewm(span=fast, adjust=False).mean()
    slow_ema = series.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": macd_line - signal_line,
        }
    )


def bollinger_bands(
    values: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands with band width and %B position."""
    series = _as_series(values)
    middle = series.rolling(window=window, min_periods=window).mean()
    std = series.rolling(window=window, min_periods=window).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    width = (upper - lower) / middle.replace(0.0, np.nan)
    span = (upper - lower).replace(0.0, np.nan)
    percent_b = (series - lower) / span
    return pd.DataFrame(
        {
            "middle": middle,
            "upper": upper,
            "lower": lower,
            "bandwidth": width,
            "percent_b": percent_b,
        }
    )


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Wilder's true range."""
    high_s, low_s, close_s = _as_series(high), _as_series(low), _as_series(close)
    prev_close = close_s.shift(1)
    ranges = pd.concat(
        [
            high_s - low_s,
            (high_s - prev_close).abs(),
            (low_s - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothed)."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_window: int = 14,
    d_window: int = 3,
) -> pd.DataFrame:
    """Stochastic oscillator (%K / %D)."""
    high_s, low_s, close_s = _as_series(high), _as_series(low), _as_series(close)
    lowest = low_s.rolling(window=k_window, min_periods=k_window).min()
    highest = high_s.rolling(window=k_window, min_periods=k_window).max()
    span = (highest - lowest).replace(0.0, np.nan)
    k = 100.0 * (close_s - lowest) / span
    d = k.rolling(window=d_window, min_periods=d_window).mean()
    return pd.DataFrame({"k": k, "d": d})


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    close_s = _as_series(close)
    volume_s = _as_series(volume)
    direction = np.sign(close_s.diff().fillna(0.0))
    return (direction * volume_s).cumsum()


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> pd.DataFrame:
    """Average Directional Index with +DI / -DI."""
    high_s, low_s = _as_series(high), _as_series(low)
    up_move = high_s.diff()
    down_move = -low_s.diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    alpha = 1.0 / window
    tr_smooth = true_range(high, low, close).ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    plus_smooth = plus_dm.ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    minus_smooth = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=window).mean()

    safe_tr = tr_smooth.replace(0.0, np.nan)
    plus_di = 100.0 * plus_smooth / safe_tr
    minus_di = 100.0 * minus_smooth / safe_tr
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_line = dx.ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    return pd.DataFrame({"adx": adx_line, "plus_di": plus_di, "minus_di": minus_di})


def cci(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    """Commodity Channel Index."""
    typical = (_as_series(high) + _as_series(low) + _as_series(close)) / 3.0
    moving = typical.rolling(window=window, min_periods=window).mean()
    mean_dev = typical.rolling(window=window, min_periods=window).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True
    )
    return (typical - moving) / (0.015 * mean_dev.replace(0.0, np.nan))


def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Williams %R (ranges from -100 to 0)."""
    high_s, low_s, close_s = _as_series(high), _as_series(low), _as_series(close)
    highest = high_s.rolling(window=window, min_periods=window).max()
    lowest = low_s.rolling(window=window, min_periods=window).min()
    span = (highest - lowest).replace(0.0, np.nan)
    return -100.0 * (highest - close_s) / span


def momentum(values: pd.Series, window: int = 10) -> pd.Series:
    """Absolute price momentum over ``window`` periods."""
    series = _as_series(values)
    return series - series.shift(window)


def rate_of_change(values: pd.Series, window: int = 10) -> pd.Series:
    """Percentage rate of change over ``window`` periods."""
    series = _as_series(values)
    base = series.shift(window).replace(0.0, np.nan)
    return 100.0 * (series - base) / base


def money_flow_index(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    window: int = 14,
) -> pd.Series:
    """Money Flow Index — a volume weighted RSI."""
    typical = (_as_series(high) + _as_series(low) + _as_series(close)) / 3.0
    raw_flow = typical * _as_series(volume)
    delta = typical.diff()

    positive = raw_flow.where(delta > 0, 0.0).rolling(window=window, min_periods=window).sum()
    negative = raw_flow.where(delta < 0, 0.0).rolling(window=window, min_periods=window).sum()

    ratio = positive / negative.replace(0.0, np.nan)
    result = 100.0 - (100.0 / (1.0 + ratio))
    return result.where(negative != 0.0, 100.0).where(positive.notna())


def historical_volatility(close: pd.Series, window: int = 20, periods_per_year: int = 252) -> pd.Series:
    """Annualised rolling volatility of log returns."""
    series = _as_series(close)
    log_returns = np.log(series / series.shift(1))
    return log_returns.rolling(window=window, min_periods=window).std(ddof=1) * np.sqrt(periods_per_year)


def support_resistance(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    lookback: int = 60,
    levels: int = 3,
) -> Dict[str, List[float]]:
    """Derive naive support/resistance levels from recent swing points.

    Swing highs/lows are local extrema over a 5-bar window inside the most
    recent ``lookback`` bars. Levels above the last close become resistance,
    levels below become support.
    """
    high_s, low_s, close_s = _as_series(high), _as_series(low), _as_series(close)
    if close_s.dropna().empty:
        return {"support": [], "resistance": []}

    window_high = high_s.tail(lookback)
    window_low = low_s.tail(lookback)
    last_close = float(close_s.dropna().iloc[-1])

    swing_highs: List[float] = []
    swing_lows: List[float] = []
    highs = window_high.to_numpy(dtype="float64")
    lows = window_low.to_numpy(dtype="float64")
    for i in range(2, len(highs) - 2):
        window = highs[i - 2 : i + 3]
        if not np.isnan(window).any() and highs[i] == window.max():
            swing_highs.append(float(highs[i]))
        window = lows[i - 2 : i + 3]
        if not np.isnan(window).any() and lows[i] == window.min():
            swing_lows.append(float(lows[i]))

    resistance = sorted({round(v, 4) for v in swing_highs if v > last_close})[:levels]
    support = sorted({round(v, 4) for v in swing_lows if v < last_close}, reverse=True)[:levels]

    if not resistance and len(highs):
        highest = float(np.nanmax(highs))
        if highest > last_close:
            resistance = [round(highest, 4)]
    if not support and len(lows):
        lowest = float(np.nanmin(lows))
        if lowest < last_close:
            support = [round(lowest, 4)]

    return {"support": support, "resistance": resistance}


def pivot_points(high: float, low: float, close: float) -> Dict[str, float]:
    """Classic floor-trader pivot points for the next session."""
    pivot = (high + low + close) / 3.0
    return {
        "pivot": round(pivot, 4),
        "r1": round(2 * pivot - low, 4),
        "r2": round(pivot + (high - low), 4),
        "r3": round(high + 2 * (pivot - low), 4),
        "s1": round(2 * pivot - high, 4),
        "s2": round(pivot - (high - low), 4),
        "s3": round(low - 2 * (high - pivot), 4),
    }


def _last_valid(series: pd.Series) -> Optional[float]:
    """Return the last non-NaN value of ``series`` as a plain float."""
    if series is None or len(series) == 0:
        return None
    cleaned = series.dropna()
    if cleaned.empty:
        return None
    value = float(cleaned.iloc[-1])
    if not np.isfinite(value):
        return None
    return round(value, 6)


def compute_indicator_snapshot(df: pd.DataFrame) -> Dict[str, Optional[float]]:
    """Compute the latest value of every supported indicator.

    ``df`` must contain ``open``/``high``/``low``/``close``/``volume`` columns
    sorted ascending by date. Missing history simply yields ``None`` values
    instead of raising.
    """
    required = {"high", "low", "close", "volume"}
    if df is None or df.empty or not required.issubset(df.columns):
        return {}

    close = _as_series(df["close"])
    high = _as_series(df["high"])
    low = _as_series(df["low"])
    volume = _as_series(df["volume"])

    macd_df = macd(close)
    bb_df = bollinger_bands(close)
    stoch_df = stochastic(high, low, close)
    adx_df = adx(high, low, close)

    snapshot: Dict[str, Optional[float]] = {
        "close": _last_valid(close),
        "sma_5": _last_valid(sma(close, 5)),
        "sma_10": _last_valid(sma(close, 10)),
        "sma_20": _last_valid(sma(close, 20)),
        "sma_50": _last_valid(sma(close, 50)),
        "sma_200": _last_valid(sma(close, 200)),
        "ema_12": _last_valid(ema(close, 12)),
        "ema_26": _last_valid(ema(close, 26)),
        "rsi_14": _last_valid(rsi(close, 14)),
        "macd": _last_valid(macd_df["macd"]),
        "macd_signal": _last_valid(macd_df["signal"]),
        "macd_histogram": _last_valid(macd_df["histogram"]),
        "bb_upper": _last_valid(bb_df["upper"]),
        "bb_middle": _last_valid(bb_df["middle"]),
        "bb_lower": _last_valid(bb_df["lower"]),
        "bb_bandwidth": _last_valid(bb_df["bandwidth"]),
        "bb_percent_b": _last_valid(bb_df["percent_b"]),
        "atr_14": _last_valid(atr(high, low, close, 14)),
        "stoch_k": _last_valid(stoch_df["k"]),
        "stoch_d": _last_valid(stoch_df["d"]),
        "adx_14": _last_valid(adx_df["adx"]),
        "plus_di": _last_valid(adx_df["plus_di"]),
        "minus_di": _last_valid(adx_df["minus_di"]),
        "cci_20": _last_valid(cci(high, low, close, 20)),
        "williams_r_14": _last_valid(williams_r(high, low, close, 14)),
        "obv": _last_valid(obv(close, volume)),
        "mfi_14": _last_valid(money_flow_index(high, low, close, volume, 14)),
        "roc_10": _last_valid(rate_of_change(close, 10)),
        "momentum_10": _last_valid(momentum(close, 10)),
        "volatility_20": _last_valid(historical_volatility(close, 20)),
        "avg_volume_20": _last_valid(volume.rolling(20, min_periods=1).mean()),
    }

    latest_volume = _last_valid(volume)
    avg_volume = snapshot.get("avg_volume_20")
    if latest_volume is not None and avg_volume:
        snapshot["volume_ratio"] = round(latest_volume / avg_volume, 6)
    else:
        snapshot["volume_ratio"] = None

    return snapshot
