import logging
import re
from typing import List, Optional
from pymongo.asynchronous.database import AsyncDatabase
from datetime import datetime, timedelta

from app.database import get_database
from app.config import settings
from app.models.options import (
    AnomalyOption,
    GexAnalysis,
    GexHighlightSignal,
    GexHighlightsResponse,
    OptionsStructureAnalysis,
    OptionsStructureHighlightSignal,
    OptionsStructureHighlightsResponse,
)

logger = logging.getLogger(__name__)

MAX_OPTIONS_ACTIVITY_TICKERS = 20


def _without_legacy_gex_scopes(document: Optional[dict]) -> Optional[dict]:
    """Remove retired scope keys from stored snapshots before validation."""
    if document is not None:
        document["scopes"] = [
            scope
            for scope in document.get("scopes", [])
            if scope.get("key") != "0dte"
        ]
    return document


class OptionsService:
    def __init__(self):
        self.db: Optional[AsyncDatabase] = None

    async def initialize(self):
        """Initialize database connection"""
        if self.db is None:
            self.db = await get_database()

    async def get_anomalies(self, limit: int, offset: int = 0, is_premium: bool = False) -> List[AnomalyOption]:
        try:
            limit = min(max(limit, 1), MAX_OPTIONS_ACTIVITY_TICKERS)
            # Build query filter
            query_filter = {}

            # For non-premium users, exclude recent data
            if not is_premium:
                cutoff_date = datetime.now() - timedelta(days=settings.free_user_days_limit)
                cutoff_date_str = cutoff_date.strftime('%Y-%m-%d')
                query_filter["date"] = {"$lte": cutoff_date_str}

            # Sort by date desc first, then anomaly_score desc so latest dates come first
            # and within the same date the most anomalous records are returned first.
            cursor = self.db.options_anomalies.find(query_filter).sort([("date", -1), ("anomaly_score", -1)]).skip(offset).limit(limit)
            results = []
            async for doc in cursor:
                # Convert ObjectId to string for JSON serialization
                if "_id" in doc:
                    try:
                        doc["_id"] = str(doc["_id"])
                    except Exception:
                        pass

                results.append(doc)

            return results

        except Exception as e:
            logger.error(f"Failed to query options anomalies: {e}", exc_info=True)
            return {}

    async def get_anomalies_by_date(
        self,
        date: str,
        limit: int = MAX_OPTIONS_ACTIVITY_TICKERS,
        is_premium: bool = False,
    ) -> List[AnomalyOption]:
        try:
            if not is_premium:
                cutoff_date = datetime.now() - timedelta(days=settings.free_user_days_limit)
                if date > cutoff_date.strftime("%Y-%m-%d"):
                    return []

            # Sort by date desc first, then anomaly_score desc so results are stable
            # (date is same for the filter, but keeping the compound sort for consistency).
            cursor = self.db.options_anomalies.find({"date": date}).sort(
                [("date", -1), ("anomaly_score", -1)]
            ).limit(min(max(limit, 1), MAX_OPTIONS_ACTIVITY_TICKERS))
            results = []
            async for doc in cursor:
                # Convert ObjectId to string for JSON serialization
                if "_id" in doc:
                    try:
                        doc["_id"] = str(doc["_id"])
                    except Exception:
                        pass

                results.append(doc)

            return results

        except Exception as e:
            logger.error(f"Failed to query options anomalies: {e}", exc_info=True)
            return {}

    async def get_latest_anomalies(
        self,
        is_premium: bool = False,
        limit: int = MAX_OPTIONS_ACTIVITY_TICKERS,
    ) -> List[AnomalyOption]:
        """
        Return anomalies for the latest date present in the collection.

        Args:
            is_premium: Whether the user has premium access (default: False)

        Returns:
            A dict mapping symbol -> list of anomaly documents for the latest date.
        """
        try:
            query_filter = {}
            if not is_premium:
                cutoff_date = datetime.now() - timedelta(days=settings.free_user_days_limit)
                query_filter["date"] = {"$lte": cutoff_date.strftime('%Y-%m-%d')}

            latest_doc = await self.db.options_anomalies.find_one(
                query_filter,
                sort=[("date", -1)],
            )
            if not latest_doc:
                user_type = "premium" if is_premium else "free"
                logger.debug(f"No option anomalies found in collection for {user_type} user")
                return {}

            latest_date = latest_doc.get("date")
            if not latest_date:
                logger.debug("Latest anomaly document does not contain a 'date' field")
                return {}

            return await self.get_anomalies_by_date(
                latest_date,
                limit=limit,
                is_premium=is_premium,
            )
        except Exception as e:
            logger.error(f"Failed to query latest options anomalies: {e}", exc_info=True)
            return {}

    async def get_anomaly_dates(
        self,
        limit: int = 60,
        is_premium: bool = False,
    ) -> List[str]:
        """Return actual accessible option-activity dates, newest first."""
        query_filter = {}
        if not is_premium:
            cutoff = datetime.now() - timedelta(days=settings.free_user_days_limit)
            query_filter["date"] = {"$lte": cutoff.strftime("%Y-%m-%d")}
        try:
            dates = await self.db.options_anomalies.distinct(
                "date",
                query_filter,
            )
            return sorted(
                (str(value) for value in dates),
                reverse=True,
            )[:limit]
        except Exception as e:
            logger.error(
                f"Failed to query option anomaly dates: {e}",
                exc_info=True,
            )
            return []

    async def get_gex(
        self,
        symbol: str,
        date: str,
        is_premium: bool = False,
    ) -> Optional[GexAnalysis]:
        """Return one symbol's GEX analysis for a trading date."""
        if not is_premium:
            cutoff = datetime.now() - timedelta(days=settings.free_user_days_limit)
            if date > cutoff.strftime("%Y-%m-%d"):
                return None
        try:
            document = await self.db.options_gex.find_one(
                {"date": date, "symbol": symbol.upper()}
            )
            document = _without_legacy_gex_scopes(document)
            if document and "_id" in document:
                document["_id"] = str(document["_id"])
            return document
        except Exception as e:
            logger.error(
                f"Failed to query GEX for {symbol} on {date}: {e}",
                exc_info=True,
            )
            return None

    async def get_latest_gex(
        self,
        symbol: str,
        is_premium: bool = False,
    ) -> Optional[GexAnalysis]:
        """Return the latest accessible GEX analysis for one symbol."""
        query_filter = {"symbol": symbol.upper()}
        if not is_premium:
            cutoff = datetime.now() - timedelta(days=settings.free_user_days_limit)
            query_filter["date"] = {"$lte": cutoff.strftime("%Y-%m-%d")}
        try:
            document = await self.db.options_gex.find_one(
                query_filter,
                sort=[("date", -1)],
            )
            document = _without_legacy_gex_scopes(document)
            if document and "_id" in document:
                document["_id"] = str(document["_id"])
            return document
        except Exception as e:
            logger.error(
                f"Failed to query latest GEX for {symbol}: {e}",
                exc_info=True,
            )
            return None

    async def get_gex_dates(
        self,
        symbol: str,
        limit: int = 60,
        is_premium: bool = False,
    ) -> List[str]:
        """Return actual accessible GEX dates for one symbol, newest first."""
        query_filter = {"symbol": symbol.upper()}
        if not is_premium:
            cutoff = datetime.now() - timedelta(days=settings.free_user_days_limit)
            query_filter["date"] = {"$lte": cutoff.strftime("%Y-%m-%d")}
        try:
            dates = await self.db.options_gex.distinct("date", query_filter)
            return sorted((str(value) for value in dates), reverse=True)[:limit]
        except Exception as e:
            logger.error(
                f"Failed to query GEX dates for {symbol}: {e}",
                exc_info=True,
            )
            return []

    async def get_gex_symbols(
        self,
        query: str = "",
        limit: int = 1000,
        is_premium: bool = False,
    ) -> List[str]:
        """Return symbols that have at least one accessible GEX document."""
        query_filter = {}
        normalized_query = query.strip().upper()
        if normalized_query:
            query_filter["symbol"] = {
                "$regex": re.escape(normalized_query),
                "$options": "i",
            }
        if not is_premium:
            cutoff = datetime.now() - timedelta(days=settings.free_user_days_limit)
            query_filter["date"] = {"$lte": cutoff.strftime("%Y-%m-%d")}
        try:
            symbols = await self.db.options_gex.distinct("symbol", query_filter)
            return sorted(str(value).upper() for value in symbols)[:limit]
        except Exception as e:
            logger.error(
                f"Failed to query available GEX symbols: {e}",
                exc_info=True,
            )
            return []

    async def get_gex_highlights(
        self,
        limit: int = 6,
        signal: Optional[GexHighlightSignal] = None,
        is_premium: bool = False,
    ) -> GexHighlightsResponse:
        """Return precomputed rankings from the latest accessible GEX snapshot."""
        query_filter = {}
        if not is_premium:
            cutoff = datetime.now() - timedelta(days=settings.free_user_days_limit)
            query_filter["date"] = {"$lte": cutoff.strftime("%Y-%m-%d")}

        snapshot = await self.db.options_gex_highlights.find_one(
            query_filter,
            sort=[("date", -1)],
        )
        if not snapshot or not snapshot.get("date"):
            return GexHighlightsResponse(count=0)
        ranking_key = signal or "all"
        selected = snapshot.get("rankings", {}).get(ranking_key, [])[:limit]
        return GexHighlightsResponse(
            date=str(snapshot["date"]),
            items=selected,
            count=len(selected),
        )

    async def get_latest_options_structure(
        self,
        symbol: str,
        is_premium: bool = False,
    ) -> Optional[OptionsStructureAnalysis]:
        """Return the latest accessible two-snapshot options structure."""
        query_filter = {"symbol": symbol.upper()}
        if not is_premium:
            cutoff = datetime.now() - timedelta(days=settings.free_user_days_limit)
            query_filter["date"] = {"$lte": cutoff.strftime("%Y-%m-%d")}
        try:
            document = await self.db.options_structure.find_one(
                query_filter,
                sort=[("date", -1)],
            )
            if document and "_id" in document:
                document["_id"] = str(document["_id"])
            return document
        except Exception as e:
            logger.error(
                f"Failed to query options structure for {symbol}: {e}",
                exc_info=True,
            )
            return None

    async def get_options_structure_highlights(
        self,
        limit: int = 6,
        signal: Optional[OptionsStructureHighlightSignal] = None,
        is_premium: bool = False,
    ) -> OptionsStructureHighlightsResponse:
        """Return latest accessible precomputed IV and positioning rankings."""
        query_filter = {}
        if not is_premium:
            cutoff = datetime.now() - timedelta(days=settings.free_user_days_limit)
            query_filter["date"] = {"$lte": cutoff.strftime("%Y-%m-%d")}
        try:
            snapshot = await self.db.options_structure_highlights.find_one(
                query_filter,
                sort=[("date", -1)],
            )
            if not snapshot or not snapshot.get("date"):
                return OptionsStructureHighlightsResponse(count=0)
            ranking_key = signal or "all"
            selected = snapshot.get("rankings", {}).get(
                ranking_key,
                [],
            )[:limit]
            return OptionsStructureHighlightsResponse(
                date=str(snapshot["date"]),
                items=selected,
                count=len(selected),
            )
        except Exception as e:
            logger.error(
                f"Failed to query options structure highlights: {e}",
                exc_info=True,
            )
            return OptionsStructureHighlightsResponse(count=0)

    async def get_options_structure_symbols(
        self,
        query: str = "",
        limit: int = 1000,
        is_premium: bool = False,
    ) -> List[str]:
        """Return symbols with an accessible options structure snapshot."""
        query_filter = {}
        normalized_query = query.strip().upper()
        if normalized_query:
            query_filter["symbol"] = {
                "$regex": re.escape(normalized_query),
                "$options": "i",
            }
        if not is_premium:
            cutoff = datetime.now() - timedelta(days=settings.free_user_days_limit)
            query_filter["date"] = {"$lte": cutoff.strftime("%Y-%m-%d")}
        try:
            symbols = await self.db.options_structure.distinct(
                "symbol",
                query_filter,
            )
            return sorted(str(value).upper() for value in symbols)[:limit]
        except Exception as e:
            logger.error(
                f"Failed to query options structure symbols: {e}",
                exc_info=True,
            )
            return []

# Create global options service instance
options_service = OptionsService()
