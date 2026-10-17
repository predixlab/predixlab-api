"""Unit tests for the cache module."""
import pytest
import time
from datetime import datetime, timedelta, timezone

from app.services.cache import InMemoryCache, CacheKeyBuilder
from app.models.stock import Direction


class TestInMemoryCache:
    """Tests for InMemoryCache class."""

    def test_set_and_get(self):
        """Test setting and getting a value from cache."""
        cache = InMemoryCache(default_ttl_minutes=5)
        
        cache.set("test_key", "test_value")
        result = cache.get("test_key")
        
        assert result == "test_value"

    def test_get_nonexistent_key(self):
        """Test getting a nonexistent key returns None."""
        cache = InMemoryCache(default_ttl_minutes=5)
        
        result = cache.get("nonexistent_key")
        
        assert result is None

    def test_set_with_custom_ttl(self):
        """Test setting a value with custom TTL."""
        cache = InMemoryCache(default_ttl_minutes=5)
        
        cache.set("test_key", "test_value", ttl_minutes=10)
        result = cache.get("test_key")
        
        assert result == "test_value"

    def test_get_expired_key(self):
        """Test that expired keys return None."""
        cache = InMemoryCache(default_ttl_minutes=1)
        
        # Manually insert an expired entry
        cache.cache["test_key"] = ("test_value", time.time() - 60)
        
        result = cache.get("test_key")
        
        assert result is None
        # Key should be removed after getting
        assert "test_key" not in cache.cache

    def test_delete_existing_key(self):
        """Test deleting an existing key."""
        cache = InMemoryCache(default_ttl_minutes=5)
        cache.set("test_key", "test_value")
        
        result = cache.delete("test_key")
        
        assert result is True
        assert cache.get("test_key") is None

    def test_delete_nonexistent_key(self):
        """Test deleting a nonexistent key."""
        cache = InMemoryCache(default_ttl_minutes=5)
        
        result = cache.delete("nonexistent_key")
        
        assert result is False

    def test_clear(self):
        """Test clearing all cache entries."""
        cache = InMemoryCache(default_ttl_minutes=5)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        
        cache.clear()
        
        assert cache.get("key1") is None
        assert cache.get("key2") is None
        assert cache.get("key3") is None

    def test_cleanup_expired(self):
        """Test cleaning up expired entries."""
        cache = InMemoryCache(default_ttl_minutes=5)
        
        # Add some expired entries
        cache.cache["expired1"] = ("value1", time.time() - 60)
        cache.cache["expired2"] = ("value2", time.time() - 60)
        # Add a valid entry
        cache.set("valid", "value")
        
        removed_count = cache.cleanup_expired()
        
        assert removed_count == 2
        assert cache.get("valid") == "value"

    def test_get_stats(self):
        """Test getting cache statistics."""
        cache = InMemoryCache(default_ttl_minutes=5)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        # Add an expired entry
        cache.cache["expired"] = ("value", time.time() - 60)
        
        stats = cache.get_stats()
        
        assert stats["total_entries"] == 3
        assert stats["active_entries"] == 2
        assert stats["expired_entries"] == 1
        assert "cache_size_mb" in stats

    def test_cache_complex_objects(self):
        """Test caching complex objects."""
        cache = InMemoryCache(default_ttl_minutes=5)
        
        complex_object = {
            "list": [1, 2, 3],
            "nested": {"key": "value"},
            "tuple": (1, 2, 3)
        }
        
        cache.set("complex", complex_object)
        result = cache.get("complex")
        
        assert result == complex_object


class TestCacheKeyBuilder:
    """Tests for CacheKeyBuilder class."""

    def test_available_dates_without_limit(self):
        """Test building available dates key without limit."""
        key = CacheKeyBuilder.available_dates()
        
        assert key == "available_dates:limit:all"

    def test_available_dates_with_limit(self):
        """Test building available dates key with limit."""
        key = CacheKeyBuilder.available_dates(limit=100)
        
        assert key == "available_dates:limit:100"

    def test_daily_picks_without_direction(self):
        """Test building daily picks key without direction."""
        key = CacheKeyBuilder.daily_picks("2024-01-01")
        
        assert key == "daily_picks:2024-01-01:all"

    def test_daily_picks_with_direction(self):
        """Test building daily picks key with direction."""
        key = CacheKeyBuilder.daily_picks("2024-01-01", Direction.RISING)
        
        assert "daily_picks:2024-01-01:" in key
        assert "rising" in key.lower()

    def test_predictions(self):
        """Test building predictions key."""
        key = CacheKeyBuilder.predictions("2024-01-01")
        
        assert key == "predictions:2024-01-01"

    def test_stock_data(self):
        """Test building stock data key."""
        key = CacheKeyBuilder.stock_data("AAPL")
        
        assert key == "stock_data:AAPL"

    def test_stock_data_lowercase_input(self):
        """Test building stock data key with lowercase input."""
        key = CacheKeyBuilder.stock_data("aapl")
        
        assert key == "stock_data:AAPL"

    def test_all_stocks(self):
        """Test building all stocks key."""
        key = CacheKeyBuilder.all_stocks()
        
        assert key == "all_stocks"

    def test_trading_calendar(self):
        """Test building trading calendar key."""
        key = CacheKeyBuilder.trading_calendar()
        
        assert key == "trading_calendar"

    def test_picks_history(self):
        """Test building picks history key."""
        key = CacheKeyBuilder.picks_history("AAPL", "2024-01-01", "2024-01-31")
        
        assert key == "picks_history:AAPL:2024-01-01:2024-01-31"

    def test_picks_history_lowercase_input(self):
        """Test building picks history key with lowercase input."""
        key = CacheKeyBuilder.picks_history("aapl", "2024-01-01", "2024-01-31")
        
        assert key == "picks_history:AAPL:2024-01-01:2024-01-31"

    def test_backtest_performance(self):
        """Test building backtest performance key."""
        key = CacheKeyBuilder.backtest_performance("AAPL", "hash123")
        
        assert key == "backtest_performance:AAPL:hash123"

    def test_backtest_result_premium_user(self):
        """Test building backtest result key for premium user."""
        premium_expires = datetime.now(timezone.utc) + timedelta(days=30)
        key = CacheKeyBuilder.backtest_result("AAPL", 90, "premium", premium_expires)
        
        assert "backtest_result:AAPL:90:premium:premium" in key

    def test_backtest_result_free_user(self):
        """Test building backtest result key for free user."""
        key = CacheKeyBuilder.backtest_result("AAPL", 90, "free", None)
        
        assert "backtest_result:AAPL:90:free:free" in key

    def test_backtest_result_admin_user(self):
        """Test building backtest result key for admin user."""
        key = CacheKeyBuilder.backtest_result("AAPL", 90, "admin", None)
        
        assert "backtest_result:AAPL:90:admin:premium" in key

    def test_backtest_result_expired_premium(self):
        """Test building backtest result key for expired premium user."""
        expired = datetime.now(timezone.utc) - timedelta(days=1)
        key = CacheKeyBuilder.backtest_result("AAPL", 90, "premium", expired)
        
        assert "backtest_result:AAPL:90:premium:free" in key
