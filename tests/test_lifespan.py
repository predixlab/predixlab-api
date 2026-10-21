import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app import main


@pytest.mark.parametrize("request_fails", [False, True])
async def test_cleanup_starts_and_is_cancelled_before_database_shutdown(request_fails):
    app = FastAPI()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def cleanup():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def close_database():
        assert stopped.is_set()
        assert all(task.done() for task in app.state.background_tasks)

    services = (
        "user_service", "stock_service", "picks_service", "options_service",
        "stripe_service", "predictions_service", "market_service", "analysis_service",
        "screener_service", "screener_strategy_service", "watchlist_service",
        "alpharank_service", "referral_service",
    )
    with ExitStack() as stack:
        for name in services:
            stack.enter_context(patch.object(getattr(main, name), "initialize", AsyncMock()))
        connect = stack.enter_context(patch.object(main, "connect_to_mongo", AsyncMock()))
        close = stack.enter_context(patch.object(
            main, "close_mongo_connection", AsyncMock(side_effect=close_database)
        ))
        scheduler = MagicMock()
        stack.enter_context(patch.object(main, "AsyncIOScheduler", return_value=scheduler))
        stack.enter_context(patch.object(main, "run_expiry_cleanup", cleanup))

        async def exercise_lifespan():
            async with main.lifespan(app):
                await asyncio.wait_for(started.wait(), timeout=1)
                assert len(app.state.background_tasks) == 1
                task = app.state.background_tasks[0]
                assert task.get_name() == "expiry_cleanup"
                assert not task.done()
                close.assert_not_awaited()
                if request_fails:
                    raise RuntimeError("application error")

        if request_fails:
            with pytest.raises(RuntimeError, match="application error"):
                await exercise_lifespan()
        else:
            await exercise_lifespan()

        connect.assert_awaited_once()
        close.assert_awaited_once()
        scheduler.start.assert_called_once()
        scheduler.shutdown.assert_called_once_with(wait=False)
        assert app.state.background_tasks[0].cancelled()
