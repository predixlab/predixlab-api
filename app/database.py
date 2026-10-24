from typing import Any

from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError
import logging
from app.config import settings
from app.core.indexes import AGENT_API_KEYS_LIST_INDEX

logger = logging.getLogger(__name__)

class MongoDB:
    client: AsyncMongoClient[dict[str, Any]] | None = None
    database: AsyncDatabase[dict[str, Any]] | None = None

mongodb = MongoDB()

async def connect_to_mongo():
    """Create database connection"""
    try:
        mongodb.client = AsyncMongoClient(settings.mongodb_url)
        mongodb.database = mongodb.client[settings.database_name]

        # Test the connection
        await mongodb.client.admin.command('ping')
        logger.info(f"Connected to MongoDB: {settings.database_name}")

        # Create indexes
        await create_indexes()

    except ServerSelectionTimeoutError as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        raise

async def close_mongo_connection():
    """Close database connection"""
    if mongodb.client:
        await mongodb.client.close()
        logger.info("Disconnected from MongoDB")

async def get_database() -> AsyncDatabase[dict[str, Any]] | None:
    """Get database instance"""
    return mongodb.database


async def _create_index(
    collection_name: str,
    keys: Any,
    **kwargs: Any,
) -> bool:
    """Create one index without preventing unrelated indexes from being created."""
    try:
        db = await get_database()
        if db is None:
            raise RuntimeError("MongoDB connection is not initialized")
        collection = db[collection_name]
        await collection.create_index(keys, **kwargs)
        return True
    except OperationFailure as e:
        if e.code == 85 and "already exists with a different name" in str(e):
            logger.debug(
                "Equivalent index already exists on %s for %s",
                collection_name,
                keys,
            )
            return True
        logger.error(
            "Failed to create index on %s for %s: %s",
            collection_name,
            keys,
            e,
        )
        return False


async def create_indexes():
    """Create database indexes for better performance"""
    index_definitions = [
        ("users", "email", {"unique": True}),
        ("users", "oauth_provider_id", {}),
        ("users", "referral_code", {"unique": True, "sparse": True}),
        ("referrals", "invitee_id", {"unique": True}),
        ("referrals", [("inviter_id", 1), ("created_at", -1)], {}),
        (
            "referral_clicks",
            [("referral_code", 1), ("visitor_id", 1)],
            {"unique": True},
        ),
        ("verification_codes", "email", {}),
        ("verification_codes", "expires_at", {"expireAfterSeconds": 0}),
        ("rate_limits", "expires_at", {"expireAfterSeconds": 0}),
        ("user_sessions", "user_id", {}),
        ("user_sessions", "expires_at", {"expireAfterSeconds": 0}),
        ("agent_api_keys", AGENT_API_KEYS_LIST_INDEX, {}),
        ("predictions", [("date", 1), ("market", 1)], {}),
        (
            "predictions",
            [("date", 1), ("instrument", 1), ("market", 1)],
            {},
        ),
        ("predictions", "instrument", {}),
        ("picks", [("date", 1), ("market", 1)], {}),
        ("picks", [("date", 1), ("market", 1), ("direction", 1)], {}),
        ("picks", [("instrument", 1), ("market", 1), ("date", 1)], {}),
        ("picks", "instrument", {}),
        ("picks", "strategy", {}),
        # Pivot writers own their collection indexes; the API only reads them.
        ("options_anomalies", [("date", -1), ("anomaly_score", -1)], {}),
        ("options_anomalies", [("date", -1)], {}),
        (
            "options_anomalies",
            [("date", -1), ("symbol", 1)],
            {"name": "options_anomalies_date_symbol"},
        ),
    ]
    failed = 0
    for collection_name, keys, options in index_definitions:
        if not await _create_index(collection_name, keys, **options):
            failed += 1

    gex_date_symbol = [("date", -1), ("symbol", 1)]
    if not await _create_index("options_gex", gex_date_symbol, unique=True):
        # Cosmos DB cannot add a unique index after a collection has data.
        fallback_created = await _create_index(
            "options_gex",
            gex_date_symbol,
            name="gex_date_symbol",
        )
        if not fallback_created:
            failed += 1
    if not await _create_index(
        "options_gex",
        [("symbol", 1), ("date", -1)],
        name="gex_symbol_date",
    ):
        failed += 1
    if not await _create_index(
        "options_structure",
        [("date", -1), ("symbol", 1)],
        name="options_structure_date_symbol",
    ):
        failed += 1
    if not await _create_index(
        "options_structure",
        [("symbol", 1), ("date", -1)],
        name="options_structure_symbol_date",
    ):
        failed += 1
    if not await _create_index(
        "options_structure_highlights",
        [("date", -1)],
        name="options_structure_highlights_date",
    ):
        failed += 1

    highlights_date = [("date", -1)]
    if not await _create_index(
        "options_gex_highlights",
        highlights_date,
        unique=True,
    ):
        fallback_created = await _create_index(
            "options_gex_highlights",
            highlights_date,
            name="gex_highlights_date",
        )
        if not fallback_created:
            failed += 1

    if failed:
        logger.warning("Database index setup completed with %s failures", failed)
    else:
        logger.info("Database indexes created successfully")
