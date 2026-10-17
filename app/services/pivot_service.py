"""Data access for the pivot pick list.

Reads only. The `pivot_picks` collection is written exclusively by the
`predixlab-pivot` Container Apps job, which runs nightly with
`predixlab-collector` as its init container.

The free/premium split follows the same rule as the rest of the product: free
users see everything older than `settings.free_user_days_limit` days and nothing
newer, because the recent sessions are the ones with option value.
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from pymongo.asynchronous.database import AsyncDatabase

from app.config import settings
from app.database import get_database
from app.models.pivot import (
    PickDirection,
    PickEvent,
    PivotPerformance,
    PivotPerformanceBreakdown,
    PivotPick,
    PivotRun,
    PivotRunModelInfo,
    PivotSummary,
    pick_from_document,
)
from app.services.path_metrics_service import path_metrics_service

logger = logging.getLogger(__name__)

#: How many names per side the summary carries.
SUMMARY_PICKS = 5

#: Cap on a history query, so a malformed request cannot pull the whole table.
MAX_HISTORY = 750

#: Probability that an arbitrary bar sits within the tolerance window of a real
#: zigzag swing point, measured on the walk-forward panel. This is the level a
#: coin-flip signal would reach, and the only thing that makes a detection rate
#: interpretable.
PIVOT_BASE_RATE = 0.183

#: Events that represent actionable picks. Detection-only markers published by
#: the model job must never cross the API boundary.
PUBLIC_PICK_EVENTS = tuple(event.value for event in PickEvent)


class PivotService:
    def __init__(self):
        self.db: Optional[AsyncDatabase] = None

    async def initialize(self):
        """Initialize database connection"""
        if self.db is None:
            self.db = await get_database()

    # ------------------------------------------------------------------
    def _cutoff(self) -> str:
        """Newest date a free user may see, as a `YYYY-MM-DD` string."""
        cutoff = datetime.now() - timedelta(days=settings.free_user_days_limit)
        return cutoff.strftime("%Y-%m-%d")

    def _base_filter(self, market: str, is_premium: bool) -> Dict[str, Any]:
        query: Dict[str, Any] = {
            "market": market,
            "event": {"$in": PUBLIC_PICK_EVENTS},
        }
        if not is_premium:
            query["date"] = {"$lte": self._cutoff()}
        return query

    def _public_filter(self, market: str) -> Dict[str, Any]:
        return {"market": market, "event": {"$in": PUBLIC_PICK_EVENTS}}

    # ------------------------------------------------------------------
    async def get_available_dates(
        self,
        market: str = "us",
        limit: Optional[int] = 60,
        is_premium: bool = False,
    ) -> List[str]:
        """Sessions that carry picks, newest first."""
        try:
            pipeline: List[Dict[str, Any]] = [
                {"$match": self._base_filter(market, is_premium)},
                {"$group": {"_id": "$date"}},
                {"$sort": {"_id": -1}},
            ]
            if limit is not None and limit > 0:
                pipeline.append({"$limit": limit})

            # `AsyncCollection.aggregate` is a coroutine function: it must be
            # awaited to obtain the cursor, unlike `find`, which returns one
            # directly. Iterating the un-awaited coroutine raises TypeError.
            cursor = await self.db.pivot_picks.aggregate(pipeline)
            dates = [doc["_id"] async for doc in cursor if doc.get("_id")]
            logger.debug(f"Found {len(dates)} pivot pick dates for market {market}")
            return dates
        except Exception as e:
            logger.error(f"Failed to get pivot dates for market {market}: {e}")
            return []

    async def get_latest_date(self, market: str = "us", is_premium: bool = False) -> Optional[str]:
        """Newest session the caller is allowed to see."""
        try:
            doc = await self.db.pivot_picks.find_one(
                self._base_filter(market, is_premium),
                sort=[("date", -1)],
            )
            return doc.get("date") if doc else None
        except Exception as e:
            logger.error(f"Failed to get latest pivot date for market {market}: {e}")
            return None

    async def get_picks_by_date(
        self,
        date: str,
        market: str = "us",
        direction: Optional[str] = None,
        event: Optional[str] = None,
        limit: int = 100,
        strict: bool = False,
    ) -> List[PivotPick]:
        """One session's events, ordered by rank within each side.

        Returning an empty list is a legitimate outcome, not an error: events are
        sparse and most sessions fire few or none.
        """
        try:
            query: Dict[str, Any] = {**self._public_filter(market), "date": date}
            if direction:
                query["direction"] = direction
            if event:
                if event not in PUBLIC_PICK_EVENTS:
                    return []
                query["event"] = event
            cursor = self.db.pivot_picks.find(query)
            items = [pick_from_document(doc) async for doc in cursor]
            items.sort(key=lambda pick: (pick.direction.value, pick.rank))

            side_limit = max(limit, 1)
            if direction:
                items = items[:side_limit]
            else:
                side_counts: Dict[PickDirection, int] = {}
                limited_items = []
                for pick in items:
                    count = side_counts.get(pick.direction, 0)
                    if count < side_limit:
                        limited_items.append(pick)
                        side_counts[pick.direction] = count + 1
                items = limited_items

            logger.debug(f"Retrieved {len(items)} pivot events for {market} on {date}")
            return items
        except Exception as e:
            logger.error(f"Failed to get pivot picks for {market} on {date}: {e}")
            if strict:
                raise
            return []

    async def count_events(
        self, date: str, market: str = "us", strict: bool = False
    ) -> Dict[str, int]:
        """Events by type for one session."""
        try:
            pipeline = [
                {"$match": {**self._public_filter(market), "date": date}},
                {"$group": {"_id": "$event", "count": {"$sum": 1}}},
            ]
            counts: Dict[str, int] = {}
            async for doc in await self.db.pivot_picks.aggregate(pipeline):
                if doc.get("_id"):
                    counts[str(doc["_id"])] = int(doc.get("count", 0))
            return counts
        except Exception as e:
            logger.error(f"Failed to count pivot events for {market} on {date}: {e}")
            if strict:
                raise
            return {}

    async def get_pick_for_symbol(
        self,
        date: str,
        symbol: str,
        market: str = "us",
    ) -> Optional[PivotPick]:
        try:
            doc = await self.db.pivot_picks.find_one(
                {
                    **self._public_filter(market),
                    "date": date,
                    "symbol": symbol.upper(),
                }
            )
            return pick_from_document(doc) if doc else None
        except Exception as e:
            logger.error(f"Failed to get pivot pick for {symbol} on {date}: {e}")
            return None

    async def get_symbol_history(
        self,
        symbol: str,
        market: str = "us",
        limit: int = 120,
        is_premium: bool = False,
    ) -> List[PivotPick]:
        """Every session this symbol was picked, oldest first so it plots directly.

        Path metrics are attached here rather than stored: the pivot job owns
        signal detection, and what the price did afterwards is derivable from
        the OHLC archive this service already has.
        """
        try:
            query = self._base_filter(market, is_premium)
            query["symbol"] = symbol.upper()
            cursor = (
                self.db.pivot_picks.find(query)
                .sort("date", -1)
                .limit(min(max(limit, 1), MAX_HISTORY))
            )
            items = [pick_from_document(doc) async for doc in cursor]
            items.reverse()
            return path_metrics_service.attach(items)
        except Exception as e:
            logger.error(f"Failed to get pivot history for {symbol}: {e}")
            return []

    async def get_summary(self, market: str = "us", is_premium: bool = False) -> PivotSummary:
        """Headline numbers plus the strongest names on each side."""
        summary = PivotSummary(market=market)
        try:
            date = await self.get_latest_date(market, is_premium)
            if not date:
                return summary
            summary.date = date

            pipeline = [
                {"$match": {**self._public_filter(market), "date": date}},
                {"$group": {"_id": "$direction", "count": {"$sum": 1}}},
            ]
            async for doc in await self.db.pivot_picks.aggregate(pipeline):
                if doc.get("_id") == PickDirection.LONG.value:
                    summary.long_count = int(doc.get("count", 0))
                elif doc.get("_id") == PickDirection.SHORT.value:
                    summary.short_count = int(doc.get("count", 0))
            summary.counts = await self.count_events(date, market)

            summary.top_longs = await self.get_picks_by_date(
                date, market=market, direction=PickDirection.LONG.value, limit=SUMMARY_PICKS
            )
            summary.top_shorts = await self.get_picks_by_date(
                date, market=market, direction=PickDirection.SHORT.value, limit=SUMMARY_PICKS
            )

            first = await self.db.pivot_picks.find_one(
                {**self._public_filter(market), "date": date}
            )
            if first:
                summary.horizon = first.get("horizon")
                summary.generated_at = first.get("generated_at")
            return summary
        except Exception as e:
            logger.error(f"Failed to build pivot summary for {market}: {e}")
            return summary

    async def get_performance(
        self,
        market: str = "us",
        direction: Optional[str] = None,
        event: Optional[str] = None,
    ) -> PivotPerformance:
        """Forward-horizon outcome of the published signals.

        Deliberately not a strategy backtest. It answers only "after this signal
        fired, what did the name do over the next `horizon` sessions", raw and
        benchmark-relative.
        """
        performance = PivotPerformance(market=market, direction=direction, event=event)
        try:
            scope = self._public_filter(market)
            if direction:
                scope["direction"] = direction
            if event:
                if event not in PUBLIC_PICK_EVENTS:
                    return performance
                scope["event"] = event

            performance.pending = await self.db.pivot_picks.count_documents(
                {**scope, "signal_return": None}
            )

            match = {**scope, "signal_return": {"$ne": None}}
            pipeline = [
                {"$match": match},
                {
                    "$group": {
                        "_id": None,
                        "n": {"$sum": 1},
                        "wins": {"$sum": {"$cond": [{"$gt": ["$signal_return", 0]}, 1, 0]}},
                        "excess_wins": {
                            "$sum": {"$cond": [{"$gt": ["$excess_return", 0]}, 1, 0]}
                        },
                        "avg": {"$avg": "$signal_return"},
                        "avg_excess": {"$avg": "$excess_return"},
                        "best": {"$max": "$signal_return"},
                        "worst": {"$min": "$signal_return"},
                        "start": {"$min": "$date"},
                        "end": {"$max": "$date"},
                        "horizon": {"$first": "$horizon"},
                        "returns": {"$push": "$signal_return"},
                        "excess": {"$push": "$excess_return"},
                    }
                },
            ]
            rows = [doc async for doc in await self.db.pivot_picks.aggregate(pipeline)]
            if not rows or not rows[0].get("n"):
                return performance

            row = rows[0]
            n = int(row["n"])
            performance.evaluated = n
            performance.horizon = row.get("horizon")
            performance.win_rate = round(row["wins"] / n, 4)
            performance.excess_win_rate = round(row["excess_wins"] / n, 4)
            performance.avg_return = _round(row.get("avg"))
            performance.avg_excess_return = _round(row.get("avg_excess"))
            performance.best_return = row.get("best")
            performance.worst_return = row.get("worst")
            performance.median_return = _median(row.get("returns"))
            performance.median_excess_return = _median(row.get("excess"))
            performance.start = row.get("start")
            performance.end = row.get("end")

            await self._attach_detection(performance, scope, market)
            return performance
        except Exception as e:
            logger.error(f"Failed to compute pivot performance for {market}: {e}")
            return performance

    async def _attach_detection(
        self, performance: PivotPerformance, scope: Dict[str, Any], market: str
    ) -> None:
        """Fill in turning-point detection accuracy for reversal signals.

        The base rate is measured the same way the hit is: the share of *all*
        published reversals' universe-days that sit near a pivot would require
        the full panel, which the API does not hold. Instead the chance level is
        the share of scored bars that a random pick would have hit, supplied by
        the job as a constant — see the module docstring in
        ``predixlab_pivot.jobs.backfill``.
        """
        try:
            scored = await self.db.pivot_picks.count_documents(
                {**scope, "pivot_hit": {"$ne": None}}
            )
            if not scored:
                return
            hits = await self.db.pivot_picks.count_documents({**scope, "pivot_hit": True})
            performance.detection_scored = scored
            performance.detection_rate = round(hits / scored, 4)
            performance.detection_base_rate = PIVOT_BASE_RATE
            performance.detection_lift = round(
                (hits / scored) / PIVOT_BASE_RATE, 2
            ) if PIVOT_BASE_RATE else None
        except Exception as e:
            logger.error(f"Failed to compute detection stats for {market}: {e}")

    async def get_performance_breakdown(
        self, market: str = "us"
    ) -> PivotPerformanceBreakdown:
        """Performance per event type.

        No combined figure: longs and shorts carry opposite raw returns by
        construction, so an average over both would describe the period's mix
        rather than the system.
        """
        by_event = []
        horizon = None
        for event in PickEvent:
            item = await self.get_performance(market=market, event=event.value)
            if item.evaluated or item.pending:
                by_event.append(item)
                horizon = horizon or item.horizon
        return PivotPerformanceBreakdown(market=market, horizon=horizon, by_event=by_event)

    async def get_latest_run(self, market: str = "us") -> Optional[PivotRun]:
        """Most recent run record, including failed and skipped executions.

        Deliberately not gated on the free-user cutoff: knowing *whether* the
        pipeline ran is operational information, not premium data.
        """
        try:
            doc = await self.db.pivot_runs.find_one({"market": market}, sort=[("date", -1)])
            if not doc:
                return None
            model = doc.get("model")
            allowed = set(PivotRunModelInfo.model_fields.keys())
            return PivotRun(
                market=doc.get("market", market),
                date=doc.get("date"),
                horizon=doc.get("horizon"),
                tag=doc.get("tag"),
                status=doc.get("status", "unknown"),
                started_at=doc.get("started_at"),
                finished_at=doc.get("finished_at"),
                duration_seconds=doc.get("duration_seconds"),
                symbols_scored=doc.get("symbols_scored", 0) or 0,
                published=doc.get("published", 0) or 0,
                counts=doc.get("counts") or {},
                model=(
                    PivotRunModelInfo(**{k: v for k, v in model.items() if k in allowed})
                    if isinstance(model, dict)
                    else None
                ),
                error=doc.get("error"),
                publish_error=doc.get("publish_error"),
            )
        except Exception as e:
            logger.error(f"Failed to get latest pivot run for {market}: {e}")
            return None


def _round(value: Optional[float], digits: int = 6) -> Optional[float]:
    return round(value, digits) if value is not None else None


def _median(values: Optional[List[Optional[float]]]) -> Optional[float]:
    """Median over the non-null entries, or ``None`` when there are none."""
    if not values:
        return None
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    mid = len(ordered) // 2
    value = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return round(value, 6)


# Create global pivot service instance
pivot_service = PivotService()
