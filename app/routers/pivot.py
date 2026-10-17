"""Pivot pick endpoints.

Serves the short list produced by the `predixlab-pivot` nightly job: the top
model events per side for a session, with the observed signal price, supporting
context and historical outcomes. The full scored cross-section is not exposed.

Access follows the platform rule: free users see sessions older than
`settings.free_user_days_limit` days, premium users see everything. The check
lives in one helper so every route enforces it identically.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Optional
import logging
from datetime import datetime, timedelta

from app.models.pivot import (
    PickDirection,
    PickEvent,
    PivotAvailableDatesResponse,
    PivotPerformance,
    PivotPerformanceBreakdown,
    PivotPick,
    PivotPicksResponse,
    PivotRun,
    PivotSummary,
    PivotSymbolHistoryResponse,
)
from app.services.pivot_service import pivot_service
from app.core.dependencies import get_current_user
from app.core.security import is_premium_user
from app.models.user import UserInDB
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pivot", tags=["Pivot"])

#: Upper bound on a single session request. A busy session emits a few dozen
#: events (the walk-forward maximum is 60), so this is generous rather than
#: restrictive.
MAX_PAGE_SIZE = 200


async def _service():
    """Initialize the service lazily, the way the other data routers do."""
    if pivot_service.db is None:
        await pivot_service.initialize()
    return pivot_service


def _premium(current_user: UserInDB) -> bool:
    return is_premium_user(
        current_user.role,
        current_user.premium_expires_at,
        current_user.referral_access_expires_at,
    )


def _assert_date_visible(date: str, is_premium: bool) -> None:
    """Reject a free user's request for a session inside the paywall window."""
    if is_premium:
        return
    cutoff = (datetime.now() - timedelta(days=settings.free_user_days_limit)).strftime("%Y-%m-%d")
    if date > cutoff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Pivot picks for {date} require a premium subscription. "
                f"Free accounts can access sessions on or before {cutoff}."
            ),
        )


def _split(picks: list[PivotPick]) -> tuple[list[PivotPick], list[PivotPick]]:
    """Separate a session's picks into the two sides, each already rank-ordered."""
    longs = [p for p in picks if p.direction == PickDirection.LONG]
    shorts = [p for p in picks if p.direction == PickDirection.SHORT]
    return longs, shorts


@router.get("/dates", response_model=PivotAvailableDatesResponse, summary="Get available pick dates")
async def get_pivot_dates(
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
    limit: int = Query(60, ge=1, le=800, description="Maximum number of dates to return"),
):
    """Sessions that carry pivot picks, newest first."""
    try:
        service = await _service()
        dates = await service.get_available_dates(
            market=market, limit=limit, is_premium=_premium(current_user)
        )
        return PivotAvailableDatesResponse(market=market, dates=dates, count=len(dates))
    except Exception as e:
        logger.error(f"Failed to get pivot dates for market {market}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve pivot dates",
        )


@router.get("/summary", response_model=PivotSummary, summary="Get pivot summary")
async def get_pivot_summary(
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
):
    """Per-side counts plus the strongest names on each side of the book."""
    try:
        service = await _service()
        return await service.get_summary(market=market, is_premium=_premium(current_user))
    except Exception as e:
        logger.error(f"Failed to get pivot summary for market {market}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve pivot summary",
        )


@router.get(
    "/performance",
    response_model=PivotPerformance,
    summary="Forward-horizon outcome of the signals",
)
async def get_pivot_performance(
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
    direction: Optional[PickDirection] = Query(None, description="Restrict to one side"),
    event: Optional[PickEvent] = Query(None, description="Restrict to one event type"),
):
    """What the names did over the horizon after each signal fired.

    Not a strategy backtest. Both the raw and benchmark-relative figures are
    returned because the raw figure alone is affected by broad market direction.
    """
    try:
        service = await _service()
        return await service.get_performance(
            market=market,
            direction=direction.value if direction else None,
            event=event.value if event else None,
        )
    except Exception as e:
        logger.error(f"Failed to get pivot performance for market {market}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve pivot performance",
        )


@router.get(
    "/performance/breakdown",
    response_model=PivotPerformanceBreakdown,
    summary="Signal outcome split by event type",
)
async def get_pivot_performance_breakdown(
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
):
    """Overall forward-horizon outcome plus the same figures per event type."""
    try:
        service = await _service()
        return await service.get_performance_breakdown(market=market)
    except Exception as e:
        logger.error(f"Failed to get pivot performance breakdown for {market}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve pivot performance breakdown",
        )


@router.get("/runs/latest", response_model=PivotRun, summary="Get the latest pivot run")
async def get_latest_pivot_run(
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
):
    """Provenance for the most recent nightly execution."""
    try:
        service = await _service()
        run = await service.get_latest_run(market=market)
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No pivot runs recorded for market: {market}",
            )
        return run
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get latest pivot run for market {market}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve pivot run",
        )


@router.get("/picks/latest", response_model=PivotPicksResponse, summary="Get the latest picks")
async def get_latest_pivot_picks(
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
    direction: Optional[PickDirection] = Query(None, description="Restrict to one side"),
    event: Optional[PickEvent] = Query(None, description="Restrict to one event type"),
    limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
):
    """Events for the newest session the caller may see.

    Declared before `/picks/{date}` so the literal path wins over the
    parameterised one.
    """
    try:
        service = await _service()
        is_premium = _premium(current_user)
        date = await service.get_latest_date(market=market, is_premium=is_premium)
        if not date:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No pivot picks found for market: {market}",
            )

        picks = await service.get_picks_by_date(
            date,
            market=market,
            direction=direction.value if direction else None,
            event=event.value if event else None,
            limit=limit,
        )
        longs, shorts = _split(picks)
        return PivotPicksResponse(
            market=market,
            date=date,
            horizon=picks[0].horizon if picks else None,
            longs=longs,
            shorts=shorts,
            total_count=len(picks),
            counts=await service.count_events(date, market),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get latest pivot picks for market {market}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve pivot picks",
        )


@router.get(
    "/symbols/{symbol}/history",
    response_model=PivotSymbolHistoryResponse,
    summary="Get a symbol's pick history",
)
async def get_pivot_symbol_history(
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
    limit: int = Query(120, ge=1, le=750),
):
    """Every session this symbol was picked, oldest first so it plots directly."""
    try:
        service = await _service()
        items = await service.get_symbol_history(
            symbol, market=market, limit=limit, is_premium=_premium(current_user)
        )
        return PivotSymbolHistoryResponse(
            market=market, symbol=symbol.upper(), items=items, count=len(items)
        )
    except Exception as e:
        logger.error(f"Failed to get pivot history for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve pivot history",
        )


@router.get("/picks/{date}", response_model=PivotPicksResponse, summary="Get picks by date")
async def get_pivot_picks(
    date: str,
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
    direction: Optional[PickDirection] = Query(None, description="Restrict to one side"),
    event: Optional[PickEvent] = Query(None, description="Restrict to one event type"),
    limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
):
    """Events for one session. An empty result means nothing fired that day."""
    try:
        _assert_date_visible(date, _premium(current_user))

        service = await _service()
        picks = await service.get_picks_by_date(
            date,
            market=market,
            direction=direction.value if direction else None,
            event=event.value if event else None,
            limit=limit,
        )
        longs, shorts = _split(picks)
        return PivotPicksResponse(
            market=market,
            date=date,
            horizon=picks[0].horizon if picks else None,
            longs=longs,
            shorts=shorts,
            total_count=len(picks),
            counts=await service.count_events(date, market),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get pivot picks for {market} on {date}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve pivot picks",
        )


@router.get(
    "/picks/{date}/{symbol}",
    response_model=PivotPick,
    summary="Get a symbol's pick for a date",
)
async def get_pivot_pick_for_symbol(
    date: str,
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
):
    """One symbol's pick for one session."""
    try:
        _assert_date_visible(date, _premium(current_user))

        service = await _service()
        pick = await service.get_pick_for_symbol(date, symbol, market=market)
        if not pick:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{symbol.upper()} was not picked on {date}",
            )
        return pick
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get pivot pick for {symbol} on {date}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve pivot pick",
        )
