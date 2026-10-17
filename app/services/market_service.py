"""Read cached market intelligence from collector-produced snapshots."""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from pymongo.errors import PyMongoError

from app.config import settings
from app.database import mongodb
from app.models.market import (
    HeatmapResponse,
    MarketBreadth,
    MarketOverviewResponse,
    MarketSentiment,
    MoverCategory,
    MoversResponse,
    PerformanceDistributionResponse,
)
from app.services.snapshot_storage import market_snapshot_dir

logger = logging.getLogger(__name__)

MARKET_RESULT_LIMIT = 10
MARKET_SNAPSHOT_VERSION = 3
SUPPORTED_MARKET_SNAPSHOT_VERSIONS = {2, MARKET_SNAPSHOT_VERSION}


def _empty_sentiment() -> MarketSentiment:
    return MarketSentiment(
        score=50.0,
        label="neutral",
        components={},
        summary="The market snapshot has no data.",
    )


class MarketService:
    """Serves the collector's precomputed market snapshot."""

    def __init__(
        self,
        snapshot_root: Optional[Path] = None,
        market: str = "us",
        database: Optional[Any] = None,
    ):
        self.market = market.lower()
        self._database = database
        self._file_mode = snapshot_root is not None
        self.snapshot_path = (
            market_snapshot_dir(self.market, snapshot_root) / "market_summary.json"
        )
        self._snapshot_mtime_ns: Optional[int] = None
        self._database_revision: Optional[tuple[Any, Any]] = None
        self._reset_snapshot()

    def _reset_snapshot(self) -> None:
        self._snapshot_date: Optional[str] = None
        self._snapshot_generated_at: Optional[datetime] = None
        self._breadth = MarketBreadth()
        self._sentiment = _empty_sentiment()
        self._movers: Dict[MoverCategory, MoversResponse] = {}
        self._distributions: Dict[int, PerformanceDistributionResponse] = {}
        self._heatmaps: Dict[int, HeatmapResponse] = {}

    async def initialize(self) -> None:
        """Load a supported snapshot, otherwise serve empty responses."""
        self._reset_snapshot()
        self._snapshot_mtime_ns = None
        self._database_revision = None
        await self._refresh(force=True)

    async def refresh(self) -> bool:
        """Refresh the configured market source if a newer document exists."""
        return await self._refresh()

    async def _refresh(self, force: bool = False) -> bool:
        if self._file_mode:
            return self._reload_if_changed(force=force)
        return await self._reload_database_if_changed(force=force)

    async def _reload_database_if_changed(self, force: bool = False) -> bool:
        """Load the latest compact Market Overview document from MongoDB."""
        database = self._database or mongodb.database
        if database is None:
            raise RuntimeError("MongoDB is not connected")
        try:
            document = await database[
                settings.market_overviews_collection
            ].find_one(
                {"market": self.market},
                sort=[("date", -1)],
            )
        except PyMongoError:
            logger.exception("Could not query the Market Overview collection")
            raise
        if document is None:
            if force:
                logger.warning(
                    "No Market Overview document exists for %s in %s",
                    self.market,
                    settings.market_overviews_collection,
                )
            return False

        revision = (document.get("date"), document.get("generated_at"))
        if not force and revision == self._database_revision:
            return False
        try:
            if document.get("version") not in SUPPORTED_MARKET_SNAPSHOT_VERSIONS:
                raise ValueError(
                    f"unsupported snapshot version {document.get('version')!r}"
                )
            self._load_snapshot(document)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Could not load Market Overview document; keeping current responses: %s",
                exc,
            )
            return False

        self._database_revision = revision
        logger.info(
            "Loaded Market Overview document for %s (%d symbols)",
            self._snapshot_date,
            self._breadth.total_symbols,
        )
        return True

    def _reload_if_changed(self, force: bool = False) -> bool:
        """Reload an atomically replaced collector snapshot when it changes."""
        try:
            modified_at = self.snapshot_path.stat().st_mtime_ns
        except FileNotFoundError:
            self._snapshot_mtime_ns = None
            if force:
                logger.warning(
                    "Market snapshot is unavailable at %s; serving empty responses",
                    self.snapshot_path,
                )
            return False
        except OSError as exc:
            logger.warning("Could not stat market snapshot %s: %s", self.snapshot_path, exc)
            return False

        if not force and modified_at == self._snapshot_mtime_ns:
            return False

        try:
            document = self._read_snapshot()
            if document.get("version") not in SUPPORTED_MARKET_SNAPSHOT_VERSIONS:
                raise ValueError(
                    f"unsupported snapshot version {document.get('version')!r}"
                )
            self._load_snapshot(document)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self._snapshot_mtime_ns = modified_at
            logger.warning(
                "Could not load market snapshot %s; keeping current responses: %s",
                self.snapshot_path,
                exc,
            )
            return False

        self._snapshot_mtime_ns = modified_at
        logger.info(
            "Loaded market snapshot for %s (%d symbols)",
            self._snapshot_date,
            self._breadth.total_symbols,
        )
        return True

    def _read_snapshot(self) -> Dict[str, Any]:
        with self.snapshot_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        if not isinstance(document, dict):
            raise ValueError("market snapshot must be a JSON object")
        return document

    def _set_snapshot(
        self,
        date: Optional[str],
        generated_at: datetime,
        breadth: MarketBreadth,
        sentiment: MarketSentiment,
        movers: Dict[MoverCategory, MoversResponse],
        distributions: Dict[int, PerformanceDistributionResponse],
        heatmaps: Dict[int, HeatmapResponse],
    ) -> None:
        self._snapshot_date = date
        self._snapshot_generated_at = generated_at
        self._breadth = breadth
        self._sentiment = sentiment
        self._movers = movers
        self._distributions = distributions
        self._heatmaps = heatmaps

    def _load_snapshot(self, document: Dict[str, Any]) -> None:
        required_sections = (
            "generated_at",
            "breadth",
            "sentiment",
            "movers",
            "distributions",
        )
        missing = [section for section in required_sections if section not in document]
        if missing:
            raise ValueError(f"market snapshot missing sections: {missing}")

        generated_at = document["generated_at"]
        if isinstance(generated_at, str):
            generated_at = datetime.fromisoformat(
                generated_at.replace("Z", "+00:00")
            )
        if not isinstance(generated_at, datetime):
            raise ValueError("market snapshot generated_at must be an ISO timestamp")

        movers = document["movers"]
        distributions = document["distributions"]
        heatmaps = document.get("heatmaps", {})
        if not all(
            isinstance(section, dict)
            for section in (movers, distributions, heatmaps)
        ):
            raise ValueError("market snapshot result sections must be objects")

        sentiment = document["sentiment"]
        if document.get("version") == 2:
            sentiment = {**sentiment, "components": {}}

        self._set_snapshot(
            date=document.get("date"),
            generated_at=generated_at,
            breadth=MarketBreadth.model_validate(document["breadth"]),
            sentiment=MarketSentiment.model_validate(sentiment),
            movers={
                MoverCategory(category): MoversResponse.model_validate(response)
                for category, response in movers.items()
            },
            distributions={
                int(window_days): PerformanceDistributionResponse.model_validate(
                    response
                )
                for window_days, response in distributions.items()
            },
            heatmaps={
                int(window_days): HeatmapResponse.model_validate(response)
                for window_days, response in heatmaps.items()
            },
        )

    def _snapshot_document(self) -> Dict[str, Any]:
        return {
            "version": MARKET_SNAPSHOT_VERSION,
            "date": self._snapshot_date,
            "generated_at": self._snapshot_generated_at,
            "breadth": self._breadth.model_dump(mode="json"),
            "sentiment": self._sentiment.model_dump(mode="json"),
            "movers": {
                category.value: response.model_dump(mode="json")
                for category, response in self._movers.items()
            },
            "distributions": {
                str(window_days): response.model_dump(mode="json")
                for window_days, response in self._distributions.items()
            },
            "heatmaps": {
                str(window_days): response.model_dump(mode="json")
                for window_days, response in self._heatmaps.items()
            },
        }

    def get_snapshot(self) -> Dict[str, Any]:
        """Return the complete cached summary for client-side filtering."""
        document = copy.deepcopy(self._snapshot_document())
        generated_at = document.get("generated_at")
        if isinstance(generated_at, datetime):
            document["generated_at"] = generated_at.isoformat()
        return document

    async def get_breadth(self) -> MarketBreadth:
        await self._refresh()
        return self._breadth.model_copy(deep=True)

    async def get_movers(
        self,
        category: MoverCategory,
        limit: int = MARKET_RESULT_LIMIT,
    ) -> MoversResponse:
        await self._refresh()
        cached = self._movers.get(category)
        if cached is None:
            return MoversResponse(category=category, entries=[], total_count=0)
        result_limit = min(max(limit, 1), MARKET_RESULT_LIMIT)
        entries = [entry.model_copy(deep=True) for entry in cached.entries[:result_limit]]
        return MoversResponse(
            category=category,
            date=cached.date,
            entries=entries,
            total_count=len(entries),
        )

    async def get_sentiment(self) -> MarketSentiment:
        await self._refresh()
        return self._sentiment.model_copy(deep=True)

    async def get_overview(self, movers_limit: int = 5) -> MarketOverviewResponse:
        await self._refresh()
        breadth = await self.get_breadth()
        sentiment = await self.get_sentiment()
        gainers = await self.get_movers(MoverCategory.GAINERS, movers_limit)
        losers = await self.get_movers(MoverCategory.LOSERS, movers_limit)
        active = await self.get_movers(MoverCategory.MOST_ACTIVE, movers_limit)
        return MarketOverviewResponse(
            date=breadth.date,
            breadth=breadth,
            sentiment=sentiment,
            top_gainers=gainers.entries,
            top_losers=losers.entries,
            most_active=active.entries,
            generated_at=self._snapshot_generated_at or datetime.now(timezone.utc),
        )

    async def get_performance_distribution(
        self, window_days: int = 1
    ) -> PerformanceDistributionResponse:
        await self._refresh()
        cached = self._distributions.get(window_days)
        if cached is None:
            return PerformanceDistributionResponse(
                date=self._snapshot_date,
                window_days=window_days,
                buckets=[],
                total_symbols=0,
            )
        return cached.model_copy(deep=True)

    async def get_heatmap(
        self,
        window_days: int = 1,
        limit: int = MARKET_RESULT_LIMIT,
    ) -> HeatmapResponse:
        await self._refresh()
        cached = self._heatmaps.get(window_days)
        if cached is None:
            return HeatmapResponse(
                date=self._snapshot_date,
                window_days=window_days,
                entries=[],
            )
        result_limit = min(max(limit, 1), MARKET_RESULT_LIMIT)
        return HeatmapResponse(
            date=cached.date,
            window_days=window_days,
            entries=[
                entry.model_copy(deep=True)
                for entry in cached.entries[:result_limit]
            ],
        )


market_service = MarketService()
