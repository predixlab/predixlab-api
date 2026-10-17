"""Async Mongo access for daily AlphaRank signals."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pymongo.asynchronous.database import AsyncDatabase

from app.config import settings
from app.database import get_database
from app.models.alpharank import AlphaRankSignal, alpha_rank_from_document
from app.models.pivot import PickDirection
from app.services.path_metrics_service import path_metrics_service

logger = logging.getLogger(__name__)

MAX_GENERATION_READ_ATTEMPTS = 3
MAX_HISTORY = 750
MAX_HISTORY_SNAPSHOT_ATTEMPTS = 3


class AlphaRankService:
    """Read committed AlphaRank publications from the daily model pipeline."""

    def __init__(self) -> None:
        self.db: Optional[AsyncDatabase] = None

    async def initialize(self) -> None:
        if self.db is None:
            self.db = await get_database()

    def _signals_collection(self):
        return self.db[settings.alpharank_collection]

    def _generations_collection(self):
        return self.db[settings.alpharank_generations_collection]

    def _cutoff(self) -> str:
        return (datetime.now() - timedelta(days=settings.free_user_days_limit)).strftime(
            "%Y-%m-%d"
        )

    def _base_filter(self, market: str, is_premium: bool) -> Dict[str, Any]:
        query: Dict[str, Any] = {
            "market": market,
            "signal_model": "alpha_rank",
        }
        if not is_premium:
            query["date"] = {"$lte": self._cutoff()}
        return query

    def _manifest_filter(self, market: str, is_premium: bool) -> Dict[str, Any]:
        query = self._base_filter(market, is_premium)
        query["active_generation_id"] = {"$exists": True, "$ne": None}
        query["row_count"] = {"$gte": 0}
        return query

    def _date_filter(
        self, date: str, market: str, is_premium: bool
    ) -> Dict[str, Any]:
        query = self._manifest_filter(market, is_premium)
        query["date"] = (
            date
            if is_premium
            else {"$eq": date, "$lte": self._cutoff()}
        )
        return query

    async def get_available_dates(
        self,
        market: str = "us",
        limit: Optional[int] = 60,
        is_premium: bool = False,
    ) -> List[str]:
        """Return visible signal dates newest first."""

        try:
            pipeline: List[Dict[str, Any]] = [
                {"$match": self._manifest_filter(market, is_premium)},
                {"$group": {"_id": "$date"}},
                {"$sort": {"_id": -1}},
            ]
            if limit is not None and limit > 0:
                pipeline.append({"$limit": limit})
            cursor = await self._generations_collection().aggregate(pipeline)
            return [document["_id"] async for document in cursor if document.get("_id")]
        except Exception as exc:
            logger.error("Failed to get AlphaRank dates for %s: %s", market, exc)
            raise

    async def get_latest_date(
        self, market: str = "us", is_premium: bool = False
    ) -> Optional[str]:
        """Return the newest date visible to this caller."""

        try:
            document = await self._generations_collection().find_one(
                self._manifest_filter(market, is_premium),
                sort=[("date", -1)],
            )
            return document.get("date") if document else None
        except Exception as exc:
            logger.error("Failed to get latest AlphaRank date for %s: %s", market, exc)
            raise

    async def get_signals_by_date(
        self,
        date: str,
        market: str = "us",
        is_premium: bool = False,
    ) -> List[AlphaRankSignal]:
        """Return a complete, stable manifest-committed generation."""

        try:
            manifest_query = self._date_filter(date, market, is_premium)
            generations = self._generations_collection()
            saw_manifest = False
            for _ in range(MAX_GENERATION_READ_ATTEMPTS):
                manifest = await generations.find_one(manifest_query)
                if not manifest:
                    if saw_manifest:
                        continue
                    return []
                saw_manifest = True

                generation_id = manifest.get("active_generation_id")
                row_count = manifest.get("row_count")
                if not isinstance(row_count, int) or row_count < 0:
                    raise RuntimeError(
                        f"Invalid AlphaRank manifest row_count for {market} on {date}"
                    )

                cursor = self._signals_collection().find(
                    {
                        "market": market,
                        "date": date,
                        "signal_model": "alpha_rank",
                        "generation_id": generation_id,
                    }
                )
                signals: List[AlphaRankSignal] = []
                fetched_count = 0
                async for document in cursor:
                    fetched_count += 1
                    try:
                        signals.append(alpha_rank_from_document(document))
                    except (TypeError, ValueError) as exc:
                        logger.warning(
                            "Skipping malformed AlphaRank document for %s on %s: %s",
                            market,
                            date,
                            exc,
                        )

                committed = await generations.find_one(manifest_query)
                if (
                    committed
                    and committed.get("active_generation_id") == generation_id
                    and committed.get("row_count") == row_count
                    and fetched_count == row_count
                ):
                    return sorted(
                        signals, key=lambda signal: (signal.rank, signal.symbol)
                    )

            raise RuntimeError(
                f"Could not read a complete stable AlphaRank generation for {market} on {date}"
            )
        except Exception as exc:
            logger.error(
                "Failed to get AlphaRank signals for %s on %s: %s", market, date, exc
            )
            raise

    async def get_ticker_history(
        self,
        ticker: str,
        market: str = "us",
        limit: int = 120,
        is_premium: bool = False,
    ) -> List[AlphaRankSignal]:
        """Committed publications for one bullish AlphaRank ticker, oldest first."""

        try:
            safe_limit = min(max(limit, 1), MAX_HISTORY)
            symbol = ticker.upper()
            manifest_query = self._manifest_filter(market, is_premium)
            generations = self._generations_collection()

            for _ in range(MAX_HISTORY_SNAPSHOT_ATTEMPTS):
                manifests = [
                    manifest async for manifest in generations.find(manifest_query)
                ]
                active_generations = {
                    str(manifest["date"]): manifest["active_generation_id"]
                    for manifest in manifests
                    if (
                        manifest.get("date")
                        and manifest.get("active_generation_id") is not None
                    )
                }
                if not active_generations:
                    return []

                exact_generations = [
                    {"date": date, "generation_id": generation_id}
                    for date, generation_id in active_generations.items()
                ]
                signal_query = {
                    **self._base_filter(market, is_premium),
                    "symbol": symbol,
                    "$or": exact_generations,
                }
                raw_documents = [
                    document
                    async for document in self._signals_collection().find(signal_query)
                ]

                committed_manifests = [
                    manifest async for manifest in generations.find(manifest_query)
                ]
                committed_generations = {
                    str(manifest["date"]): manifest["active_generation_id"]
                    for manifest in committed_manifests
                    if (
                        manifest.get("date")
                        and manifest.get("active_generation_id") is not None
                    )
                }
                if committed_generations != active_generations:
                    continue

                items: List[AlphaRankSignal] = []
                for document in raw_documents:
                    date = str(document.get("date", ""))
                    if document.get("generation_id") != active_generations.get(date):
                        continue
                    try:
                        items.append(alpha_rank_from_document(document))
                    except (TypeError, ValueError) as exc:
                        logger.warning(
                            "Skipping malformed AlphaRank history document for %s on %s: %s",
                            symbol,
                            date,
                            exc,
                        )

                items.sort(
                    key=lambda signal: (signal.date, signal.rank, signal.symbol),
                    reverse=True,
                )
                items = items[:safe_limit]
                items.reverse()
                self._attach_path_metrics(items)
                return items

            raise RuntimeError(
                f"Could not read a stable AlphaRank history snapshot for {symbol}"
            )
        except Exception as exc:
            logger.error(
                "Failed to get AlphaRank history for %s in %s: %s",
                ticker,
                market,
                exc,
            )
            raise

    @staticmethod
    def _attach_path_metrics(items: List[AlphaRankSignal]) -> None:
        """Attach long-side forward outcomes without inventing missing data."""

        for signal in items:
            outcome = path_metrics_service.outcome_for(
                signal.symbol,
                signal.date,
                signal.horizon,
                PickDirection.LONG.value,
            )
            if outcome is None:
                continue
            signal.max_gain = outcome.max_gain
            signal.max_loss = outcome.max_loss
            signal.days_to_max_gain = outcome.days_to_max_gain
            signal.days_to_max_loss = outcome.days_to_max_loss
            signal.path_complete = outcome.complete


alpharank_service = AlphaRankService()
