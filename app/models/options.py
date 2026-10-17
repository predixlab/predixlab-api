from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import date, datetime
from enum import Enum

DateType = date

class OptionType(str, Enum):
    CALLS = "calls"
    PUTS = "puts"

class PositionType(str, Enum):
    NATURAL = "Natural"
    NEW_CONTRACT = "NewContract"
    NEW_POSITION = "NewPosition"
    CLOSE_POSITION = "ClosePosition"
    HIGH_ACTIVITY = "HighActivity"

class AnomalyContract(BaseModel):
    symbol: str
    contract: str
    option_type: OptionType
    strike: float
    expiration: Optional[date] = None
    open_interest_today: Optional[int] = None
    open_interest_previous: Optional[int] = None
    oi_change: Optional[int] = None
    volume_today: Optional[int] = None
    volume_previous: Optional[int] = None
    volume_change: Optional[int] = None
    in_the_money: Optional[bool] = None
    last_price: Optional[float] = None
    underlying_price: Optional[float] = None
    anomaly_score: Optional[float] = None
    position_type: Optional[PositionType] = None

    class Config:
        populate_by_name = True
        orm_mode = True

class AnomalyOption(BaseModel):
    symbol: str
    date: date
    items: list[AnomalyContract] = Field(default_factory=list)
    anomaly_score: Optional[float] = None

    class Config:
        populate_by_name = True
        orm_mode = True


class OptionsAnomalyDatesResponse(BaseModel):
    dates: list[str] = Field(default_factory=list)
    count: int


class GexStrikeBucket(BaseModel):
    strike: float
    call_gex: float
    put_gex: float
    net_gex: float


class GexDecomposition(BaseModel):
    oi_change: float
    greeks: float
    new_contracts: float
    removed_contracts: float


class GexDriver(BaseModel):
    contract: str
    option_type: OptionType
    strike: float
    expiration: date
    open_interest_today: int
    open_interest_previous: int
    oi_change: int
    gex_today: float
    gex_previous: float
    gex_change: float
    share_of_absolute_change: float
    attribution: Literal[
        "oi_change",
        "greeks",
        "new_contract",
        "removed_contract",
    ]
    is_unusual: bool
    anomaly_score: Optional[float] = None


class GexScope(BaseModel):
    key: Literal["all", "7d", "30d"]
    net_gex: float
    call_gex: float
    put_gex: float
    previous_net_gex: float
    net_gex_change: float
    regime: Literal["positive", "negative", "neutral"]
    gamma_flip: Optional[float] = None
    previous_gamma_flip: Optional[float] = None
    call_wall: Optional[float] = None
    put_wall: Optional[float] = None
    previous_call_wall: Optional[float] = None
    previous_put_wall: Optional[float] = None
    decomposition: GexDecomposition
    strike_buckets: list[GexStrikeBucket] = Field(default_factory=list)
    top_drivers: list[GexDriver] = Field(default_factory=list)


class GexAnalysis(BaseModel):
    symbol: str
    date: date
    previous_date: Optional[date] = None
    generated_at: datetime
    underlying_price: float
    previous_underlying_price: Optional[float] = None
    contract_count: int
    valid_gamma_contract_count: int
    oi_gamma_coverage: float
    model: Literal["call_positive_put_negative"]
    spot_move: float
    scopes: list[GexScope] = Field(default_factory=list)


class GexAvailableDatesResponse(BaseModel):
    symbol: str
    dates: list[date] = Field(default_factory=list)
    count: int


class GexAvailableSymbolsResponse(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    count: int


GexHighlightSignal = Literal[
    "positive_gamma",
    "negative_gamma",
    "near_flip",
    "gex_change",
    "wall_shift",
    "high_activity",
]


class GexHighlight(BaseModel):
    symbol: str
    date: date
    attention_score: float
    primary_signal: GexHighlightSignal
    regime: Literal["positive", "negative", "neutral"]
    underlying_price: float
    gamma_flip_distance: Optional[float] = None
    net_gex_change: float
    net_gex_change_ratio: float
    call_wall_change: Optional[float] = None
    put_wall_change: Optional[float] = None


class GexHighlightsResponse(BaseModel):
    date: Optional[DateType] = None
    items: list[GexHighlight] = Field(default_factory=list)
    count: int


class OptionsStructureSummary(BaseModel):
    front_expiration: date
    skew_expiration: Optional[date] = None
    atm_iv: Optional[float] = None
    previous_atm_iv: Optional[float] = None
    atm_iv_change: Optional[float] = None
    downside_skew: Optional[float] = None
    previous_downside_skew: Optional[float] = None
    downside_skew_change: Optional[float] = None
    total_open_interest: int
    previous_total_open_interest: int
    total_oi_change: int
    call_oi_change: int
    put_oi_change: int
    new_contracts: int
    closed_contracts: int


class OptionsTermPoint(BaseModel):
    expiration: date
    days_to_expiry: int
    atm_iv: Optional[float] = None
    previous_atm_iv: Optional[float] = None
    atm_iv_change: Optional[float] = None
    call_open_interest: int
    put_open_interest: int
    call_oi_change: int
    put_oi_change: int


class OptionsSkewPoint(BaseModel):
    delta: float
    option_type: OptionType
    iv: Optional[float] = None
    previous_iv: Optional[float] = None
    iv_change: Optional[float] = None
    actual_delta: Optional[float] = None
    delta_distance: Optional[float] = None
    open_interest: int = 0
    volume: int = 0
    relative_spread: Optional[float] = None
    is_reliable: bool = False


class OptionsSkewExpiration(BaseModel):
    expiration: date
    days_to_expiry: int
    points: list[OptionsSkewPoint] = Field(default_factory=list)


class OptionsPositionMigration(BaseModel):
    expiration: date
    days_to_expiry: int
    strike: float
    call_open_interest: int
    previous_call_open_interest: int
    call_oi_change: int
    put_open_interest: int
    previous_put_open_interest: int
    put_oi_change: int
    total_oi_change: int
    absolute_oi_change: int
    call_volume: int
    put_volume: int
    kind: Literal["new", "closed", "increased", "decreased", "mixed"]


class OptionsStructureAnalysis(BaseModel):
    symbol: str
    date: date
    previous_date: Optional[date] = None
    generated_at: datetime
    underlying_price: float
    previous_underlying_price: Optional[float] = None
    summary: OptionsStructureSummary
    term_structure: list[OptionsTermPoint] = Field(default_factory=list)
    skew_expirations: list[OptionsSkewExpiration] = Field(default_factory=list)
    position_migrations: list[OptionsPositionMigration] = Field(
        default_factory=list
    )


OptionsStructureHighlightSignal = Literal[
    "iv_expansion",
    "iv_crush",
    "downside_hedging",
    "upside_chasing",
    "position_rotation",
    "term_dislocation",
]


class OptionsStructureHighlight(BaseModel):
    symbol: str
    date: date
    underlying_price: float
    attention_score: float
    primary_signal: OptionsStructureHighlightSignal
    atm_iv_change: Optional[float] = None
    downside_skew: Optional[float] = None
    downside_skew_change: Optional[float] = None
    total_oi_change: int
    total_oi_change_ratio: float
    migration_ratio: float
    term_spread: Optional[float] = None
    term_spread_change: Optional[float] = None


class OptionsStructureHighlightsResponse(BaseModel):
    date: Optional[DateType] = None
    items: list[OptionsStructureHighlight] = Field(default_factory=list)
    count: int