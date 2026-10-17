import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from pymongo.asynchronous.database import AsyncDatabase

from app.database import get_database
from app.models.pivot import PickEvent
from app.models.stock import DailyPick, Direction
from app.config import settings

logger = logging.getLogger(__name__)

PUBLIC_PICK_EVENTS = tuple(event.value for event in PickEvent)

class PicksService:
    def __init__(self):
        self.db: Optional[AsyncDatabase] = None

    @staticmethod
    def _to_direction(value: str) -> Direction:
        direction_map = {
            "long": Direction.RISING,
            "short": Direction.FALLING,
        }
        direction = direction_map.get(value)
        if direction is None:
            direction = Direction(value)
        return direction

    @classmethod
    def _to_daily_pick(cls, document: Dict[str, Any]) -> DailyPick:
        return DailyPick(
            instrument=document.get("symbol") or document["instrument"],
            direction=cls._to_direction(document["direction"]),
            date=datetime.strptime(document["date"], "%Y-%m-%d"),
            score=document.get("score"),
            strategy=document.get("event") or document.get("strategy"),
        )

    async def initialize(self):
        """Initialize database connection"""
        if self.db is None:
            self.db = await get_database()

    async def get_available_dates(self, market: str = "us", limit: Optional[int] = None, is_premium: bool = False) -> List[str]:
        """Get available dates for picks from MongoDB

        Args:
            market: Market to query (default: "us")
            limit: Maximum number of dates to return (default: None)
            is_premium: Whether the user has premium access (default: False)

        Returns:
            List of available dates as strings
        """
        try:
            # Build match filter
            match_filter = {
                "market": market,
                "event": {"$in": PUBLIC_PICK_EVENTS},
            }

            # For non-premium users, add date filter to exclude recent data
            if not is_premium:
                cutoff_date = datetime.now() - timedelta(days=settings.free_user_days_limit)
                cutoff_date_str = cutoff_date.strftime('%Y-%m-%d')
                match_filter["date"] = {"$lte": cutoff_date_str}

            pipeline = [
                {"$match": match_filter},
                {"$group": {"_id": "$date"}},
                {"$sort": {"_id": -1}}
            ]

            # Add limit if specified
            if limit is not None and limit > 0:
                pipeline.append({"$limit": limit})

            # aggregate() returns a coroutine, await it to get the cursor
            cursor = await self.db.pivot_picks.aggregate(pipeline)
            results = await cursor.to_list(length=None)
            dates = [doc["_id"] for doc in results]

            user_type = "premium" if is_premium else "free"
            logger.debug(f"Found {len(dates)} picks dates for market {market} (limit: {limit}) for {user_type} user")
            return dates

        except Exception as e:
            user_type = "premium" if is_premium else "free"
            logger.error(f"Failed to get picks dates for {user_type} user: {e}")
            return []

    async def get_picks_by_date(self, date: str, direction: Optional[Direction] = None, market: str = "us") -> List[DailyPick]:
        """Get picks for a specific date and optional direction and market"""
        try:
            query = {
                "date": date,
                "market": market,
                "event": {"$in": PUBLIC_PICK_EVENTS},
            }

            # Add direction filter only if specified
            if direction is not None:
                query["direction"] = (
                    "long" if direction == Direction.RISING else "short"
                )

            cursor = self.db.pivot_picks.find(query)

            picks = []
            async for doc in cursor:
                picks.append(self._to_daily_pick(doc))

            direction_str = direction.value if direction else "all directions"
            logger.debug(f"Retrieved {len(picks)} picks for {date} {direction_str}")
            return picks

        except Exception as e:
            direction_str = direction.value if direction else "all directions"
            logger.error(f"Failed to get picks for {date} {direction_str}: {e}")
            return []

    async def get_picks_by_symbol(self, symbol: str, start_date: datetime, end_date: datetime, market: str = "us") -> List[Dict[str, Any]]:
        """Get picks for a specific symbol within a date range"""
        try:
            start_date_str = start_date.strftime('%Y-%m-%d')
            end_date_str = end_date.strftime('%Y-%m-%d')

            cursor = self.db.pivot_picks.find({
                "symbol": symbol,
                "market": market,
                "event": {"$in": PUBLIC_PICK_EVENTS},
                "date": {
                    "$gte": start_date_str,
                    "$lte": end_date_str
                }
            }).sort("date", 1)

            picks = []
            async for doc in cursor:
                picks.append({
                    "date": doc["date"],
                    "direction": self._to_direction(doc["direction"]).value,
                    "score": doc.get("score"),
                    "strategy": doc.get("event") or doc.get("strategy")
                })

            logger.debug(f"Retrieved {len(picks)} picks for {symbol} from {start_date_str} to {end_date_str}")
            return picks

        except Exception as e:
            logger.error(f"Failed to get picks for {symbol} in date range: {e}")
            return []

    async def get_latest_picks(self, market: str = "us", is_premium: bool = False) -> List[DailyPick]:
        """Get all picks for the latest available date

        Args:
            market: Market to query (default: "us")
            is_premium: Whether the user has premium access (default: False)

        Returns:
            List of DailyPick objects for the latest available date
        """
        try:
            # Build query filter
            query_filter = {
                "market": market,
                "event": {"$in": PUBLIC_PICK_EVENTS},
            }

            # For non-premium users, add date filter to exclude recent data
            if not is_premium:
                cutoff_date = datetime.now() - timedelta(days=settings.free_user_days_limit)
                cutoff_date_str = cutoff_date.strftime('%Y-%m-%d')
                query_filter["date"] = {"$lte": cutoff_date_str}

            # Get the latest date directly from the pivot picks collection
            latest_doc = await self.db.pivot_picks.find_one(
                query_filter,
                sort=[("date", -1)]
            )

            if not latest_doc:
                user_type = "premium" if is_premium else "free"
                logger.warning(f"No picks found for market {market} for {user_type} user")
                return []

            latest_date = latest_doc["date"]

            # Get all picks for the latest date
            cursor = self.db.pivot_picks.find({
                "date": latest_date,
                "market": market,
                "event": {"$in": PUBLIC_PICK_EVENTS},
            })

            picks = []
            async for doc in cursor:
                picks.append(self._to_daily_pick(doc))

            user_type = "premium" if is_premium else "free"
            logger.debug(f"Retrieved {len(picks)} latest picks for market {market} on {latest_date} for {user_type} user")
            return picks

        except Exception as e:
            user_type = "premium" if is_premium else "free"
            logger.error(f"Failed to get latest picks for market {market} for {user_type} user: {e}")
            return []

# Create global picks service instance
picks_service = PicksService()
