from pydantic import BaseModel, Field, field_serializer
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from enum import Enum

class Direction(str, Enum):
    RISING = "rising"
    FALLING = "declining"

class MatchType(str, Enum):
    PREFIX = "prefix"
    INFIX = "infix"

class PredictionType(str, Enum):
    RISE_1D = "us_rise_1d"
    RISE_5D = "us_rise_5d"
    DECLINE_1D = "us_decline_1d"
    DECLINE_5D = "us_decline_5d"

class StockData(BaseModel):
    symbol: str
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @field_serializer('date')
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

class StockDataResponse(BaseModel):
    symbol: str
    data: List[StockData]
    total_records: int

class DailyPick(BaseModel):
    instrument: str
    direction: Direction
    score: Optional[float] = None
    final_score: Optional[float] = None
    weighted_score: Optional[float] = None
    strategy: Optional[str] = None
    date: datetime

    @field_serializer('date')
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


class Prediction(BaseModel):
    instrument: str
    us_rise_1d: Optional[float] = None
    us_rise_5d: Optional[float] = None
    us_decline_1d: Optional[float] = None
    us_decline_5d: Optional[float] = None
    date: datetime

    @field_serializer('date')
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

class PredictionsResponse(BaseModel):
    date: str
    predictions: List[Prediction]
    total_count: int

class BacktestSignal(BaseModel):
    date: datetime
    direction: Direction
    score: Optional[float] = None
    strategy: Optional[str] = None

    @field_serializer('date')
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

class BacktestPerformance(BaseModel):
    signal_date: str
    direction: Direction
    days: int
    return_pct: float
    score: Optional[float] = None

class BacktestResult(BaseModel):
    symbol: str
    period_days: int
    signals: List[BacktestSignal]
    performance: Dict[str, Any]

class AvailableDatesResponse(BaseModel):
    dates: List[str]

class TradingCalendarResponse(BaseModel):
    dates: List[str]
    total_count: int
    limit: int
    offset: int

class StockListResponse(BaseModel):
    symbols: List[str]
    total_count: int

# Cache models
class PredictionCache(BaseModel):
    date: str
    file_path: str
    data: List[Prediction]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime

    @field_serializer('created_at', 'expires_at')
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

class PickCache(BaseModel):
    date: str
    direction: Direction
    file_path: str
    data: List[DailyPick]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime

    @field_serializer('created_at', 'expires_at')
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

class StockDataCache(BaseModel):
    symbol: str
    data: List[StockData]
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime

    @field_serializer('last_updated', 'expires_at')
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

class DailyPicksResponse(BaseModel):
    date: str
    risings: List[DailyPick]
    fallings: List[DailyPick]

class LatestPredictionsResponse(BaseModel):
    date: str
    predictions: List[Prediction]
    total_count: int
