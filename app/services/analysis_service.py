"""Per-symbol deep analysis: indicators, risk, peers, seasonality, narrative.

The goal of this service is explainability. Instead of only telling the user
that a model assigned a 0.72 probability, it describes *what the tape is doing*
— trend structure, momentum, volatility, key levels — and translates those
observations into plain language.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app.core import analytics, indicators
from app.models.market import (
    CorrelationResponse,
    IndicatorSeriesPoint,
    IndicatorSeriesResponse,
    IndicatorSnapshot,
    PeerComparisonEntry,
    PeerComparisonResponse,
    RiskLevel,
    RiskMetrics,
    SeasonalityBucket,
    SeasonalityResponse,
    SignalStrength,
    SupportResistanceLevels,
    SymbolProfileResponse,
    SymbolQuote,
    TechnicalAnalysisResponse,
    TechnicalSignal,
    TrendDirection,
)
from app.services.cache import InMemoryCache
from app.services.market_data import market_data_repository

logger = logging.getLogger(__name__)

MONTH_LABELS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
WEEKDAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SUPPORTED_SERIES_INDICATORS = {
    "sma_5", "sma_10", "sma_20", "sma_50", "sma_200",
    "ema_12", "ema_26", "rsi_14", "macd", "macd_signal", "macd_histogram",
    "bb_upper", "bb_middle", "bb_lower", "atr_14", "obv",
    "stoch_k", "stoch_d", "adx_14", "cci_20", "williams_r_14", "mfi_14",
}


class AnalysisService:
    """Turns a single symbol's price history into actionable context."""

    def __init__(self, repository=None):
        self.repository = repository or market_data_repository
        self.cache = InMemoryCache(default_ttl_minutes=30)

    async def initialize(self) -> None:
        return None

    # -- quotes -----------------------------------------------------------

    def _build_quote(self, symbol: str, df: pd.DataFrame) -> SymbolQuote:
        close = df["close"].astype("float64")
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else None
        volume = df["volume"].iloc[-1]
        avg_volume = float(df["volume"].tail(20).mean()) if len(df) >= 5 else None
        window_52w = df.tail(252)

        return SymbolQuote(
            symbol=symbol.upper(),
            date=pd.Timestamp(df["date"].iloc[-1]).strftime("%Y-%m-%d"),
            close=round(last, 4),
            open=_f(df["open"].iloc[-1]),
            high=_f(df["high"].iloc[-1]),
            low=_f(df["low"].iloc[-1]),
            volume=int(volume) if pd.notna(volume) else None,
            change=round(last - prev, 4) if prev is not None else None,
            change_percent=round((last - prev) / prev, 6) if prev else None,
            volume_ratio=round(float(volume) / avg_volume, 4) if (avg_volume and pd.notna(volume)) else None,
            week52_high=_f(window_52w["high"].max()) if not window_52w.empty else None,
            week52_low=_f(window_52w["low"].min()) if not window_52w.empty else None,
        )

    async def get_quote(self, symbol: str) -> Optional[SymbolQuote]:
        df = self.repository.get_tail(symbol, 300)
        if df is None or df.empty:
            return None
        return self._build_quote(symbol, df)

    async def get_quotes(self, symbols: List[str]) -> List[SymbolQuote]:
        quotes: List[SymbolQuote] = []
        for symbol in symbols:
            quote = await self.get_quote(symbol)
            if quote is not None:
                quotes.append(quote)
        return quotes

    # -- indicators -------------------------------------------------------

    async def get_indicator_snapshot(self, symbol: str) -> Optional[IndicatorSnapshot]:
        df = self.repository.get_frame(symbol)
        if df is None or df.empty:
            return None
        return IndicatorSnapshot(
            symbol=symbol.upper(),
            date=pd.Timestamp(df["date"].iloc[-1]).strftime("%Y-%m-%d"),
            values=indicators.compute_indicator_snapshot(df),
        )

    async def get_indicator_series(
        self,
        symbol: str,
        names: List[str],
        limit: int = 250,
    ) -> Optional[IndicatorSeriesResponse]:
        """Return a per-date series for the requested indicator names."""
        df = self.repository.get_frame(symbol)
        if df is None or df.empty:
            return None

        requested = [n for n in names if n in SUPPORTED_SERIES_INDICATORS]
        if not requested:
            requested = ["sma_20", "sma_50", "rsi_14", "macd"]

        computed = self._compute_series(df)
        frame = pd.DataFrame({name: computed[name] for name in requested if name in computed})
        frame.insert(0, "date", pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").to_numpy())
        frame = frame.tail(limit)

        points = [
            IndicatorSeriesPoint(
                date=row["date"],
                values={name: _f(row.get(name)) for name in requested if name in frame.columns},
            )
            for _, row in frame.iterrows()
        ]

        return IndicatorSeriesResponse(
            symbol=symbol.upper(),
            indicators=requested,
            points=points,
            total_records=len(points),
        )

    @staticmethod
    def _compute_series(df: pd.DataFrame) -> Dict[str, pd.Series]:
        """Compute every supported indicator as a full series."""
        close, high, low = df["close"], df["high"], df["low"]
        volume = df["volume"]
        macd_df = indicators.macd(close)
        bb_df = indicators.bollinger_bands(close)
        stoch_df = indicators.stochastic(high, low, close)
        adx_df = indicators.adx(high, low, close)

        return {
            "sma_5": indicators.sma(close, 5),
            "sma_10": indicators.sma(close, 10),
            "sma_20": indicators.sma(close, 20),
            "sma_50": indicators.sma(close, 50),
            "sma_200": indicators.sma(close, 200),
            "ema_12": indicators.ema(close, 12),
            "ema_26": indicators.ema(close, 26),
            "rsi_14": indicators.rsi(close, 14),
            "macd": macd_df["macd"],
            "macd_signal": macd_df["signal"],
            "macd_histogram": macd_df["histogram"],
            "bb_upper": bb_df["upper"],
            "bb_middle": bb_df["middle"],
            "bb_lower": bb_df["lower"],
            "atr_14": indicators.atr(high, low, close, 14),
            "obv": indicators.obv(close, volume),
            "stoch_k": stoch_df["k"],
            "stoch_d": stoch_df["d"],
            "adx_14": adx_df["adx"],
            "cci_20": indicators.cci(high, low, close, 20),
            "williams_r_14": indicators.williams_r(high, low, close, 14),
            "mfi_14": indicators.money_flow_index(high, low, close, volume, 14),
        }

    # -- technical analysis ----------------------------------------------

    async def get_technical_analysis(self, symbol: str) -> Optional[TechnicalAnalysisResponse]:
        """Full rule-based technical read-out for a symbol."""
        df = self.repository.get_frame(symbol)
        if df is None or len(df) < 2:
            return None

        snapshot = indicators.compute_indicator_snapshot(df)
        quote = self._build_quote(symbol, df)
        trend = classify_trend(snapshot)
        signals = build_signals(snapshot, quote)
        score = aggregate_signal_score(signals)
        strength = classify_signal(score)

        levels_raw = indicators.support_resistance(df["high"], df["low"], df["close"])
        pivots = indicators.pivot_points(
            float(df["high"].iloc[-1]), float(df["low"].iloc[-1]), float(df["close"].iloc[-1])
        )
        levels = SupportResistanceLevels(
            support=levels_raw["support"], resistance=levels_raw["resistance"], pivot=pivots
        )

        performance = analytics.period_returns(df["close"])
        narrative = build_narrative(symbol.upper(), quote, trend, strength, snapshot, signals, levels)

        return TechnicalAnalysisResponse(
            symbol=symbol.upper(),
            date=quote.date,
            quote=quote,
            trend=trend,
            signal=strength,
            signal_score=score,
            indicators=snapshot,
            signals=signals,
            levels=levels,
            performance=performance,
            narrative=narrative,
        )

    # -- risk -------------------------------------------------------------

    async def get_risk_metrics(
        self,
        symbol: str,
        window_days: int = 252,
        benchmark: Optional[str] = None,
    ) -> Optional[RiskMetrics]:
        df = self.repository.get_tail(symbol, window_days)
        if df is None or len(df) < 2:
            return None

        benchmark_prices = None
        if benchmark:
            bench_df = self.repository.get_tail(benchmark, window_days)
            if bench_df is not None and not bench_df.empty:
                aligned = pd.merge(
                    df[["date", "close"]],
                    bench_df[["date", "close"]],
                    on="date",
                    suffixes=("", "_bench"),
                )
                if len(aligned) >= 3:
                    df = aligned[["date", "close"]].copy()
                    benchmark_prices = aligned["close_bench"]

        metrics = analytics.compute_risk_metrics(df["close"], benchmark_prices)
        return RiskMetrics(
            symbol=symbol.upper(),
            window_days=window_days,
            benchmark=benchmark.upper() if benchmark else None,
            metrics=metrics,
            risk_level=classify_risk_level(metrics.get("annualized_volatility")),
        )

    # -- peers and correlation -------------------------------------------

    async def compare_peers(
        self,
        symbol: str,
        peers: List[str],
        window_days: int = 252,
    ) -> Optional[PeerComparisonResponse]:
        """Compare a symbol against a peer group on return and risk."""
        base_df = self.repository.get_tail(symbol, window_days)
        if base_df is None or len(base_df) < 2:
            return None

        base_returns = analytics.daily_returns(base_df["close"])
        base_series = pd.Series(
            base_returns.to_numpy(), index=pd.DatetimeIndex(base_df["date"].iloc[1:])
        )

        peer_entries: List[PeerComparisonEntry] = []
        peer_window_returns: List[float] = []

        for peer in peers:
            if peer.upper() == symbol.upper():
                continue
            peer_df = self.repository.get_tail(peer, window_days)
            if peer_df is None or len(peer_df) < 2:
                continue
            entry = self._build_comparison_entry(peer, peer_df, base_series)
            peer_entries.append(entry)
            window_return = analytics.cumulative_return(peer_df["close"])
            if window_return is not None:
                peer_window_returns.append(window_return)

        base_entry = self._build_comparison_entry(symbol, base_df, base_series)
        if peer_window_returns:
            peer_avg = float(np.mean(peer_window_returns))
            base_return = analytics.cumulative_return(base_df["close"])
            if base_return is not None:
                base_entry.relative_strength = round(base_return - peer_avg, 6)
            for entry, ret in zip(peer_entries, peer_window_returns):
                entry.relative_strength = round(ret - peer_avg, 6)

        peer_entries.sort(key=lambda e: e.relative_strength or -np.inf, reverse=True)

        return PeerComparisonResponse(
            symbol=symbol.upper(),
            window_days=window_days,
            peers=peer_entries,
            base=base_entry,
        )

    def _build_comparison_entry(
        self,
        symbol: str,
        df: pd.DataFrame,
        base_returns: pd.Series,
    ) -> PeerComparisonEntry:
        close = df["close"]
        returns = analytics.daily_returns(close)
        indexed = pd.Series(returns.to_numpy(), index=pd.DatetimeIndex(df["date"].iloc[1:]))
        periods = analytics.period_returns(close, {"1m": 21, "3m": 63, "1y": 252})

        return PeerComparisonEntry(
            symbol=symbol.upper(),
            change_percent_1m=periods.get("1m"),
            change_percent_3m=periods.get("3m"),
            change_percent_1y=periods.get("1y"),
            annualized_volatility=analytics.annualized_volatility(returns),
            sharpe_ratio=analytics.sharpe_ratio(returns),
            max_drawdown=analytics.max_drawdown(close)["max_drawdown"],
            correlation=analytics.correlation(indexed, base_returns),
        )

    async def get_correlation_matrix(self, symbols: List[str], window_days: int = 252) -> CorrelationResponse:
        """Pairwise return correlation for a basket of symbols."""
        unique = sorted({s.upper() for s in symbols})
        closes = self.repository.get_close_matrix(unique, window_days)
        matrix: Dict[str, Dict[str, Optional[float]]] = {}

        if closes.empty:
            return CorrelationResponse(symbols=unique, window_days=window_days, matrix={})

        returns = closes.pct_change().dropna(how="all")
        corr = returns.corr()
        for row in corr.index:
            matrix[row] = {
                col: (None if pd.isna(corr.loc[row, col]) else round(float(corr.loc[row, col]), 6))
                for col in corr.columns
            }

        return CorrelationResponse(
            symbols=list(corr.index),
            window_days=window_days,
            matrix=matrix,
        )

    # -- seasonality ------------------------------------------------------

    async def get_seasonality(self, symbol: str, granularity: str = "month") -> Optional[SeasonalityResponse]:
        df = self.repository.get_frame(symbol)
        if df is None or len(df) < 30:
            return None

        if granularity == "weekday":
            raw = analytics.weekday_seasonality(df["date"], df["close"])
            labels = WEEKDAY_LABELS
        else:
            granularity = "month"
            raw = analytics.monthly_seasonality(df["date"], df["close"])
            labels = MONTH_LABELS

        buckets = [
            SeasonalityBucket(
                period=item["period"],
                label=_label_for(labels, item["period"], granularity),
                average_return=item["average_return"],
                median_return=item["median_return"],
                positive_rate=item["positive_rate"],
                sample_size=item["sample_size"],
            )
            for item in raw
        ]

        return SeasonalityResponse(symbol=symbol.upper(), granularity=granularity, buckets=buckets)

    # -- aggregate profile ------------------------------------------------

    async def get_symbol_profile(
        self,
        symbol: str,
        benchmark: Optional[str] = None,
        prediction: Optional[Dict[str, object]] = None,
    ) -> Optional[SymbolProfileResponse]:
        """One call that assembles the full picture for a symbol."""
        technical = await self.get_technical_analysis(symbol)
        if technical is None:
            return None

        risk = await self.get_risk_metrics(symbol, 252, benchmark)
        if risk is None:
            risk = RiskMetrics(symbol=symbol.upper(), window_days=252, metrics={})

        return SymbolProfileResponse(
            symbol=symbol.upper(),
            quote=technical.quote,
            technical=technical,
            risk=risk,
            levels=technical.levels,
            performance=technical.performance,
            prediction=prediction,
        )

    def clear_cache(self) -> None:
        self.cache.clear()


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------

def classify_trend(snapshot: Dict[str, Optional[float]]) -> TrendDirection:
    """Classify the trend from moving-average stacking and ADX."""
    close = snapshot.get("close")
    sma20, sma50, sma200 = snapshot.get("sma_20"), snapshot.get("sma_50"), snapshot.get("sma_200")
    adx_value = snapshot.get("adx_14")

    if close is None:
        return TrendDirection.NEUTRAL

    available = [v for v in (sma20, sma50, sma200) if v is not None]
    if not available:
        return TrendDirection.NEUTRAL

    above = sum(1 for v in available if close > v)
    below = len(available) - above
    strong = adx_value is not None and adx_value >= 25

    if above == len(available) and len(available) >= 2:
        stacked = (
            sma20 is not None and sma50 is not None and sma20 > sma50
            and (sma200 is None or sma50 > sma200)
        )
        if strong and stacked:
            return TrendDirection.STRONG_UPTREND
        return TrendDirection.UPTREND
    if below == len(available) and len(available) >= 2:
        stacked = (
            sma20 is not None and sma50 is not None and sma20 < sma50
            and (sma200 is None or sma50 < sma200)
        )
        if strong and stacked:
            return TrendDirection.STRONG_DOWNTREND
        return TrendDirection.DOWNTREND
    if above > below:
        return TrendDirection.UPTREND
    if below > above:
        return TrendDirection.DOWNTREND
    return TrendDirection.NEUTRAL


def build_signals(snapshot: Dict[str, Optional[float]], quote: SymbolQuote) -> List[TechnicalSignal]:
    """Translate raw indicator values into discrete, explainable observations."""
    signals: List[TechnicalSignal] = []
    close = snapshot.get("close")

    rsi_value = snapshot.get("rsi_14")
    if rsi_value is not None:
        if rsi_value >= 70:
            signals.append(TechnicalSignal(
                name="RSI(14)", verdict="overbought",
                detail=f"RSI at {rsi_value:.1f} — momentum is stretched and prone to a pullback.",
                bullish=False, weight=1.0))
        elif rsi_value <= 30:
            signals.append(TechnicalSignal(
                name="RSI(14)", verdict="oversold",
                detail=f"RSI at {rsi_value:.1f} — selling looks exhausted, a bounce is possible.",
                bullish=True, weight=1.0))
        elif rsi_value >= 55:
            signals.append(TechnicalSignal(
                name="RSI(14)", verdict="bullish",
                detail=f"RSI at {rsi_value:.1f} sits in bullish territory.",
                bullish=True, weight=0.5))
        elif rsi_value <= 45:
            signals.append(TechnicalSignal(
                name="RSI(14)", verdict="bearish",
                detail=f"RSI at {rsi_value:.1f} sits in bearish territory.",
                bullish=False, weight=0.5))
        else:
            signals.append(TechnicalSignal(
                name="RSI(14)", verdict="neutral",
                detail=f"RSI at {rsi_value:.1f} is balanced.", bullish=None, weight=0.5))

    macd_value, macd_signal = snapshot.get("macd"), snapshot.get("macd_signal")
    if macd_value is not None and macd_signal is not None:
        bullish = macd_value > macd_signal
        signals.append(TechnicalSignal(
            name="MACD", verdict="bullish" if bullish else "bearish",
            detail=(
                f"MACD {macd_value:.3f} is {'above' if bullish else 'below'} its signal line "
                f"{macd_signal:.3f}."
            ),
            bullish=bullish, weight=1.0))

    for label, key in (("SMA(20)", "sma_20"), ("SMA(50)", "sma_50"), ("SMA(200)", "sma_200")):
        value = snapshot.get(key)
        if value is None or close is None:
            continue
        bullish = close > value
        distance = (close - value) / value if value else 0.0
        signals.append(TechnicalSignal(
            name=label, verdict="above" if bullish else "below",
            detail=f"Price is {abs(distance) * 100:.1f}% {'above' if bullish else 'below'} the {label}.",
            bullish=bullish, weight=1.0 if key != "sma_200" else 1.5))

    sma50, sma200 = snapshot.get("sma_50"), snapshot.get("sma_200")
    if sma50 is not None and sma200 is not None:
        bullish = sma50 > sma200
        signals.append(TechnicalSignal(
            name="Golden/Death cross", verdict="golden_cross" if bullish else "death_cross",
            detail=(
                "The 50-day average is above the 200-day average (golden cross structure)."
                if bullish else
                "The 50-day average is below the 200-day average (death cross structure)."
            ),
            bullish=bullish, weight=1.5))

    percent_b = snapshot.get("bb_percent_b")
    if percent_b is not None:
        if percent_b > 1:
            signals.append(TechnicalSignal(
                name="Bollinger Bands", verdict="above_upper_band",
                detail="Price closed above the upper band — an extended move.",
                bullish=False, weight=0.5))
        elif percent_b < 0:
            signals.append(TechnicalSignal(
                name="Bollinger Bands", verdict="below_lower_band",
                detail="Price closed below the lower band — a washed-out move.",
                bullish=True, weight=0.5))
        else:
            signals.append(TechnicalSignal(
                name="Bollinger Bands", verdict="inside_bands",
                detail=f"Price sits at {percent_b * 100:.0f}% of the band range.",
                bullish=None, weight=0.25))

    adx_value = snapshot.get("adx_14")
    plus_di, minus_di = snapshot.get("plus_di"), snapshot.get("minus_di")
    if adx_value is not None:
        if adx_value >= 25 and plus_di is not None and minus_di is not None:
            bullish = plus_di > minus_di
            signals.append(TechnicalSignal(
                name="ADX(14)", verdict="trending",
                detail=(
                    f"ADX {adx_value:.1f} confirms a {'bullish' if bullish else 'bearish'} trend "
                    f"(+DI {plus_di:.1f} vs -DI {minus_di:.1f})."
                ),
                bullish=bullish, weight=1.0))
        else:
            signals.append(TechnicalSignal(
                name="ADX(14)", verdict="range_bound",
                detail=f"ADX {adx_value:.1f} indicates a weak or range-bound trend.",
                bullish=None, weight=0.5))

    volume_ratio = snapshot.get("volume_ratio")
    if volume_ratio is not None:
        if volume_ratio >= 2:
            direction = quote.change_percent or 0.0
            signals.append(TechnicalSignal(
                name="Volume", verdict="spike",
                detail=f"Volume is {volume_ratio:.1f}x its 20-day average — conviction behind the move.",
                bullish=direction > 0, weight=1.0))
        elif volume_ratio <= 0.5:
            signals.append(TechnicalSignal(
                name="Volume", verdict="thin",
                detail=f"Volume is only {volume_ratio:.1f}x its 20-day average — little participation.",
                bullish=None, weight=0.25))

    week52_high, week52_low = quote.week52_high, quote.week52_low
    if close is not None and week52_high and week52_low and week52_high > week52_low:
        position = (close - week52_low) / (week52_high - week52_low)
        if position >= 0.95:
            verdict, bullish, text = "near_52w_high", True, "Price is within 5% of its 52-week high."
        elif position <= 0.05:
            verdict, bullish, text = "near_52w_low", False, "Price is within 5% of its 52-week low."
        else:
            verdict, bullish, text = (
                "mid_range", None, f"Price sits at {position * 100:.0f}% of its 52-week range.")
        signals.append(TechnicalSignal(
            name="52-week range", verdict=verdict, detail=text, bullish=bullish, weight=0.75))

    return signals


def aggregate_signal_score(signals: List[TechnicalSignal]) -> float:
    """Weighted net bullishness across all signals, normalised to [-1, 1]."""
    total_weight = sum(s.weight for s in signals if s.bullish is not None)
    if not total_weight:
        return 0.0
    score = sum((1.0 if s.bullish else -1.0) * s.weight for s in signals if s.bullish is not None)
    return round(score / total_weight, 4)


def classify_signal(score: float) -> SignalStrength:
    """Bucket the aggregate score into a coarse stance."""
    if score >= 0.6:
        return SignalStrength.STRONGLY_BULLISH
    if score >= 0.2:
        return SignalStrength.BULLISH
    if score <= -0.6:
        return SignalStrength.STRONGLY_BEARISH
    if score <= -0.2:
        return SignalStrength.BEARISH
    return SignalStrength.NEUTRAL


def classify_risk_level(annualized_volatility: Optional[float]) -> Optional[RiskLevel]:
    """Bucket annualised volatility into a human-friendly risk label."""
    if annualized_volatility is None:
        return None
    if annualized_volatility < 0.20:
        return RiskLevel.LOW
    if annualized_volatility < 0.35:
        return RiskLevel.MODERATE
    if annualized_volatility < 0.55:
        return RiskLevel.HIGH
    return RiskLevel.VERY_HIGH


_TREND_TEXT = {
    TrendDirection.STRONG_UPTREND: "in a strong uptrend",
    TrendDirection.UPTREND: "in an uptrend",
    TrendDirection.NEUTRAL: "trendless",
    TrendDirection.DOWNTREND: "in a downtrend",
    TrendDirection.STRONG_DOWNTREND: "in a strong downtrend",
}

_SIGNAL_TEXT = {
    SignalStrength.STRONGLY_BULLISH: "The technical picture is strongly bullish",
    SignalStrength.BULLISH: "The technical picture leans bullish",
    SignalStrength.NEUTRAL: "The technical picture is mixed",
    SignalStrength.BEARISH: "The technical picture leans bearish",
    SignalStrength.STRONGLY_BEARISH: "The technical picture is strongly bearish",
}


def build_narrative(
    symbol: str,
    quote: SymbolQuote,
    trend: TrendDirection,
    strength: SignalStrength,
    snapshot: Dict[str, Optional[float]],
    signals: List[TechnicalSignal],
    levels: SupportResistanceLevels,
) -> str:
    """Compose a short plain-language summary of the technical state."""
    parts: List[str] = []

    change_text = ""
    if quote.change_percent is not None:
        change_text = f" ({quote.change_percent * 100:+.2f}% on the session)"
    parts.append(f"{symbol} closed at {quote.close}{change_text} and is {_TREND_TEXT[trend]}.")

    parts.append(f"{_SIGNAL_TEXT[strength]}.")

    highlights = [s for s in signals if s.bullish is not None and s.weight >= 1.0]
    if highlights:
        bullish_names = [s.name for s in highlights if s.bullish]
        bearish_names = [s.name for s in highlights if not s.bullish]
        if bullish_names:
            parts.append(f"Supportive: {', '.join(bullish_names)}.")
        if bearish_names:
            parts.append(f"Cautionary: {', '.join(bearish_names)}.")

    volatility = snapshot.get("volatility_20")
    if volatility is not None:
        risk_level = classify_risk_level(volatility)
        parts.append(
            f"20-day annualised volatility is {volatility * 100:.1f}% "
            f"({risk_level.value.replace('_', ' ') if risk_level else 'n/a'} risk)."
        )

    if levels.support:
        parts.append(f"Nearest support around {levels.support[0]}.")
    if levels.resistance:
        parts.append(f"Nearest resistance around {levels.resistance[0]}.")

    parts.append("This is a rule-based technical read-out, not investment advice.")
    return " ".join(parts)


def _label_for(labels: List[str], period: int, granularity: str) -> str:
    index = period - 1 if granularity == "month" else period
    if 0 <= index < len(labels):
        return labels[index]
    return str(period)


def _f(value) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    number = float(value)
    if not np.isfinite(number):
        return None
    return round(number, 6)


analysis_service = AnalysisService()
