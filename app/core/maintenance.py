"""Expiry cleanup for databases that do not enforce custom-field TTL indexes."""

import asyncio
import logging
from datetime import datetime, timezone

from app.config import settings
from app.database import get_database

logger = logging.getLogger(__name__)

EXPIRING_COLLECTIONS = ("verification_codes", "rate_limits", "user_sessions")


async def purge_expired_documents() -> None:
    """Idempotent across replicas; one collection's failure must not block others."""
    db = await get_database()
    now = datetime.now(timezone.utc)
    for name in EXPIRING_COLLECTIONS:
        try:
            result = await db[name].delete_many({"expires_at": {"$lt": now}})
            logger.info("Purged %s expired documents from %s", result.deleted_count, name)
        except Exception:
            logger.exception("Failed to purge expired documents from %s", name)


async def run_expiry_cleanup() -> None:
    """Run once at startup, then periodically until the lifespan cancels the task."""
    while True:
        try:
            await purge_expired_documents()
        except Exception:
            logger.exception("Expired-document cleanup failed")
        await asyncio.sleep(settings.expiry_cleanup_interval_seconds)
