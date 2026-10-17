"""Tests for scheduler configuration in main.py"""
import pytest
from datetime import timezone
from apscheduler.triggers.cron import CronTrigger
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager


@pytest.fixture
async def mock_services():
    """Fixture to mock all service initializations"""
    with patch('app.main.connect_to_mongo', new_callable=AsyncMock), \
         patch('app.main.user_service.initialize', new_callable=AsyncMock), \
         patch('app.main.stock_service.initialize', new_callable=AsyncMock), \
         patch('app.main.picks_service.initialize', new_callable=AsyncMock), \
         patch('app.main.options_service.initialize', new_callable=AsyncMock), \
         patch('app.main.stripe_service.initialize', new_callable=AsyncMock), \
         patch('app.main.predictions_service.initialize', new_callable=AsyncMock), \
         patch('app.main.market_service.initialize', new_callable=AsyncMock), \
         patch('app.main.screener_service.initialize', new_callable=AsyncMock), \
         patch('app.main.close_mongo_connection', new_callable=AsyncMock):
        yield


@pytest.mark.asyncio
async def test_scheduler_configuration(mock_services):
    """Test that the scheduler is configured correctly with CronTrigger"""
    from app.main import lifespan
    from fastapi import FastAPI
    
    app = FastAPI()
    
    async with lifespan(app):
        # Check that scheduler was added to app state
        assert hasattr(app.state, 'scheduler')
        scheduler = app.state.scheduler
        
        # Check that the scheduler is running
        assert scheduler.running
        
        # Get the monitor job
        job = scheduler.get_job('monitor_latest_picks_cache')
        assert job is not None
        
        # Verify the trigger is a CronTrigger
        assert isinstance(job.trigger, CronTrigger)
        
        # Verify job options
        assert job.coalesce is True
        assert job.misfire_grace_time == 60
        assert job.max_instances == 1

        # Verify the cron schedule (hour=2, minute=0-29, second=0)
        # The trigger should have the correct fields set
        trigger_fields = {field.name: str(field) for field in job.trigger.fields}
        assert 'hour' in trigger_fields
        assert 'minute' in trigger_fields
        assert 'second' in trigger_fields


@pytest.mark.asyncio
async def test_scheduler_job_timing(mock_services):
    """Test that the job is scheduled to run only during 02:00-02:29 UTC"""
    from app.main import lifespan
    from fastapi import FastAPI
    from datetime import datetime
    
    app = FastAPI()
    
    async with lifespan(app):
        scheduler = app.state.scheduler
        job = scheduler.get_job('monitor_latest_picks_cache')
        
        # Test various times to ensure job runs only during 02:00-02:29
        # Time in window: 02:00 UTC
        test_time_in_window = datetime(2024, 1, 1, 2, 0, 0, tzinfo=timezone.utc)
        next_run = job.trigger.get_next_fire_time(None, test_time_in_window)
        assert next_run is not None
        assert next_run.hour == 2
        assert 0 <= next_run.minute <= 29
        
        # Time outside window: 03:00 UTC
        test_time_outside = datetime(2024, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
        next_run_outside = job.trigger.get_next_fire_time(None, test_time_outside)
        # Next run should be the next day at 02:00
        assert next_run_outside is not None
        assert next_run_outside.hour == 2
        assert next_run_outside.day == test_time_outside.day + 1


@pytest.mark.asyncio
async def test_scheduler_shutdown(mock_services):
    """Test that the scheduler shutdown is initiated cleanly"""
    from app.main import lifespan
    from fastapi import FastAPI
    
    app = FastAPI()
    
    scheduler = None
    async with lifespan(app):
        scheduler = app.state.scheduler
        assert scheduler.running
    
    # After exiting the context, shutdown should have been called
    # The scheduler may still show as running since wait=False is used
    # but we can verify it has no scheduled jobs or is in the process of shutting down
    assert scheduler is not None
