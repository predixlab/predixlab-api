"""Schemas for the pivot pick list.

`predixlab-pivot` scores the whole universe every session, then publishes only
the short list of detected events: the top names per side, ranked by conviction
within that day's cross-section. The full scored cross-section is a model
artifact and stays in the job's artifacts tree.

Documents live in `pivot_picks`, keyed by `(market, date, symbol, direction)`.
Historical rows additionally carry the realised outcome from the walk-forward
backtest, which is what lets the product show how past picks actually turned
out rather than only what it predicted.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class PickDirection(str, Enum):
    """Side of the book. There is no `flat`: a HOLD is not a pick."""

    LONG = "long"
    SHORT = "short"


class PickEvent(str, Enum):
    """What fired. A pick *is* one of these — it is not a ranking."""

    BUY_TURN = "BUY_TURN"
    SELL_TURN = "SELL_TURN"
    TREND_BUY = "TREND_BUY"
    BASE_BREAKOUT = "BASE_BREAKOUT"


class PickKind(str, Enum):
    """Which question the event answers."""

    #: Price was in the extreme of its range and a reversal bar confirmed.
    REVERSAL = "reversal"
    #: A directional breakout entry — joining an advance or the first move out
    #: of a base, not calling a turn. Different trade, different holding pattern.
    TREND = "trend"


class PivotAction(str, Enum):
    """The model's *state* label on the bar that triggered the event.

    Context only. It does **not** determine the side: an event is dated by its
    trigger bar while its setup may be up to three sessions earlier, so by the
    time a reversal confirms the state has often decayed to ``HOLD`` or flipped
    outright. ``direction`` is the decision.
    """

    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class PivotPick(BaseModel):
    """One published model event for one symbol on one session."""

    date: str = Field(..., description="Session the pick was published for (YYYY-MM-DD)")
    market: str = Field(default="us")
    symbol: str
    event: PickEvent
    kind: PickKind
    direction: PickDirection
    rank: int = Field(
        ...,
        description="Presentation order within its side that day; it gates nothing",
    )
    horizon: int = Field(default=10, description="Prediction horizon in trading days")

    action: PivotAction
    score: float = Field(..., description="Signed conviction; positive is bullish")
    confidence: float = Field(
        ..., description="Conviction percentile within the day's full cross-section"
    )
    swing_position: Optional[float] = Field(
        default=None, description="0 = expected local low, 1 = expected local high"
    )
    p_up: Optional[float] = Field(
        default=None, description="Probability the horizon return is positive"
    )

    sustained_bullish: Optional[float] = Field(
        default=None,
        description="Share of the last 40 sessions the model called bullish (context, not a gate)",
    )
    rationale: Optional[str] = Field(
        default=None, description="'; '-separated plain-language drivers"
    )

    close: Optional[float] = None
    price: Optional[float] = Field(default=None, description="Un-rebased tradeable close")

    # What the name did over the horizon after the signal fired. Null while the
    # horizon has not elapsed — those signals are still open, not missing.
    forward_return: Optional[float] = Field(
        default=None, description="The stock's own return over the horizon"
    )
    signal_return: Optional[float] = Field(
        default=None,
        description="The same return from the position's side; negated for shorts",
    )
    excess_return: Optional[float] = Field(
        default=None,
        description="signal_return with the day's common market move removed",
    )

    # The path the position took, not just where it ended. Computed by the
    # backend from its own OHLC archive rather than published by the pivot job,
    # which owns signal detection only.
    max_gain: Optional[float] = Field(
        default=None,
        description="Best the position was ever up during the horizon, intraday highs included",
    )
    max_loss: Optional[float] = Field(
        default=None,
        description="Worst the position was ever down during the horizon, intraday lows included",
    )
    days_to_max_gain: Optional[int] = Field(
        default=None, description="Trading days from the signal to its best point"
    )
    days_to_max_loss: Optional[int] = Field(
        default=None, description="Trading days from the signal to its worst point"
    )
    path_complete: Optional[bool] = Field(
        default=None,
        description="False while the horizon has not fully elapsed, so a partial path is not read as a final one",
    )

    pivot_hit: Optional[bool] = Field(
        default=None,
        description=(
            "Whether this reversal landed within a few bars of a real swing point. "
            "Null for trend entries, which are not turning points."
        ),
    )


class PivotPicksResponse(BaseModel):
    """One session's events, split by side.

    An empty response is a real answer: most sessions fire nothing, and 14 of
    754 walk-forward sessions fired nothing at all.
    """

    market: str
    date: Optional[str] = None
    horizon: Optional[int] = None
    longs: List[PivotPick] = Field(default_factory=list)
    shorts: List[PivotPick] = Field(default_factory=list)
    total_count: int = 0
    counts: Dict[str, int] = Field(
        default_factory=dict, description="Events by type for this session"
    )


class PivotSymbolHistoryResponse(BaseModel):
    market: str
    symbol: str
    count: int = 0
    items: List[PivotPick] = Field(default_factory=list)


class PivotAvailableDatesResponse(BaseModel):
    market: str
    dates: List[str] = Field(default_factory=list)
    count: int = 0


class PivotPerformance(BaseModel):
    """What the signals did over the horizon that followed them.

    This is not a strategy backtest. It reports observed outcomes over a fixed
    horizon rather than simulating a portfolio or execution policy. The single
    question is: after this signal fired, what did the name do over the next
    `horizon` sessions?

    Both the raw and the benchmark-relative figure are reported, because the raw
    one alone is misleading: buy events average +0.55% while the universe
    averages +0.56% on the same bars. Only the excess separates "the signal knew
    something" from "the market went up that fortnight".
    """

    market: str
    direction: Optional[str] = None
    event: Optional[str] = None
    horizon: Optional[int] = None
    #: Signals whose horizon has elapsed. Recent ones are still open.
    evaluated: int = 0
    #: Signals published but not yet resolvable.
    pending: int = 0
    win_rate: Optional[float] = Field(default=None, description="Share with a positive raw return")
    avg_return: Optional[float] = None
    median_return: Optional[float] = None
    best_return: Optional[float] = None
    worst_return: Optional[float] = None
    #: Benchmark-relative. This is the number that says whether the signal added
    #: anything over simply being in the market that day.
    excess_win_rate: Optional[float] = None
    avg_excess_return: Optional[float] = None
    median_excess_return: Optional[float] = None

    # Turning-point detection: the model's actual objective. Populated for
    # reversals only — a trend signal fires on a new 60-day high, the opposite of
    # a swing low, so scoring it against pivot labels asks the wrong question.
    detection_scored: int = 0
    detection_rate: Optional[float] = Field(
        default=None, description="Share landing within a few bars of a real swing point"
    )
    detection_base_rate: Optional[float] = Field(
        default=None, description="What the same share would be by chance"
    )
    detection_lift: Optional[float] = Field(
        default=None, description="detection_rate / detection_base_rate"
    )

    start: Optional[str] = None
    end: Optional[str] = None


class PivotPerformanceBreakdown(BaseModel):
    """Signal performance, split by event type.

    There is deliberately **no combined figure**. Longs and shorts have opposite
    raw returns by construction, so averaging them measures the mix ratio of the
    period rather than any property of the system — a genuinely uninformative
    number, not merely an unflattering one. Each signal type is reported on its
    own, against the metric that actually applies to it.
    """

    market: str
    horizon: Optional[int] = None
    by_event: List[PivotPerformance] = Field(default_factory=list)


class PivotSummary(BaseModel):
    """Headline numbers for the picks board."""

    market: str
    date: Optional[str] = None
    horizon: Optional[int] = None
    long_count: int = 0
    short_count: int = 0
    counts: Dict[str, int] = Field(
        default_factory=dict, description="Events by type on the latest session"
    )
    top_longs: List[PivotPick] = Field(default_factory=list)
    top_shorts: List[PivotPick] = Field(default_factory=list)
    generated_at: Optional[datetime] = None


class PivotRunModelInfo(BaseModel):
    trained_at: Optional[str] = None
    rows: Optional[int] = None
    symbols: Optional[int] = None
    features: Optional[int] = None
    start: Optional[str] = None
    end: Optional[str] = None
    retrained: Optional[bool] = None
    age_days: Optional[float] = None


class PivotRun(BaseModel):
    """Provenance for one execution of the nightly job."""

    market: str
    date: Optional[str] = None
    horizon: Optional[int] = None
    tag: Optional[str] = None
    status: str = "unknown"
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    symbols_scored: int = 0
    published: int = 0
    counts: Dict[str, int] = Field(default_factory=dict)
    model: Optional[PivotRunModelInfo] = None
    error: Optional[str] = Field(default=None, description="The run itself failed")
    publish_error: Optional[str] = Field(
        default=None,
        description=(
            "The run succeeded but its picks never reached the collection, "
            "so the served picks are stale"
        ),
    )

    model_config = {"protected_namespaces": ()}


def pick_from_document(document: Dict[str, Any]) -> PivotPick:
    """Build a :class:`PivotPick` from a raw MongoDB document.

    Unknown keys (``_id``, ``tag``, ``generated_at``) are dropped rather than
    rejected so the job can add provenance fields without breaking the API.
    """
    allowed = set(PivotPick.model_fields.keys())
    payload = {key: value for key, value in document.items() if key in allowed}
    payload.setdefault("market", "us")
    return PivotPick(**payload)
