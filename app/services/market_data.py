"""Shared OHLCV repository for the market intelligence services.

The existing :class:`~app.services.stock_service.StockService` returns Pydantic
objects, which is convenient for the API but expensive when hundreds of symbols
have to be scanned. This repository instead hands out cached ``pandas``
DataFrames sorted ascending by date, which is what the indicator and analytics
libraries expect.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from app.config import settings
from app.services.cache import InMemoryCache
from app.services.universe_service import UniverseService

logger = logging.getLogger(__name__)

OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


class MarketDataRepository:
    """Loads and caches per-symbol OHLCV history from the local dataset."""

    def __init__(self, data_dir: Optional[Path] = None, index_path: Optional[Path] = None):
        self.data_dir = Path(data_dir or settings.us_stock_data_dir).expanduser()
        self.index_path = Path(index_path or settings.us_index_path).expanduser()
        # Frames are large; keep them a bit longer than the API response cache.
        self.frame_cache = InMemoryCache(default_ttl_minutes=60)
        self.universe = UniverseService(index_path=self.index_path)

    # -- universe ---------------------------------------------------------

    def get_universe(self) -> List[str]:
        """Symbols currently in a tracked index, sorted.

        Deliberately *not* "every symbol with a price file". The archive keeps
        names long after they leave an index, so listing the directory put 689
        symbols into every scan when only 516 were still constituents, and the
        extra 173 were exactly the delisted and demoted ones a screen must not
        surface. :class:`~app.services.universe_service.UniverseService` reads
        the collector's membership files instead.
        """
        return self.universe.get_universe()

    # -- frames -----------------------------------------------------------

    def get_frame(self, symbol: str) -> Optional[pd.DataFrame]:
        """Return the full OHLCV history for ``symbol`` ascending by date."""
        key = f"frame:{symbol.upper()}"
        cached = self.frame_cache.get(key)
        if cached is not None:
            # ``None`` is cached as a sentinel for "symbol has no data".
            return cached if isinstance(cached, pd.DataFrame) else None

        path = self.data_dir / f"{symbol.upper()}.csv"
        if not path.exists():
            self.frame_cache.set(key, "missing")
            return None

        try:
            df = pd.read_csv(path)
        except Exception as exc:
            logger.error(f"Failed to load {path}: {exc}")
            self.frame_cache.set(key, "missing")
            return None

        df = self._normalize(df)
        if df is None or df.empty:
            self.frame_cache.set(key, "missing")
            return None

        self.frame_cache.set(key, df)
        return df

    @staticmethod
    def _normalize(df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """Coerce a raw CSV frame into the canonical OHLCV shape."""
        if df is None or df.empty:
            return None

        df = df.rename(columns={c: str(c).strip().lower() for c in df.columns})
        missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
        if missing:
            logger.warning(f"Stock data frame missing columns: {missing}")
            return None

        df = df[OHLCV_COLUMNS].copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["date", "close"])
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        return df.reset_index(drop=True)

    def get_tail(self, symbol: str, bars: int) -> Optional[pd.DataFrame]:
        """Return the most recent ``bars`` rows for ``symbol``."""
        df = self.get_frame(symbol)
        if df is None or df.empty:
            return None
        if bars and bars > 0:
            return df.tail(bars).reset_index(drop=True)
        return df

    def get_frames(self, symbols: List[str], bars: Optional[int] = None) -> Dict[str, pd.DataFrame]:
        """Bulk-load frames, silently skipping symbols without data."""
        frames: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            df = self.get_tail(symbol, bars) if bars else self.get_frame(symbol)
            if df is not None and not df.empty:
                frames[symbol.upper()] = df
        return frames

    def get_close_matrix(self, symbols: List[str], bars: int = 252) -> pd.DataFrame:
        """Return a date-indexed frame of aligned close prices."""
        series_map: Dict[str, pd.Series] = {}
        for symbol in symbols:
            df = self.get_tail(symbol, bars)
            if df is None or df.empty:
                continue
            series_map[symbol.upper()] = pd.Series(
                df["close"].to_numpy(dtype="float64"), index=pd.DatetimeIndex(df["date"])
            )
        if not series_map:
            return pd.DataFrame()
        return pd.DataFrame(series_map).sort_index()

    def get_latest_date(self, symbol: str) -> Optional[str]:
        """Return the most recent session date for ``symbol`` as ``YYYY-MM-DD``."""
        df = self.get_frame(symbol)
        if df is None or df.empty:
            return None
        return pd.Timestamp(df["date"].iloc[-1]).strftime("%Y-%m-%d")

    def clear_cache(self) -> None:
        self.frame_cache.clear()
        self.universe.clear_cache()


market_data_repository = MarketDataRepository()
