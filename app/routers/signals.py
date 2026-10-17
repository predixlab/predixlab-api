"""Combined Pivot and AlphaRank signal endpoints."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.config import settings
from app.core.dependencies import get_current_user
from app.core.security import is_premium_user
from app.models.alpharank import (
    AlphaRankAvailableDatesResponse,
    AlphaRankSignal,
    AlphaRankSignalsResponse,
    AlphaRankTickerHistoryResponse,
)
from app.models.pivot import PickDirection, PickEvent, PivotPicksResponse
from app.models.user import UserInDB
from app.routers.pivot import get_latest_pivot_picks, get_pivot_picks
from app.services.alpharank_service import alpharank_service
from app.services.pivot_service import pivot_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/signals", tags=["Signals"])

MAX_PAGE_SIZE = 200


class SignalsResponse(BaseModel):
    """Combined signal board for a single coherent market session."""

    market: str
    date: str
    pivot: Optional[PivotPicksResponse] = None
    alpharank: Optional[AlphaRankSignalsResponse] = None


async def _alpha_service():
    if alpharank_service.db is None:
        await alpharank_service.initialize()
    return alpharank_service


async def _pivot_service():
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
    if is_premium:
        return
    cutoff = (datetime.now() - timedelta(days=settings.free_user_days_limit)).strftime(
        "%Y-%m-%d"
    )
    if date > cutoff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Signals for {date} require a premium subscription. "
                f"Free accounts can access sessions on or before {cutoff}."
            ),
        )


def _alpha_response(
    market: str, date: str, signals: list[AlphaRankSignal]
) -> AlphaRankSignalsResponse:
    return AlphaRankSignalsResponse(
        market=market,
        date=date,
        horizon=signals[0].horizon if signals else None,
        items=signals,
        total_count=len(signals),
    )


async def _combined_response(
    date: str, market: str, is_premium: bool
) -> SignalsResponse:
    pivot = await _pivot_service()
    alpharank = await _alpha_service()
    picks = await pivot.get_picks_by_date(date, market=market, strict=True)
    counts = await pivot.count_events(date, market, strict=True)
    alpha_signals = await alpharank.get_signals_by_date(
        date, market=market, is_premium=is_premium
    )

    if not picks and not counts and not alpha_signals:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No signals found for market {market} on {date}",
        )

    pivot_response = None
    if picks or counts or alpha_signals:
        longs = [pick for pick in picks if pick.direction == PickDirection.LONG]
        shorts = [pick for pick in picks if pick.direction == PickDirection.SHORT]
        pivot_response = PivotPicksResponse(
            market=market,
            date=date,
            horizon=picks[0].horizon if picks else None,
            longs=longs,
            shorts=shorts,
            total_count=len(picks),
            counts=counts,
        )

    return SignalsResponse(
        market=market,
        date=date,
        pivot=pivot_response,
        alpharank=(
            _alpha_response(market, date, alpha_signals) if alpha_signals else None
        ),
    )


@router.get("/dates", response_model=AlphaRankAvailableDatesResponse)
async def get_signal_dates(
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
    limit: int = Query(60, ge=1, le=800, description="Maximum number of dates"),
):
    """Dates published by either daily signal source, newest first."""

    try:
        is_premium = _premium(current_user)
        pivot = await _pivot_service()
        alpharank = await _alpha_service()
        pivot_dates = await pivot.get_available_dates(
            market=market, limit=limit, is_premium=is_premium
        )
        alpha_dates = await alpharank.get_available_dates(
            market=market, limit=limit, is_premium=is_premium
        )
        dates = sorted(set(pivot_dates) | set(alpha_dates), reverse=True)[:limit]
        return AlphaRankAvailableDatesResponse(
            market=market, dates=dates, count=len(dates)
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get signal dates for %s: %s", market, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve signal dates",
        )


@router.get("/pivot", response_model=PivotPicksResponse)
async def get_signals_pivot(
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
    direction: Optional[PickDirection] = Query(None, description="Restrict to one side"),
    event: Optional[PickEvent] = Query(None, description="Restrict to one event type"),
    limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
):
    """Alias for the current latest Pivot picks behavior."""

    return await get_latest_pivot_picks(current_user, market, direction, event, limit)


@router.get("/pivot/{date}", response_model=PivotPicksResponse)
async def get_signals_pivot_by_date(
    date: str,
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
    direction: Optional[PickDirection] = Query(None, description="Restrict to one side"),
    event: Optional[PickEvent] = Query(None, description="Restrict to one event type"),
    limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
):
    """Alias for the current dated Pivot picks behavior."""

    return await get_pivot_picks(
        date, current_user, market, direction, event, limit
    )


@router.get("/alpharank", response_model=AlphaRankSignalsResponse)
async def get_latest_alpharank_signals(
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
):
    """Latest AlphaRank top-15 visible to the caller."""

    try:
        service = await _alpha_service()
        date = await service.get_latest_date(market, _premium(current_user))
        if not date:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No AlphaRank signals found for market: {market}",
            )
        signals = await service.get_signals_by_date(
            date, market, _premium(current_user)
        )
        return _alpha_response(market, date, signals)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get latest AlphaRank signals for %s: %s", market, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve AlphaRank signals",
        )


@router.get(
    "/alpharank/tickers/{ticker}/history",
    response_model=AlphaRankTickerHistoryResponse,
)
async def get_alpharank_ticker_history(
    ticker: str,
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
    limit: int = Query(120, ge=1, le=750, description="Maximum publications"),
):
    """Committed AlphaRank history for one ticker, oldest first."""

    try:
        service = await _alpha_service()
        items = await service.get_ticker_history(
            ticker,
            market=market,
            limit=limit,
            is_premium=_premium(current_user),
        )
        return AlphaRankTickerHistoryResponse(
            market=market,
            symbol=ticker.upper(),
            count=len(items),
            items=items,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Failed to get AlphaRank history for %s in %s: %s", ticker, market, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve AlphaRank history",
        )


@router.get("/alpharank/{date}", response_model=AlphaRankSignalsResponse)
async def get_alpharank_signals_by_date(
    date: str,
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
):
    """AlphaRank top-15 for a session; an empty list is a valid dated result."""

    try:
        is_premium = _premium(current_user)
        _assert_date_visible(date, is_premium)
        service = await _alpha_service()
        signals = await service.get_signals_by_date(date, market, is_premium)
        return _alpha_response(market, date, signals)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Failed to get AlphaRank signals for %s on %s: %s", market, date, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve AlphaRank signals",
        )


@router.get("", response_model=SignalsResponse)
async def get_latest_signals(
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
):
    """Latest coherent combined signal board visible to the caller."""

    try:
        is_premium = _premium(current_user)
        pivot = await _pivot_service()
        alpharank = await _alpha_service()
        dates = [
            date
            for date in (
                await pivot.get_latest_date(market, is_premium),
                await alpharank.get_latest_date(market, is_premium),
            )
            if date
        ]
        if not dates:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No signals found for market: {market}",
            )
        return await _combined_response(max(dates), market, is_premium)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get latest signals for %s: %s", market, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve signals",
        )


@router.get("/{date}", response_model=SignalsResponse)
async def get_signals_by_date(
    date: str,
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to query"),
):
    """Combined signal board for a specific session."""

    try:
        is_premium = _premium(current_user)
        _assert_date_visible(date, is_premium)
        return await _combined_response(date, market, is_premium)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get signals for %s on %s: %s", market, date, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve signals",
        )
