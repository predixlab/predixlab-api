import time
from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
import threading
import logging

from app.core.security import is_premium_user
from app.models.stock import Direction

logger = logging.getLogger(__name__)

class InMemoryCache:
    """In-memory cache with TTL (Time To Live) support"""

    def __init__(self, default_ttl_minutes: int = 30):
        self.cache: Dict[str, Tuple[Any, float]] = {}
        self.default_ttl = default_ttl_minutes * 60  # Convert to seconds
        self._lock = threading.RLock()
        logger.info(f"Initialized cache with {default_ttl_minutes} minutes TTL")

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if it exists and is not expired"""
        with self._lock:
            if key not in self.cache:
                return None

            value, expire_time = self.cache[key]
            current_time = time.time()

            if current_time > expire_time:
                # Entry expired, remove it
                del self.cache[key]
                logger.debug(f"Cache entry expired and removed: {key}")
                return None

            logger.debug(f"Cache hit: {key}")
            return value

    def set(self, key: str, value: Any, ttl_minutes: Optional[int] = None) -> None:
        """Set value in cache with TTL"""
        ttl_seconds = (ttl_minutes * 60) if ttl_minutes else self.default_ttl
        expire_time = time.time() + ttl_seconds

        with self._lock:
            self.cache[key] = (value, expire_time)
            logger.debug(f"Cache set: {key} (expires in {ttl_seconds/60:.1f} minutes)")

    def delete(self, key: str) -> bool:
        """Delete a specific key from cache"""
        with self._lock:
            if key in self.cache:
                del self.cache[key]
                logger.debug(f"Cache entry deleted: {key}")
                return True
            return False

    def clear(self) -> None:
        """Clear all cache entries"""
        with self._lock:
            self.cache.clear()
            logger.info("Cache cleared")

    def cleanup_expired(self) -> int:
        """Remove all expired entries and return count of removed entries"""
        current_time = time.time()
        expired_keys = []

        with self._lock:
            for key, (_, expire_time) in self.cache.items():
                if current_time > expire_time:
                    expired_keys.append(key)

            for key in expired_keys:
                del self.cache[key]

        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")

        return len(expired_keys)

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        current_time = time.time()
        total_entries = len(self.cache)
        expired_entries = 0

        with self._lock:
            for _, expire_time in self.cache.values():
                if current_time > expire_time:
                    expired_entries += 1

        return {
            "total_entries": total_entries,
            "active_entries": total_entries - expired_entries,
            "expired_entries": expired_entries,
            "cache_size_mb": self._estimate_size_mb()
        }

    def _estimate_size_mb(self) -> float:
        """Rough estimation of cache size in MB"""
        import sys
        total_size = 0

        with self._lock:
            for key, (value, _) in self.cache.items():
                total_size += sys.getsizeof(key)
                total_size += sys.getsizeof(value)

        return total_size / (1024 * 1024)


class CacheKeyBuilder:
    """Helper class to build consistent cache keys"""

    @staticmethod
    def available_dates(limit: Optional[int] = None) -> str:
        return f"available_dates:limit:{limit if limit else 'all'}"

    @staticmethod
    def daily_picks(date: str, direction: Optional[Direction] = None) -> str:
        return f"daily_picks:{date}:{direction if direction else 'all'}"

    @staticmethod
    def predictions(date: str) -> str:
        return f"predictions:{date}"

    @staticmethod
    def stock_data(symbol: str) -> str:
        return f"stock_data:{symbol.upper()}"

    @staticmethod
    def all_stocks() -> str:
        return "all_stocks"

    @staticmethod
    def trading_calendar() -> str:
        return "trading_calendar"

    @staticmethod
    def picks_history(symbol: str, start_date: str, end_date: str) -> str:
        return f"picks_history:{symbol.upper()}:{start_date}:{end_date}"

    @staticmethod
    def backtest_performance(symbol: str, picks_hash: str) -> str:
        return f"backtest_performance:{symbol.upper()}:{picks_hash}"

    @staticmethod
    def backtest_result(symbol: str, period_days: int, user_role: str, premium_expires_at: Optional[datetime] = None) -> str:
        """Generate cache key for backtest results, considering user role and premium status"""
        # Create a hash based on user permissions to ensure proper data isolation
        premium_hash = "premium" if is_premium_user(user_role, premium_expires_at) else "free"

        # For admin users, treat as premium
        if user_role == "admin":
            premium_hash = "premium"

        return f"backtest_result:{symbol.upper()}:{period_days}:{user_role}:{premium_hash}"
