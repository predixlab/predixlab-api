"""Pydantic models for market intelligence, analysis, screening and watchlists.

These models back the endpoints that extend PredixLab beyond raw quantitative
output: market overview, per-symbol deep analysis, the stock screener and user
watchlists/alerts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_serializer


def _serialize_utc(dt: datetime) -> str:
    """Serialize a datetime to ISO 8601 with a ``Z`` suffix."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TrendDirection(str, Enum):
    """Overall trend classification derived from moving average structure."""

    STRONG_UPTREND = "strong_uptrend"
    UPTREND = "uptrend"
    NEUTRAL = "neutral"
    DOWNTREND = "downtrend"
    STRONG_DOWNTREND = "strong_downtrend"


class SignalStrength(str, Enum):
    """Aggregated technical stance."""

    STRONGLY_BULLISH = "strongly_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    STRONGLY_BEARISH = "strongly_bearish"

    @classmethod
    def _missing_(cls, value: object) -> Optional["SignalStrength"]:
        legacy = {
            "strong_buy": cls.STRONGLY_BULLISH,
            "buy": cls.BULLISH,
            "sell": cls.BEARISH,
            "strong_sell": cls.STRONGLY_BEARISH,
        }
        return legacy.get(value) if isinstance(value, str) else None


class MoverCategory(str, Enum):
    """Supported market mover rankings."""

    GAINERS = "gainers"
    LOSERS = "losers"
    MOST_ACTIVE = "most_active"
    UNUSUAL_VOLUME = "unusual_volume"
    GAP_UP = "gap_up"
    GAP_DOWN = "gap_down"
    NEW_HIGH_52W = "new_high_52w"
    NEW_LOW_52W = "new_low_52w"


class RiskLevel(str, Enum):
    """Coarse volatility bucket used for at-a-glance risk labelling."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class AlertType(str, Enum):
    """Conditions a user can subscribe to for a watchlist symbol."""

    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    PERCENT_CHANGE_UP = "percent_change_up"
    PERCENT_CHANGE_DOWN = "percent_change_down"
    RSI_ABOVE = "rsi_above"
    RSI_BELOW = "rsi_below"
    VOLUME_SPIKE = "volume_spike"
    CROSS_ABOVE_SMA50 = "cross_above_sma50"
    CROSS_BELOW_SMA50 = "cross_below_sma50"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


# ---------------------------------------------------------------------------
# Quotes and movers
# ---------------------------------------------------------------------------

class SymbolQuote(BaseModel):
    """Latest session snapshot for a single symbol."""

    symbol: str
    date: Optional[str] = None
    close: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[int] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume_ratio: Optional[float] = Field(
        None, description="Latest volume divided by its 20-day average"
    )
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None


class MoverEntry(SymbolQuote):
    """A quote enriched with the metric that earned it a place on a ranking."""

    metric: Optional[float] = None
    metric_name: Optional[str] = None


class MoversResponse(BaseModel):
    category: MoverCategory
    date: Optional[str] = None
    entries: List[MoverEntry] = Field(default_factory=list)
    total_count: int = 0


# ---------------------------------------------------------------------------
# Market overview / breadth
# ---------------------------------------------------------------------------

class MarketBreadth(BaseModel):
    """How broad the move is across the universe on a given session."""

    date: Optional[str] = None
    total_symbols: int = 0
    advancers: int = 0
    decliners: int = 0
    unchanged: int = 0
    unchanged_symbols: List[str] = Field(default_factory=list)
    advance_decline_ratio: Optional[float] = None
    percent_advancing: Optional[float] = None
    above_sma20_percent: Optional[float] = None
    above_sma50_percent: Optional[float] = None
    above_sma200_percent: Optional[float] = None
    new_highs_52w: int = 0
    new_lows_52w: int = 0
    average_change_percent: Optional[float] = None
    median_change_percent: Optional[float] = None
    total_volume: Optional[int] = None


class MarketSentimentComponent(BaseModel):
    """One normalized input to the composite market sentiment score."""

    raw_value: Optional[float] = None
    score: Optional[float] = Field(None, ge=0, le=100)
    weight: float = Field(..., ge=0, le=1)


class MarketSentiment(BaseModel):
    """A 0-100 composite sentiment gauge with its contributing components."""

    score: float = Field(..., ge=0, le=100)
    label: str
    components: Dict[str, MarketSentimentComponent] = Field(default_factory=dict)
    summary: str = ""


class MarketOverviewResponse(BaseModel):
    date: Optional[str] = None
    breadth: MarketBreadth
    sentiment: MarketSentiment
    top_gainers: List[MoverEntry] = Field(default_factory=list)
    top_losers: List[MoverEntry] = Field(default_factory=list)
    most_active: List[MoverEntry] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_serializer("generated_at")
    def serialize_generated_at(self, dt: datetime, _info) -> str:
        return _serialize_utc(dt)


class PerformanceDistributionBucket(BaseModel):
    label: str
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    count: int = 0
    percent: Optional[float] = None


class PerformanceDistributionResponse(BaseModel):
    date: Optional[str] = None
    window_days: int
    buckets: List[PerformanceDistributionBucket] = Field(default_factory=list)
    total_symbols: int = 0


class HeatmapEntry(BaseModel):
    symbol: str
    change_percent: Optional[float] = None
    volume: Optional[int] = None
    close: Optional[float] = None


class HeatmapResponse(BaseModel):
    date: Optional[str] = None
    window_days: int
    entries: List[HeatmapEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-symbol analysis
# ---------------------------------------------------------------------------

class IndicatorSnapshot(BaseModel):
    """Latest value of every supported technical indicator."""

    symbol: str
    date: Optional[str] = None
    values: Dict[str, Optional[float]] = Field(default_factory=dict)


class IndicatorSeriesPoint(BaseModel):
    date: str
    values: Dict[str, Optional[float]] = Field(default_factory=dict)


class IndicatorSeriesResponse(BaseModel):
    symbol: str
    indicators: List[str] = Field(default_factory=list)
    points: List[IndicatorSeriesPoint] = Field(default_factory=list)
    total_records: int = 0


class SupportResistanceLevels(BaseModel):
    support: List[float] = Field(default_factory=list)
    resistance: List[float] = Field(default_factory=list)
    pivot: Dict[str, float] = Field(default_factory=dict)


class TechnicalSignal(BaseModel):
    """One human readable observation produced by the rule engine."""

    name: str
    verdict: str
    detail: str
    bullish: Optional[bool] = None
    weight: float = 1.0


class RiskMetrics(BaseModel):
    symbol: str
    window_days: int
    benchmark: Optional[str] = None
    metrics: Dict[str, Optional[float]] = Field(default_factory=dict)
    risk_level: Optional[RiskLevel] = None


class TechnicalAnalysisResponse(BaseModel):
    symbol: str
    date: Optional[str] = None
    quote: SymbolQuote
    trend: TrendDirection
    signal: SignalStrength
    signal_score: float = Field(..., description="Net bullish score in the range [-1, 1]")
    indicators: Dict[str, Optional[float]] = Field(default_factory=dict)
    signals: List[TechnicalSignal] = Field(default_factory=list)
    levels: SupportResistanceLevels = Field(default_factory=SupportResistanceLevels)
    performance: Dict[str, Optional[float]] = Field(default_factory=dict)
    narrative: str = ""


class PeerComparisonEntry(BaseModel):
    symbol: str
    change_percent_1m: Optional[float] = None
    change_percent_3m: Optional[float] = None
    change_percent_1y: Optional[float] = None
    annualized_volatility: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    correlation: Optional[float] = None
    relative_strength: Optional[float] = Field(
        None, description="Symbol return minus peer-group average return over the window"
    )


class PeerComparisonResponse(BaseModel):
    symbol: str
    window_days: int
    peers: List[PeerComparisonEntry] = Field(default_factory=list)
    base: Optional[PeerComparisonEntry] = None


class CorrelationResponse(BaseModel):
    symbols: List[str] = Field(default_factory=list)
    window_days: int
    matrix: Dict[str, Dict[str, Optional[float]]] = Field(default_factory=dict)


class SeasonalityBucket(BaseModel):
    period: int
    label: str
    average_return: Optional[float] = None
    median_return: Optional[float] = None
    positive_rate: Optional[float] = None
    sample_size: int = 0


class SeasonalityResponse(BaseModel):
    symbol: str
    granularity: str
    buckets: List[SeasonalityBucket] = Field(default_factory=list)


class SymbolProfileResponse(BaseModel):
    """A single call that assembles everything worth knowing about a symbol."""

    symbol: str
    quote: SymbolQuote
    technical: TechnicalAnalysisResponse
    risk: RiskMetrics
    levels: SupportResistanceLevels = Field(default_factory=SupportResistanceLevels)
    performance: Dict[str, Optional[float]] = Field(default_factory=dict)
    prediction: Optional[Dict[str, Any]] = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_serializer("generated_at")
    def serialize_generated_at(self, dt: datetime, _info) -> str:
        return _serialize_utc(dt)


# ---------------------------------------------------------------------------
# Screener
# ---------------------------------------------------------------------------

class StockIndex(str, Enum):
    """The index universes the backend tracks.

    ``ALL`` is their union, which is also the default universe: the backend
    covers current S&P 500 and NASDAQ-100 constituents and nothing else.
    """

    ALL = "all"
    SP500 = "sp500"
    NASDAQ100 = "nasdaq100"


class ScreenerFilters(BaseModel):
    """Criteria applied to the derived metric set.

    Grouped by the question they answer — liquidity, momentum, trend structure,
    risk, position in the yearly range — which is also how the UI lays them out.
    Every field is optional and an unset field is not a constraint.
    """

    index: StockIndex = Field(
        StockIndex.ALL, description="Restrict the scan to one index universe"
    )

    # Price and liquidity
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    min_volume: Optional[int] = None
    min_avg_volume: Optional[int] = Field(
        None, description="Minimum 20-day average volume, a steadier liquidity floor than one session"
    )
    min_volume_ratio: Optional[float] = None
    max_volume_ratio: Optional[float] = Field(
        None, description="Upper bound on volume vs its average; finds quiet tape"
    )

    # Momentum
    min_change_percent: Optional[float] = None
    max_change_percent: Optional[float] = None
    min_return_1m: Optional[float] = None
    max_return_1m: Optional[float] = None
    min_return_3m: Optional[float] = None
    max_return_3m: Optional[float] = None
    min_return_1y: Optional[float] = None
    max_return_1y: Optional[float] = None
    min_rsi: Optional[float] = Field(None, ge=0, le=100)
    max_rsi: Optional[float] = Field(None, ge=0, le=100)

    # Trend structure
    trends: Optional[List[TrendDirection]] = Field(
        None, description="Keep only these trend classifications"
    )
    above_sma20: Optional[bool] = None
    above_sma50: Optional[bool] = None
    above_sma200: Optional[bool] = None
    macd_bullish: Optional[bool] = None
    golden_cross: Optional[bool] = None

    # Risk
    min_volatility: Optional[float] = None
    max_volatility: Optional[float] = None
    max_drawdown_floor: Optional[float] = Field(
        None, description="Keep symbols whose max drawdown is not worse than this (e.g. -0.3)"
    )

    # Position in the 52-week range. The distances are signed: distance to the
    # high is negative below it, distance to the low positive above it, so a
    # range on each expresses both "near" and "far from" without a second flag.
    min_distance_to_52w_high: Optional[float] = None
    max_distance_to_52w_high: Optional[float] = None
    min_distance_to_52w_low: Optional[float] = None
    max_distance_to_52w_low: Optional[float] = None
    near_52w_high_percent: Optional[float] = Field(
        None, description="Keep symbols within this percent of their 52-week high"
    )
    near_52w_low_percent: Optional[float] = Field(
        None, description="Keep symbols within this percent of their 52-week low"
    )

    symbols: Optional[List[str]] = Field(None, description="Restrict the universe to these symbols")


class ScreenerRequest(BaseModel):
    filters: ScreenerFilters = Field(default_factory=ScreenerFilters)
    sort_by: str = "change_percent"
    sort_direction: SortDirection = SortDirection.DESC
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class ScreenerMatch(BaseModel):
    symbol: str
    date: Optional[str] = None
    close: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[int] = None
    avg_volume_20: Optional[int] = None
    volume_ratio: Optional[float] = None
    rsi_14: Optional[float] = None
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    return_1m: Optional[float] = None
    return_3m: Optional[float] = None
    return_1y: Optional[float] = None
    volatility: Optional[float] = None
    max_drawdown: Optional[float] = None
    distance_to_52w_high: Optional[float] = None
    distance_to_52w_low: Optional[float] = None
    trend: Optional[TrendDirection] = None
    indexes: List[str] = Field(
        default_factory=list, description="Index universes this symbol belongs to"
    )


class ScreenerResponse(BaseModel):
    matches: List[ScreenerMatch] = Field(default_factory=list)
    date: Optional[str] = None
    total_count: int = 0
    limit: int = 50
    offset: int = 0
    universe_size: int = Field(
        0, description="Symbols scanned, after the index filter and before the criteria"
    )


class ScreenerPreset(BaseModel):
    """A named, ready-to-run screening strategy."""

    id: str
    name: str
    description: str
    filters: ScreenerFilters
    sort_by: str = "change_percent"
    sort_direction: SortDirection = SortDirection.DESC


class ScreenerPresetsResponse(BaseModel):
    presets: List[ScreenerPreset] = Field(default_factory=list)


class ScreenerStrategy(BaseModel):
    """A screen a user named and kept.

    Same shape as a preset minus the built-in description: the point of saving
    one is that the criteria *are* the description.
    """

    id: Optional[str] = None
    user_id: str
    name: str
    filters: ScreenerFilters = Field(default_factory=ScreenerFilters)
    sort_by: str = "change_percent"
    sort_direction: SortDirection = SortDirection.DESC
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_serializer("created_at", "updated_at")
    def serialize_timestamps(self, dt: datetime, _info) -> str:
        return _serialize_utc(dt)


class ScreenerStrategyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    filters: ScreenerFilters = Field(default_factory=ScreenerFilters)
    sort_by: str = "change_percent"
    sort_direction: SortDirection = SortDirection.DESC


class ScreenerStrategyUpdateRequest(BaseModel):
    """Every field optional so a rename does not have to resend the criteria."""

    name: Optional[str] = Field(None, min_length=1, max_length=64)
    filters: Optional[ScreenerFilters] = None
    sort_by: Optional[str] = None
    sort_direction: Optional[SortDirection] = None


class ScreenerStrategyListResponse(BaseModel):
    strategies: List[ScreenerStrategy] = Field(default_factory=list)
    total_count: int = 0


# ---------------------------------------------------------------------------
# Watchlist and alerts
# ---------------------------------------------------------------------------

class WatchlistItem(BaseModel):
    symbol: str
    note: Optional[str] = None
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_serializer("added_at")
    def serialize_added_at(self, dt: datetime, _info) -> str:
        return _serialize_utc(dt)


class Watchlist(BaseModel):
    id: Optional[str] = None
    user_id: str
    name: str = "Default"
    items: List[WatchlistItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_serializer("created_at", "updated_at")
    def serialize_timestamps(self, dt: datetime, _info) -> str:
        return _serialize_utc(dt)


class WatchlistCreateRequest(BaseModel):
    name: str = Field("Default", min_length=1, max_length=64)
    symbols: List[str] = Field(default_factory=list)


class WatchlistSymbolRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    note: Optional[str] = Field(None, max_length=280)


class WatchlistListResponse(BaseModel):
    watchlists: List[Watchlist] = Field(default_factory=list)
    total_count: int = 0


class WatchlistQuotesResponse(BaseModel):
    watchlist_id: str
    name: str
    quotes: List[SymbolQuote] = Field(default_factory=list)
    total_count: int = 0


class Alert(BaseModel):
    id: Optional[str] = None
    user_id: str
    symbol: str
    alert_type: AlertType
    threshold: Optional[float] = None
    is_active: bool = True
    triggered_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_serializer("created_at")
    def serialize_created_at(self, dt: datetime, _info) -> str:
        return _serialize_utc(dt)

    @field_serializer("triggered_at", "last_checked_at")
    def serialize_optional_timestamps(self, dt: Optional[datetime], _info) -> Optional[str]:
        return _serialize_utc(dt) if dt else None


class AlertCreateRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    alert_type: AlertType
    threshold: Optional[float] = None
    note: Optional[str] = Field(None, max_length=280)


class AlertListResponse(BaseModel):
    alerts: List[Alert] = Field(default_factory=list)
    total_count: int = 0


class TriggeredAlert(BaseModel):
    alert: Alert
    current_value: Optional[float] = None
    message: str


class AlertCheckResponse(BaseModel):
    checked: int = 0
    triggered: List[TriggeredAlert] = Field(default_factory=list)
