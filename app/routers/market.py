"""Market-wide intelligence endpoints."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_current_user
from app.models.market import (
    HeatmapResponse,
    MarketBreadth,
    MarketOverviewResponse,
    MarketSentiment,
    MoverCategory,
    MoversResponse,
    PerformanceDistributionResponse,
    SymbolQuote,
)
from app.models.user import UserInDB
from app.services.analysis_service import analysis_service
from app.services.market_service import MARKET_RESULT_LIMIT, market_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["Market"])


@router.get(
    "/snapshot",
    response_model=Dict[str, Any],
    summary="Get the complete daily market summary",
)
async def get_market_snapshot(current_user: UserInDB = Depends(get_current_user)):
    """Return the full JSON summary so the client can filter it locally."""
    await market_service.refresh()
    return market_service.get_snapshot()


@router.get("/overview", response_model=MarketOverviewResponse, summary="Market overview")
async def get_market_overview(
    current_user: UserInDB = Depends(get_current_user),
    movers_limit: int = Query(
        5,
        ge=1,
        le=MARKET_RESULT_LIMIT,
        description="Number of movers per ranking",
    ),
):
    """Breadth, sentiment and the leading movers in a single call."""
    try:
        return await market_service.get_overview(movers_limit=movers_limit)
    except Exception as exc:
        logger.error(f"Failed to build market overview: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build market overview",
        )


@router.get("/breadth", response_model=MarketBreadth, summary="Market breadth")
async def get_market_breadth(current_user: UserInDB = Depends(get_current_user)):
    """Advance/decline statistics and moving-average participation."""
    try:
        return await market_service.get_breadth()
    except Exception as exc:
        logger.error(f"Failed to compute market breadth: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute market breadth",
        )


@router.get("/sentiment", response_model=MarketSentiment, summary="Market sentiment gauge")
async def get_market_sentiment(current_user: UserInDB = Depends(get_current_user)):
    """A 0-100 risk-appetite gauge derived from market breadth."""
    try:
        return await market_service.get_sentiment()
    except Exception as exc:
        logger.error(f"Failed to compute market sentiment: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute market sentiment",
        )


@router.get("/movers", response_model=MoversResponse, summary="Market movers")
async def get_market_movers(
    current_user: UserInDB = Depends(get_current_user),
    category: MoverCategory = Query(MoverCategory.GAINERS, description="Ranking to return"),
    limit: int = Query(
        MARKET_RESULT_LIMIT,
        ge=1,
        le=MARKET_RESULT_LIMIT,
        description="Number of entries",
    ),
):
    """Top gainers, losers, most active, unusual volume, gaps and 52-week extremes."""
    try:
        return await market_service.get_movers(category=category, limit=limit)
    except Exception as exc:
        logger.error(f"Failed to compute movers for {category}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute market movers",
        )


@router.get(
    "/performance-distribution",
    response_model=PerformanceDistributionResponse,
    summary="Return distribution across the universe",
)
async def get_performance_distribution(
    current_user: UserInDB = Depends(get_current_user),
    window_days: int = Query(
        1,
        ge=1,
        description="Supported trading-day lookback: 1, 7, or 30",
    ),
):
    """Histogram showing how returns are spread across the universe."""
    if window_days not in {1, 7, 30}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="window_days must be one of: 1, 7, 30",
        )
    try:
        return await market_service.get_performance_distribution(window_days=window_days)
    except Exception as exc:
        logger.error(f"Failed to compute performance distribution: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute performance distribution",
        )


@router.get("/heatmap", response_model=HeatmapResponse, summary="Market heatmap data")
async def get_market_heatmap(
    current_user: UserInDB = Depends(get_current_user),
    window_days: int = Query(1, ge=1, le=252, description="Lookback in trading days"),
    limit: int = Query(
        MARKET_RESULT_LIMIT,
        ge=1,
        le=MARKET_RESULT_LIMIT,
        description="Maximum number of tiles",
    ),
):
    """Per-symbol returns sized by volume, ready for a treemap widget."""
    try:
        return await market_service.get_heatmap(window_days=window_days, limit=limit)
    except Exception as exc:
        logger.error(f"Failed to build heatmap: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build market heatmap",
        )


@router.get("/quotes", response_model=List[SymbolQuote], summary="Batch latest quotes")
async def get_quotes(
    current_user: UserInDB = Depends(get_current_user),
    symbols: str = Query(..., description="Comma separated symbols, e.g. AAPL,MSFT,NVDA"),
):
    """Latest session snapshot for up to 50 symbols."""
    requested = _parse_symbols(symbols, limit=50)
    if not requested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one symbol is required",
        )
    try:
        return await analysis_service.get_quotes(requested)
    except Exception as exc:
        logger.error(f"Failed to fetch quotes for {requested}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch quotes",
        )


def _parse_symbols(raw: Optional[str], limit: int = 50) -> List[str]:
    """Split a comma separated symbol list into a normalised, capped list."""
    if not raw:
        return []
    seen = set()
    result: List[str] = []
    for token in raw.split(","):
        symbol = token.strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
        if len(result) >= limit:
            break
    return result
