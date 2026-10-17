import logging
from typing import List, Optional
from datetime import datetime, timedelta
from pymongo.asynchronous.database import AsyncDatabase

from app.database import get_database
from app.models.stock import Prediction
from app.config import settings

logger = logging.getLogger(__name__)

class PredictionsService:
    def __init__(self):
        self.db: Optional[AsyncDatabase] = None

    async def initialize(self):
        """Initialize database connection"""
        if self.db is None:
            self.db = await get_database()

    async def get_available_dates(self, market: str = "us", limit: Optional[int] = None, is_premium: bool = False) -> List[str]:
        """Get available dates for predictions from MongoDB

        Args:
            market: Market to query (default: "us")
            limit: Maximum number of dates to return (default: None)
            is_premium: Whether the user has premium access (default: False)

        Returns:
            List of available dates as strings
        """
        try:
            # Build match filter
            match_filter = {"market": market}

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

            cursor = self.db.predictions.aggregate(pipeline)
            dates = []
            async for doc in cursor:
                dates.append(doc["_id"])

            user_type = "premium" if is_premium else "free"
            logger.debug(f"Found {len(dates)} prediction dates for market {market} (limit: {limit}) for {user_type} user")
            return dates

        except Exception as e:
            user_type = "premium" if is_premium else "free"
            logger.error(f"Failed to get prediction dates for {user_type} user: {e}")
            return []

    async def get_predictions_by_date(self, date: str, market: str = "us") -> List[Prediction]:
        """Get predictions for a specific date and market"""
        try:
            cursor = self.db.predictions.find({
                "date": date,
                "market": market
            })

            predictions = []
            async for doc in cursor:
                # Convert MongoDB document to Prediction model
                prediction = Prediction(
                    instrument=doc["instrument"],
                    date=datetime.strptime(date, '%Y-%m-%d')
                )

                # Map prediction values from the predictions dict
                prediction_data = doc.get("predictions", {})
                if "us_rise_1d" in prediction_data:
                    prediction.us_rise_1d = prediction_data["us_rise_1d"]
                if "us_rise_5d" in prediction_data:
                    prediction.us_rise_5d = prediction_data["us_rise_5d"]
                if "us_decline_1d" in prediction_data:
                    prediction.us_decline_1d = prediction_data["us_decline_1d"]
                if "us_decline_5d" in prediction_data:
                    prediction.us_decline_5d = prediction_data["us_decline_5d"]

                predictions.append(prediction)

            logger.debug(f"Retrieved {len(predictions)} predictions for {date}")
            return predictions

        except Exception as e:
            logger.error(f"Failed to get predictions for {date}: {e}")
            return []

    async def get_latest_predictions(self, market: str = "us", is_premium: bool = False) -> List[Prediction]:
        """Get predictions for the latest available date

        Args:
            market: Market to query (default: "us")
            is_premium: Whether the user has premium access (default: False)

        Returns:
            List of Prediction objects for the latest available date
        """
        try:
            # Build query filter
            query_filter = {"market": market}

            # For non-premium users, add date filter to exclude recent data
            if not is_premium:
                cutoff_date = datetime.now() - timedelta(days=settings.free_user_days_limit)
                cutoff_date_str = cutoff_date.strftime('%Y-%m-%d')
                query_filter["date"] = {"$lte": cutoff_date_str}

            # Get the latest date directly from the predictions collection
            latest_doc = await self.db.predictions.find_one(
                query_filter,
                sort=[("date", -1)]
            )

            if not latest_doc:
                user_type = "premium" if is_premium else "free"
                logger.warning(f"No predictions found for market {market} for {user_type} user")
                return []

            latest_date = latest_doc["date"]

            # Get all predictions for the latest date
            cursor = self.db.predictions.find({
                "date": latest_date,
                "market": market
            })

            predictions = []
            async for doc in cursor:
                # Convert MongoDB document to Prediction model
                prediction = Prediction(
                    instrument=doc["instrument"],
                    date=datetime.strptime(latest_date, '%Y-%m-%d')
                )

                # Map prediction values from the predictions dict
                prediction_data = doc.get("predictions", {})
                if "us_rise_1d" in prediction_data:
                    prediction.us_rise_1d = prediction_data["us_rise_1d"]
                if "us_rise_5d" in prediction_data:
                    prediction.us_rise_5d = prediction_data["us_rise_5d"]
                if "us_decline_1d" in prediction_data:
                    prediction.us_decline_1d = prediction_data["us_decline_1d"]
                if "us_decline_5d" in prediction_data:
                    prediction.us_decline_5d = prediction_data["us_decline_5d"]

                predictions.append(prediction)

            user_type = "premium" if is_premium else "free"
            logger.debug(f"Retrieved {len(predictions)} latest predictions for market {market} on {latest_date} for {user_type} user")
            return predictions

        except Exception as e:
            user_type = "premium" if is_premium else "free"
            logger.error(f"Failed to get latest predictions for market {market} for {user_type} user: {e}")
            return []

    async def get_predictions_by_date_and_instrument(
        self,
        date: str,
        instrument: str,
        market: str = "us"
    ) -> Optional[Prediction]:
        """Get prediction for a specific date and instrument"""
        try:
            doc = await self.db.predictions.find_one({
                "date": date,
                "instrument": instrument,
                "market": market
            })

            if not doc:
                logger.debug(f"No prediction found for {instrument} on {date}")
                return None

            # Convert MongoDB document to Prediction model
            prediction = Prediction(
                instrument=doc["instrument"],
                date=datetime.strptime(date, '%Y-%m-%d')
            )

            # Map prediction values from the predictions dict
            prediction_data = doc.get("predictions", {})
            if "us_rise_1d" in prediction_data:
                prediction.us_rise_1d = prediction_data["us_rise_1d"]
            if "us_rise_5d" in prediction_data:
                prediction.us_rise_5d = prediction_data["us_rise_5d"]
            if "us_decline_1d" in prediction_data:
                prediction.us_decline_1d = prediction_data["us_decline_1d"]
            if "us_decline_5d" in prediction_data:
                prediction.us_decline_5d = prediction_data["us_decline_5d"]

            logger.debug(f"Retrieved prediction for {instrument} on {date}")
            return prediction

        except Exception as e:
            logger.error(f"Failed to get prediction for {instrument} on {date}: {e}")
            return None

# Create global predictions service instance
predictions_service = PredictionsService()
