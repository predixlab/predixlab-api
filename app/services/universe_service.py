"""Index membership: the single definition of what this backend covers.

Every scan in the product — the screener, the market breadth and heatmap
endpoints, the movers tables — starts by asking "which symbols?". Answering it
from the price archive is wrong: the archive accumulates. It keeps names that
left the S&P 500 years ago and names that were only ever collected to backfill
a label, and a scan over that set silently reports a delisted shell as a
momentum leader.

The membership files the collector writes are the answer instead. They use
qlib's three-column ``symbol <TAB> start_date <TAB> end_date`` format, one file
per universe:

``sp500.txt`` / ``nasdaq100.txt``
    A single index. Read to answer "which index is this name in?".
``us.txt``
    Their union. The default universe.

A row is *current* when its ``end_date`` has not passed. The collector writes
the sentinel ``2099-12-31`` for a name still in the index and the real removal
date for one that left, so filtering on the date is what separates the 516
current US constituents from the 176 names the archive still carries.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from app.config import settings
from app.services.cache import InMemoryCache

logger = logging.getLogger(__name__)

#: The indices this backend tracks, in the order they are reported.
INDEXES = ("sp500", "nasdaq100")

#: Membership file columns, in qlib's order.
MEMBERSHIP_COLUMNS = ["symbol", "start_date", "end_date"]

#: The union file the collector writes alongside the per-index ones.
COMBINED = "us"


class UniverseService:
    """Reads current index membership from the collector's instruments files."""

    def __init__(self, index_path: Optional[Path] = None):
        # The union file names the directory; the per-index files sit beside it.
        self.index_path = Path(index_path or settings.us_index_path).expanduser()
        self.index_dir = self.index_path.parent
        self.cache = InMemoryCache(default_ttl_minutes=60)

    # -- membership -------------------------------------------------------

    def _read(self, universe: str) -> List[str]:
        """Symbols currently in ``universe``, sorted; empty when unreadable."""
        path = (
            self.index_path
            if universe == COMBINED
            else self.index_dir / f"{universe}.txt"
        )
        if not path.exists():
            return []

        try:
            frame = pd.read_csv(
                path, header=None, names=MEMBERSHIP_COLUMNS, dtype=str, sep="\t"
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"Failed to read membership file {path}: {exc}")
            return []

        return self._current_symbols(frame)

    @staticmethod
    def _current_symbols(frame: pd.DataFrame) -> List[str]:
        """Keep the rows whose membership has not ended.

        A missing ``end_date`` is treated as open rather than dropped: an older
        collector drop wrote a bare symbol list, and reading that as "nothing is
        current" would empty the universe instead of degrading to the union.
        """
        if frame.empty or "symbol" not in frame.columns:
            return []

        symbols = frame["symbol"].dropna().astype(str).str.strip().str.upper()
        keep = symbols != ""

        if "end_date" in frame.columns:
            ends = pd.to_datetime(frame["end_date"], errors="coerce")
            keep &= ends.isna() | (ends >= pd.Timestamp(date.today()))

        return sorted(set(symbols[keep]))

    def get_index_members(self, index: str) -> List[str]:
        """Symbols currently in ``index`` (``sp500`` / ``nasdaq100``)."""
        key = f"members:{index}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        members = self._read(index)
        self.cache.set(key, members)
        return members

    def get_universe(self) -> List[str]:
        """Every symbol currently in a tracked index, sorted.

        Prefers the union file the collector writes. Falls back to unioning the
        per-index files so a data drop missing ``us.txt`` still works, and
        returns empty when nothing is readable — there is deliberately no
        "scan the price directory" fallback, because that is exactly the
        behaviour this service exists to remove.
        """
        cached = self.cache.get("universe")
        if cached is not None:
            return cached

        symbols = self._read(COMBINED)
        if not symbols:
            symbols = sorted(
                {symbol for index in INDEXES for symbol in self.get_index_members(index)}
            )
        if not symbols:
            logger.warning(
                f"No index membership found under {self.index_dir}; the universe is empty. "
                "Index constituent data is not available."
            )

        self.cache.set("universe", symbols)
        return symbols

    def get_membership(self) -> Dict[str, List[str]]:
        """Index labels per symbol, for names that carry any."""
        cached = self.cache.get("membership")
        if cached is not None:
            return cached

        membership: Dict[str, List[str]] = {}
        for index in INDEXES:
            for symbol in self.get_index_members(index):
                membership.setdefault(symbol, []).append(index)

        self.cache.set("membership", membership)
        return membership

    def resolve(self, index: Optional[str]) -> List[str]:
        """Universe for an optional index filter; unknown names select nothing.

        Returning the full universe for an unrecognised filter would silently
        widen the scan rather than reporting that the request was wrong.
        """
        if index is None or index.lower() in ("", "all", COMBINED):
            return self.get_universe()
        return self.get_index_members(index.lower())

    def clear_cache(self) -> None:
        self.cache.clear()


universe_service = UniverseService()
