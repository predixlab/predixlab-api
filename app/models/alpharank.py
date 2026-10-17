"""Schemas for daily AlphaRank signals."""

from datetime import date as Date
from datetime import datetime
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field


class AlphaRankSignal(BaseModel):
    """One symbol in the AlphaRank model's daily top-15."""

    model_config = ConfigDict(extra="ignore")

    market: str = "us"
    date: str
    symbol: str
    rank: int
    horizon: int
    tag: str
    generated_at: datetime
    signal_model: Literal["alpha_rank"] = "alpha_rank"
    score: float
    consensus: float
    predicted_excess_return: float
    predicted_rank: float = Field(
        ...,
        ge=0,
        le=1,
        description="Clipped model rank_hat percentile",
    )
    top20_probability: float
    close: float | None = None
    max_gain: float | None = None
    max_loss: float | None = None
    days_to_max_gain: int | None = None
    days_to_max_loss: int | None = None
    path_complete: bool | None = None


class AlphaRankSignalsResponse(BaseModel):
    """All AlphaRank signals published for one market session."""

    market: str
    date: str
    horizon: int | None = None
    items: List[AlphaRankSignal] = Field(default_factory=list)
    total_count: int = 0


class AlphaRankAvailableDatesResponse(BaseModel):
    """AlphaRank publication dates, newest first."""

    market: str
    dates: List[str] = Field(default_factory=list)
    count: int = 0


class AlphaRankTickerHistoryResponse(BaseModel):
    """Committed AlphaRank publications for one symbol, oldest first."""

    market: str
    symbol: str
    count: int = 0
    items: List[AlphaRankSignal] = Field(default_factory=list)


def alpha_rank_from_document(document: Dict[str, Any]) -> AlphaRankSignal:
    """Safely map a Mongo document while discarding persistence-only fields."""

    allowed = set(AlphaRankSignal.model_fields)
    payload = {
        key: _normalize_value(value)
        for key, value in document.items()
        if key in allowed
    }
    payload.setdefault("market", "us")
    payload.setdefault("signal_model", "alpha_rank")
    return AlphaRankSignal.model_validate(payload)


def _normalize_value(value: Any) -> Any:
    """Convert BSON and numeric scalar values Pydantic cannot always coerce."""

    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()
    if isinstance(value, Date) and not isinstance(value, datetime):
        return value.isoformat()
    return value
