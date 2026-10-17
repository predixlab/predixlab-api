import logging

from app.services.stock_service import stock_service
from app.services.picks_service import picks_service

async def monitor_latest_picks_cache():
    """Run a single check:
    If premium latest picks cached and the latest available date in DB is newer,
    clear all in-memory caches of stock_service.

    This function is scheduled by APScheduler to run only during 02:00-02:29 UTC.
    """
    logger_local = logging.getLogger(__name__)
    try:
        cache_key = "latest_picks:premium"
        cached = stock_service.cache.get(cache_key)
        if cached is not None:
            # Fetch premium available dates (sorted desc by pipeline)
            dates = await picks_service.get_available_dates(market="us", limit=1, is_premium=True)
            if dates:
                latest_available_date = dates[0]
                # Compare lexicographically (YYYY-MM-DD safe)
                if latest_available_date > cached.date:
                    stock_service.clear_cache()
                    logger_local.info(
                        f"Cleared cache: detected newer available date (old={cached.date}, new={latest_available_date})"
                    )
                else:
                    logger_local.debug(
                        f"No cache clear needed (cached={cached.date}, latest_available={latest_available_date})"
                    )
    except Exception:
        logger_local.exception("Error in monitor_latest_picks_cache job")
