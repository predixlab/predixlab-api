"""Multi-criteria stock screener over a collector-produced Parquet snapshot."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from app.models.market import (
    ScreenerFilters,
    ScreenerMatch,
    ScreenerPreset,
    ScreenerRequest,
    ScreenerResponse,
    SortDirection,
    StockIndex,
    TrendDirection,
)
from app.services.snapshot_storage import market_snapshot_dir

logger = logging.getLogger(__name__)

SORTABLE_FIELDS = {
    "symbol", "close", "change_percent", "volume", "volume_ratio", "rsi_14",
    "return_1m", "return_3m", "return_1y", "volatility", "max_drawdown",
    "distance_to_52w_high", "distance_to_52w_low", "avg_volume_20",
}

SCREENER_SNAPSHOT_COLUMNS = [
    "symbol",
    "date",
    "close",
    "change_percent",
    "volume",
    "avg_volume_20",
    "volume_ratio",
    "rsi_14",
    "sma_20",
    "sma_50",
    "sma_200",
    "return_1m",
    "return_3m",
    "return_1y",
    "volatility",
    "max_drawdown",
    "distance_to_52w_high",
    "distance_to_52w_low",
    "macd_bullish",
    "golden_cross",
    "trend",
    "indexes",
]


class ScreenerService:
    """Filters the collector's cached universe metric table on demand."""

    def __init__(
        self,
        snapshot_root: Optional[Path] = None,
        market: str = "us",
    ):
        self.market = market.lower()
        self.snapshot_path = (
            market_snapshot_dir(self.market, snapshot_root) / "screener.parquet"
        )
        self._snapshot_mtime_ns: Optional[int] = None
        self._set_metrics(pd.DataFrame(columns=SCREENER_SNAPSHOT_COLUMNS))

    async def initialize(self) -> None:
        self._set_metrics(pd.DataFrame(columns=SCREENER_SNAPSHOT_COLUMNS))
        self._snapshot_mtime_ns = None
        self._reload_if_changed(force=True)

    def _reload_if_changed(self, force: bool = False) -> bool:
        """Reload an atomically replaced collector snapshot when it changes."""
        try:
            modified_at = self.snapshot_path.stat().st_mtime_ns
        except FileNotFoundError:
            self._snapshot_mtime_ns = None
            if force:
                logger.warning(
                    "Screener snapshot is unavailable at %s; serving an empty table",
                    self.snapshot_path,
                )
            return False
        except OSError as exc:
            logger.warning("Could not stat screener snapshot %s: %s", self.snapshot_path, exc)
            return False

        if not force and modified_at == self._snapshot_mtime_ns:
            return False

        try:
            self._load_snapshot()
        except (OSError, KeyError, TypeError, ValueError) as exc:
            self._snapshot_mtime_ns = modified_at
            logger.warning(
                "Could not load screener snapshot %s; keeping the current table: %s",
                self.snapshot_path,
                exc,
            )
            return False

        self._snapshot_mtime_ns = modified_at
        logger.info(
            "Loaded screener snapshot %s (%d symbols)",
            self.snapshot_path,
            len(self._metrics),
        )
        return True

    def _load_snapshot(self) -> None:
        frame = pd.read_parquet(self.snapshot_path, engine="pyarrow")
        missing = set(SCREENER_SNAPSHOT_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"Screener snapshot missing columns: {sorted(missing)}")
        if "indexes" in frame:
            frame["indexes"] = frame["indexes"].apply(_decode_indexes)
        self._set_metrics(frame)

    def _set_metrics(self, frame: pd.DataFrame) -> None:
        self._metrics = frame
        self._snapshot_date = (
            str(frame["date"].mode().iloc[0])
            if not frame.empty and "date" in frame and not frame["date"].isna().all()
            else None
        )

    def build_metrics(self, symbols: Optional[List[str]] = None) -> pd.DataFrame:
        """Return cached snapshot metrics, optionally restricted to symbols."""
        self._reload_if_changed()
        frame = self._metrics
        if symbols is None:
            return frame

        wanted = {s.upper() for s in symbols}
        if frame.empty:
            return frame
        return frame[frame["symbol"].isin(wanted)].reset_index(drop=True)

    # -- filtering --------------------------------------------------------

    @staticmethod
    def _apply_filters(frame: pd.DataFrame, filters: ScreenerFilters) -> pd.DataFrame:
        """Apply every populated filter as a boolean mask."""
        if frame.empty:
            return frame

        mask = pd.Series(True, index=frame.index)

        def _ge(column: str, value: Optional[float]) -> None:
            nonlocal mask
            if value is not None and column in frame:
                mask &= frame[column].notna() & (frame[column] >= value)

        def _le(column: str, value: Optional[float]) -> None:
            nonlocal mask
            if value is not None and column in frame:
                mask &= frame[column].notna() & (frame[column] <= value)

        _ge("close", filters.min_price)
        _le("close", filters.max_price)
        _ge("volume", filters.min_volume)
        _ge("avg_volume_20", filters.min_avg_volume)
        _ge("volume_ratio", filters.min_volume_ratio)
        _le("volume_ratio", filters.max_volume_ratio)

        _ge("change_percent", filters.min_change_percent)
        _le("change_percent", filters.max_change_percent)
        _ge("rsi_14", filters.min_rsi)
        _le("rsi_14", filters.max_rsi)
        _ge("return_1m", filters.min_return_1m)
        _le("return_1m", filters.max_return_1m)
        _ge("return_3m", filters.min_return_3m)
        _le("return_3m", filters.max_return_3m)
        _ge("return_1y", filters.min_return_1y)
        _le("return_1y", filters.max_return_1y)

        _ge("volatility", filters.min_volatility)
        _le("volatility", filters.max_volatility)
        _ge("max_drawdown", filters.max_drawdown_floor)

        _ge("distance_to_52w_high", filters.min_distance_to_52w_high)
        _le("distance_to_52w_high", filters.max_distance_to_52w_high)
        _ge("distance_to_52w_low", filters.min_distance_to_52w_low)
        _le("distance_to_52w_low", filters.max_distance_to_52w_low)

        if filters.trends and "trend" in frame:
            wanted = {trend.value for trend in filters.trends}
            mask &= frame["trend"].isin(wanted)

        for column, flag in (
            ("sma_20", filters.above_sma20),
            ("sma_50", filters.above_sma50),
            ("sma_200", filters.above_sma200),
        ):
            if flag is None or column not in frame:
                continue
            valid = frame[column].notna() & frame["close"].notna()
            comparison = frame["close"] > frame[column]
            mask &= valid & (comparison if flag else ~comparison)

        for column, flag in (("macd_bullish", filters.macd_bullish), ("golden_cross", filters.golden_cross)):
            if flag is None or column not in frame:
                continue
            mask &= frame[column].notna() & (frame[column] == flag)

        if filters.near_52w_high_percent is not None and "distance_to_52w_high" in frame:
            threshold = -abs(filters.near_52w_high_percent)
            mask &= frame["distance_to_52w_high"].notna() & (frame["distance_to_52w_high"] >= threshold)

        if filters.near_52w_low_percent is not None and "distance_to_52w_low" in frame:
            threshold = abs(filters.near_52w_low_percent)
            mask &= frame["distance_to_52w_low"].notna() & (frame["distance_to_52w_low"] <= threshold)

        return frame[mask]

    def _select_universe(self, filters: ScreenerFilters) -> pd.DataFrame:
        """Rows in scope before any criterion is applied.

        The index filter and the explicit symbol list intersect rather than
        override: asking for the NASDAQ-100 *and* naming a handful of symbols
        means the ones that are in both, which is the only reading under which
        the reported universe size still describes what was scanned.
        """
        frame = self.build_metrics(filters.symbols)
        if frame.empty or filters.index == StockIndex.ALL:
            return frame

        return frame[
            frame["indexes"].apply(
                lambda indexes: filters.index.value in _decode_indexes(indexes)
            )
        ].reset_index(drop=True)

    async def screen(self, request: ScreenerRequest) -> ScreenerResponse:
        """Run a screen and return a paginated, sorted result set."""
        frame = self._select_universe(request.filters)
        universe_size = len(frame)
        date_values = frame["date"].dropna() if "date" in frame else pd.Series(dtype=str)
        snapshot_date = str(date_values.iloc[0]) if not date_values.empty else None

        if frame.empty:
            return ScreenerResponse(
                matches=[], date=snapshot_date, total_count=0,
                limit=request.limit, offset=request.offset, universe_size=0,
            )

        filtered = self._apply_filters(frame, request.filters)
        total_count = len(filtered)

        sort_by = request.sort_by if request.sort_by in SORTABLE_FIELDS else "change_percent"
        if sort_by in filtered.columns:
            ascending = request.sort_direction == SortDirection.ASC
            filtered = filtered.sort_values(sort_by, ascending=ascending, na_position="last")

        page = filtered.iloc[request.offset : request.offset + request.limit]
        matches = [self._to_match(row) for _, row in page.iterrows()]

        return ScreenerResponse(
            matches=matches,
            date=snapshot_date,
            total_count=total_count,
            limit=request.limit,
            offset=request.offset,
            universe_size=universe_size,
        )

    @staticmethod
    def _to_match(row: pd.Series) -> ScreenerMatch:
        return ScreenerMatch(
            symbol=row["symbol"],
            date=row.get("date"),
            close=_f(row.get("close")),
            change_percent=_f(row.get("change_percent")),
            volume=int(row["volume"]) if pd.notna(row.get("volume")) else None,
            avg_volume_20=int(row["avg_volume_20"]) if pd.notna(row.get("avg_volume_20")) else None,
            volume_ratio=_f(row.get("volume_ratio")),
            rsi_14=_f(row.get("rsi_14")),
            sma_20=_f(row.get("sma_20")),
            sma_50=_f(row.get("sma_50")),
            sma_200=_f(row.get("sma_200")),
            return_1m=_f(row.get("return_1m")),
            return_3m=_f(row.get("return_3m")),
            return_1y=_f(row.get("return_1y")),
            volatility=_f(row.get("volatility")),
            max_drawdown=_f(row.get("max_drawdown")),
            distance_to_52w_high=_f(row.get("distance_to_52w_high")),
            distance_to_52w_low=_f(row.get("distance_to_52w_low")),
            trend=row.get("trend"),
            indexes=_decode_indexes(row.get("indexes")),
        )

    @staticmethod
    def get_presets() -> List[ScreenerPreset]:
        """Ready-made screens so a user can start without configuring anything.

        These are the classic setups, not model output: each one is a shape a
        discretionary trader would recognise, expressed in the metrics the
        dataset already carries. The ``id`` is the stable key — the frontend
        translates the name and description from it, so an id must not change
        once shipped.
        """
        return [
            # -- trend continuation ---------------------------------------
            ScreenerPreset(
                id="momentum_leaders",
                name="Momentum leaders",
                description="Confirmed uptrend, near 52-week highs, with strong 3-month returns.",
                filters=ScreenerFilters(
                    above_sma50=True, above_sma200=True, golden_cross=True,
                    min_return_3m=0.10, near_52w_high_percent=0.10,
                ),
                sort_by="return_3m",
            ),
            ScreenerPreset(
                id="breakout_52w",
                name="52-week breakout",
                description="Trading within 2% of the yearly high on above-average volume.",
                filters=ScreenerFilters(
                    near_52w_high_percent=0.02, min_volume_ratio=1.2, above_sma50=True,
                ),
                sort_by="volume_ratio",
            ),
            ScreenerPreset(
                id="fresh_golden_cross",
                name="Fresh golden cross",
                description="The 50-day has crossed above the 200-day while price is still close to it.",
                filters=ScreenerFilters(
                    golden_cross=True, above_sma50=True,
                    min_distance_to_52w_high=-0.15, max_distance_to_52w_high=-0.02,
                ),
                sort_by="return_1m",
            ),
            ScreenerPreset(
                id="relative_strength",
                name="Relative strength",
                description="Up strongly over a year and still holding, without being extended today.",
                filters=ScreenerFilters(
                    min_return_1y=0.30, above_sma200=True, min_rsi=45, max_rsi=70,
                ),
                sort_by="return_1y",
            ),
            ScreenerPreset(
                id="steady_compounders",
                name="Steady compounders",
                description="Long uptrends with contained volatility and shallow drawdowns.",
                filters=ScreenerFilters(
                    above_sma200=True, max_volatility=0.25,
                    max_drawdown_floor=-0.25, min_return_1y=0.05,
                ),
                sort_by="return_1y",
            ),

            # -- pullbacks and reversals ----------------------------------
            ScreenerPreset(
                id="pullback_in_uptrend",
                name="Pullback in an uptrend",
                description="Long-term trend intact but trading below the 20-day after a dip.",
                filters=ScreenerFilters(
                    above_sma200=True, above_sma50=True, above_sma20=False,
                    max_rsi=50, min_return_1y=0.05,
                ),
                sort_by="rsi_14",
                sort_direction=SortDirection.ASC,
            ),
            ScreenerPreset(
                id="oversold_bounce",
                name="Oversold bounce",
                description="Deeply oversold RSI while still holding the long-term trend.",
                filters=ScreenerFilters(max_rsi=32, above_sma200=True),
                sort_by="rsi_14",
                sort_direction=SortDirection.ASC,
            ),
            ScreenerPreset(
                id="value_washout",
                name="Deep pullbacks",
                description="Near 52-week lows after a heavy decline, for mean-reversion setups.",
                filters=ScreenerFilters(near_52w_low_percent=0.10, max_return_3m=-0.15),
                sort_by="return_3m",
                sort_direction=SortDirection.ASC,
            ),
            ScreenerPreset(
                id="recovery",
                name="Turning off the lows",
                description="Well off the yearly low and back above the 50-day after a deep drawdown.",
                filters=ScreenerFilters(
                    min_distance_to_52w_low=0.20, above_sma50=True,
                    max_drawdown_floor=-0.60, max_distance_to_52w_high=-0.15,
                ),
                sort_by="return_1m",
            ),

            # -- volume and volatility ------------------------------------
            ScreenerPreset(
                id="volume_breakout",
                name="Volume breakouts",
                description="Rising on at least double the average volume with a bullish MACD.",
                filters=ScreenerFilters(
                    min_volume_ratio=2.0, min_change_percent=0.02, macd_bullish=True
                ),
                sort_by="volume_ratio",
            ),
            ScreenerPreset(
                id="quiet_accumulation",
                name="Quiet accumulation",
                description="Uptrend on subdued volume and low volatility, before the crowd arrives.",
                filters=ScreenerFilters(
                    above_sma50=True, above_sma200=True,
                    max_volume_ratio=0.9, max_volatility=0.30,
                ),
                sort_by="return_3m",
            ),
            ScreenerPreset(
                id="high_volatility",
                name="High volatility movers",
                description="Wide daily ranges and heavy turnover, for traders who want movement.",
                filters=ScreenerFilters(min_volatility=0.45, min_avg_volume=2_000_000),
                sort_by="volatility",
            ),

            # -- warnings --------------------------------------------------
            ScreenerPreset(
                id="overheated",
                name="Overheated names",
                description="Extended to the top of the range with an overbought RSI.",
                filters=ScreenerFilters(min_rsi=75, near_52w_high_percent=0.03),
                sort_by="rsi_14",
            ),
            ScreenerPreset(
                id="breaking_down",
                name="Breaking down",
                description="Confirmed downtrends losing the 200-day, worth avoiding or shorting.",
                filters=ScreenerFilters(
                    trends=[TrendDirection.DOWNTREND, TrendDirection.STRONG_DOWNTREND],
                    above_sma200=False, max_return_3m=-0.10,
                ),
                sort_by="return_3m",
                sort_direction=SortDirection.ASC,
            ),
        ]

    def clear_cache(self) -> None:
        self._snapshot_mtime_ns = None
        self._set_metrics(pd.DataFrame(columns=SCREENER_SNAPSHOT_COLUMNS))


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


def _decode_indexes(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return [str(item) for item in decoded]
        except json.JSONDecodeError:
            return [item for item in value.split(",") if item]
    if isinstance(value, (list, tuple, np.ndarray)):
        return [str(item) for item in value]
    return []


screener_service = ScreenerService()
