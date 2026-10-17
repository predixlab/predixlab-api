"""Tests for cache monitor job"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from app.jobs.cache_monitor_jobs import monitor_latest_picks_cache


@pytest.mark.asyncio
async def test_monitor_latest_picks_cache_no_cached_data():
    """Test that the job does nothing when there's no cached data"""
    with patch('app.jobs.cache_monitor_jobs.stock_service') as mock_stock_service:
        mock_stock_service.cache.get.return_value = None
        
        await monitor_latest_picks_cache()
        
        # Should not attempt to fetch dates or clear cache
        mock_stock_service.clear_cache.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_latest_picks_cache_no_dates_available():
    """Test that the job handles no available dates gracefully"""
    with patch('app.jobs.cache_monitor_jobs.stock_service') as mock_stock_service, \
         patch('app.jobs.cache_monitor_jobs.picks_service') as mock_picks_service:
        
        mock_cached = MagicMock()
        mock_cached.date = "2024-01-01"
        mock_stock_service.cache.get.return_value = mock_cached
        mock_picks_service.get_available_dates = AsyncMock(return_value=[])
        
        await monitor_latest_picks_cache()
        
        # Should not clear cache if no dates available
        mock_stock_service.clear_cache.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_latest_picks_cache_clears_when_newer_date():
    """Test that cache is cleared when a newer date is available"""
    with patch('app.jobs.cache_monitor_jobs.stock_service') as mock_stock_service, \
         patch('app.jobs.cache_monitor_jobs.picks_service') as mock_picks_service:
        
        mock_cached = MagicMock()
        mock_cached.date = "2024-01-01"
        mock_stock_service.cache.get.return_value = mock_cached
        mock_picks_service.get_available_dates = AsyncMock(return_value=["2024-01-02"])
        
        await monitor_latest_picks_cache()
        
        # Should clear cache when newer date is available
        mock_stock_service.clear_cache.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_latest_picks_cache_no_clear_when_same_date():
    """Test that cache is not cleared when the date is the same"""
    with patch('app.jobs.cache_monitor_jobs.stock_service') as mock_stock_service, \
         patch('app.jobs.cache_monitor_jobs.picks_service') as mock_picks_service:
        
        mock_cached = MagicMock()
        mock_cached.date = "2024-01-01"
        mock_stock_service.cache.get.return_value = mock_cached
        mock_picks_service.get_available_dates = AsyncMock(return_value=["2024-01-01"])
        
        await monitor_latest_picks_cache()
        
        # Should not clear cache when date is the same
        mock_stock_service.clear_cache.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_latest_picks_cache_no_clear_when_older_date():
    """Test that cache is not cleared when available date is older"""
    with patch('app.jobs.cache_monitor_jobs.stock_service') as mock_stock_service, \
         patch('app.jobs.cache_monitor_jobs.picks_service') as mock_picks_service:
        
        mock_cached = MagicMock()
        mock_cached.date = "2024-01-02"
        mock_stock_service.cache.get.return_value = mock_cached
        mock_picks_service.get_available_dates = AsyncMock(return_value=["2024-01-01"])
        
        await monitor_latest_picks_cache()
        
        # Should not clear cache when available date is older
        mock_stock_service.clear_cache.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_latest_picks_cache_handles_exception():
    """Test that exceptions are handled gracefully"""
    with patch('app.jobs.cache_monitor_jobs.stock_service') as mock_stock_service, \
         patch('app.jobs.cache_monitor_jobs.picks_service') as mock_picks_service:
        
        mock_cached = MagicMock()
        mock_cached.date = "2024-01-01"
        mock_stock_service.cache.get.return_value = mock_cached
        mock_picks_service.get_available_dates = AsyncMock(side_effect=Exception("Test error"))
        
        # Should not raise an exception
        await monitor_latest_picks_cache()
        
        # Cache should not be cleared on error
        mock_stock_service.clear_cache.assert_not_called()
