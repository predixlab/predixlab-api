"""Path metrics for a signal's forward window.

`predixlab-pivot` publishes signals and one endpoint-to-endpoint return:
`close[t+H] / close[t] - 1`. That number answers "where did it end up", and
says nothing about how it got there. A signal that ran +8% by day five and
gave it all back reads identically to one that never moved, and a signal that
survived a -12% drawdown before closing flat reads as a harmless zero.

This module reconstructs the path from the OHLC bars the backend already owns,
so pivot keeps its single responsibility (detect turning points) and the
measurement of what followed lives with the price data.

Two figures, both from the position's point of view so long and short are
directly comparable:

``max_gain``  the best the position was ever up, intraday highs included
``max_loss``  the worst it was ever down, intraday lows included

Both are measured against the signal's own close, over the *strictly future*
window `t+1 .. t+H`. The signal bar itself is the reference point and is
excluded from the outcome window.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from app.config import settings
from app.services.cache import InMemoryCache
from app.models.pivot import PickDirection, PivotPick

logger = logging.getLogger(__name__)

#: Bars are shared across every pick for a symbol, so they are worth holding.
_BARS_TTL_MINUTES = 30


@dataclass(frozen=True)
class PathOutcome:
    """The best and worst the position ever was, over its forward window."""

    max_gain: Optional[float]
    max_loss: Optional[float]
    #: Trading days from the signal to the best point. Useful on its own: a
    #: peak on day two that decays is a different signal from one on day ten.
    days_to_max_gain: Optional[int]
    days_to_max_loss: Optional[int]
    #: False when the window has not fully elapsed, so a partial path is not
    #: mistaken for a complete one.
    complete: bool


class PathMetricsService:
    """Computes forward-path metrics for published picks.

    Read-only over the same OHLC files `StockService` serves. Bars are cached
    per symbol because a symbol's history is scanned once per pick otherwise.
    """

    def __init__(self) -> None:
        self.stock_data_dir = Path(settings.us_stock_data_dir).expanduser()
        self.cache = InMemoryCache(default_ttl_minutes=_BARS_TTL_MINUTES)

    # ------------------------------------------------------------------
    def _load_bars(self, symbol: str) -> Optional[pd.DataFrame]:
        """OHLC bars for a symbol, indexed by date and sorted ascending.

        Returns ``None`` when the symbol has no price file, which is a real
        case: the pick list and the price archive are populated by different
        jobs and can briefly disagree.
        """
        key = f"path_bars:{symbol.upper()}"
        cached = self.cache.get(key)
        if cached is not None:
            # A cached miss is stored as an empty frame; `None` means unseen.
            return None if cached.empty else cached

        path = self.stock_data_dir / f"{symbol.upper()}.csv"
        if not path.exists():
            self.cache.set(key, pd.DataFrame())
            return None

        try:
            frame = pd.read_csv(path, usecols=["date", "high", "low", "close"])
        except Exception as exc:
            logger.error(f"Failed to read bars for {symbol}: {exc}")
            self.cache.set(key, pd.DataFrame())
            return None

        if frame.empty:
            self.cache.set(key, pd.DataFrame())
            return None

        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date", kind="stable")
        frame = frame.reset_index(drop=True)
        self.cache.set(key, frame)
        return frame

    # ------------------------------------------------------------------
    @staticmethod
    def _outcome(
        frame: pd.DataFrame,
        signal_date: pd.Timestamp,
        horizon: int,
        direction: str,
    ) -> Optional[PathOutcome]:
        """Walk the forward window and return the extremes it reached.

        The anchor is the signal bar's close, so the figures are what a reader
        holding from that close would have seen. A short's gain is the fall, so
        its highs and lows swap roles.
        """
        dates = frame["date"]
        # `searchsorted` gives the first bar on or after the signal date, which
        # is the signal bar itself when the symbol traded that session.
        position = int(dates.searchsorted(signal_date, side="left"))
        if position >= len(frame) or dates.iloc[position] != signal_date:
            return None

        anchor = float(frame["close"].iloc[position])
        if not anchor or anchor <= 0:
            return None

        window = frame.iloc[position + 1 : position + 1 + horizon]
        if window.empty:
            return None

        highs = window["high"].to_numpy(dtype="float64")
        lows = window["low"].to_numpy(dtype="float64")

        best_move = highs / anchor - 1.0
        worst_move = lows / anchor - 1.0

        if direction == PickDirection.SHORT.value:
            # A short profits from a fall: its best point is the lowest low.
            gains = -worst_move
            losses = -best_move
            gain_index = int(lows.argmin())
            loss_index = int(highs.argmax())
        else:
            gains = best_move
            losses = worst_move
            gain_index = int(highs.argmax())
            loss_index = int(lows.argmin())

        return PathOutcome(
            max_gain=float(gains[gain_index]),
            max_loss=float(losses[loss_index]),
            # +1 because the window starts on the session after the signal.
            days_to_max_gain=gain_index + 1,
            days_to_max_loss=loss_index + 1,
            complete=len(window) >= horizon,
        )

    # ------------------------------------------------------------------
    def outcome_for(
        self, symbol: str, date: str, horizon: int, direction: str
    ) -> Optional[PathOutcome]:
        """Path metrics for a single signal, or ``None`` when unavailable."""
        frame = self._load_bars(symbol)
        if frame is None:
            return None
        try:
            signal_date = pd.Timestamp(datetime.strptime(date[:10], "%Y-%m-%d"))
        except (TypeError, ValueError):
            return None
        return self._outcome(frame, signal_date, horizon, direction)

    # ------------------------------------------------------------------
    def attach(self, picks: Sequence[PivotPick]) -> List[PivotPick]:
        """Fill `max_gain` / `max_loss` on every pick that has bars for them.

        Picks are grouped by symbol so each price file is read once. Picks the
        archive cannot cover keep their `None`, which the API already models as
        "not available" rather than zero.
        """
        if not picks:
            return list(picks)

        by_symbol: Dict[str, List[PivotPick]] = {}
        for pick in picks:
            by_symbol.setdefault(pick.symbol.upper(), []).append(pick)

        for symbol, group in by_symbol.items():
            frame = self._load_bars(symbol)
            if frame is None:
                continue
            for pick in group:
                try:
                    signal_date = pd.Timestamp(
                        datetime.strptime(str(pick.date)[:10], "%Y-%m-%d")
                    )
                except (TypeError, ValueError):
                    continue
                outcome = self._outcome(frame, signal_date, pick.horizon, pick.direction.value)
                if outcome is None:
                    continue
                pick.max_gain = outcome.max_gain
                pick.max_loss = outcome.max_loss
                pick.days_to_max_gain = outcome.days_to_max_gain
                pick.days_to_max_loss = outcome.days_to_max_loss
                pick.path_complete = outcome.complete

        return list(picks)

    def clear_cache(self) -> None:
        self.cache.clear()


path_metrics_service = PathMetricsService()
