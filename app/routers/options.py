from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional, Dict, Any
import logging

from app.services.options_service import MAX_OPTIONS_ACTIVITY_TICKERS, options_service
from app.core.dependencies import get_current_user
from app.core.security import is_premium_user
from app.models.user import UserInDB
from app.models.options import (
    AnomalyOption,
    GexAnalysis,
    GexAvailableDatesResponse,
    GexAvailableSymbolsResponse,
    GexHighlightSignal,
    GexHighlightsResponse,
    OptionsAnomalyDatesResponse,
    OptionsStructureAnalysis,
    OptionsStructureHighlightSignal,
    OptionsStructureHighlightsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/options", tags=["Options"])


def _premium_access(user: UserInDB) -> bool:
    return is_premium_user(
        user.role,
        user.premium_expires_at,
        user.referral_access_expires_at,
    )


@router.get(
    "/structure/highlights",
    summary="Get noteworthy volatility and positioning symbols",
    response_model=OptionsStructureHighlightsResponse,
)
async def get_options_structure_highlights(
    limit: int = Query(6, ge=1, le=12),
    signal: Optional[OptionsStructureHighlightSignal] = Query(None),
    current_user: UserInDB = Depends(get_current_user),
) -> OptionsStructureHighlightsResponse:
    if options_service.db is None:
        await options_service.initialize()
    return await options_service.get_options_structure_highlights(
        limit=limit,
        signal=signal,
        is_premium=_premium_access(current_user),
    )


@router.get(
    "/structure/latest/{symbol}",
    summary="Get latest implied-volatility and positioning structure",
    response_model=OptionsStructureAnalysis,
)
async def get_latest_options_structure(
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
) -> OptionsStructureAnalysis:
    if options_service.db is None:
        await options_service.initialize()
    result = await options_service.get_latest_options_structure(
        symbol=symbol,
        is_premium=_premium_access(current_user),
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No options structure analysis is available for this symbol",
        )
    return result


@router.get(
    "/structure-symbols",
    summary="Get symbols with options structure data",
    response_model=GexAvailableSymbolsResponse,
)
async def get_options_structure_symbols(
    query: str = Query("", max_length=20),
    limit: int = Query(1000, ge=1, le=2000),
    current_user: UserInDB = Depends(get_current_user),
) -> GexAvailableSymbolsResponse:
    if options_service.db is None:
        await options_service.initialize()
    symbols = await options_service.get_options_structure_symbols(
        query=query,
        limit=limit,
        is_premium=_premium_access(current_user),
    )
    return GexAvailableSymbolsResponse(symbols=symbols, count=len(symbols))


@router.get(
    "/gex/highlights",
    summary="Get noteworthy gamma exposure symbols",
    response_model=GexHighlightsResponse,
)
async def get_gex_highlights(
    limit: int = Query(6, ge=1, le=12),
    signal: Optional[GexHighlightSignal] = Query(None),
    current_user: UserInDB = Depends(get_current_user),
) -> GexHighlightsResponse:
    if options_service.db is None:
        await options_service.initialize()
    return await options_service.get_gex_highlights(
        limit=limit,
        signal=signal,
        is_premium=_premium_access(current_user),
    )


@router.get(
    "/gex/latest/{symbol}",
    summary="Get latest gamma exposure analysis",
    response_model=GexAnalysis,
)
async def get_latest_gex(
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
) -> GexAnalysis:
    if options_service.db is None:
        await options_service.initialize()
    result = await options_service.get_latest_gex(
        symbol=symbol,
        is_premium=_premium_access(current_user),
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No gamma exposure analysis is available for this symbol",
        )
    return result


@router.get(
    "/gex/dates/{symbol}",
    summary="Get available gamma exposure dates",
    response_model=GexAvailableDatesResponse,
)
async def get_gex_dates(
    symbol: str,
    limit: int = Query(60, ge=1, le=800),
    current_user: UserInDB = Depends(get_current_user),
) -> GexAvailableDatesResponse:
    if options_service.db is None:
        await options_service.initialize()
    normalized_symbol = symbol.upper()
    dates = await options_service.get_gex_dates(
        symbol=normalized_symbol,
        limit=limit,
        is_premium=_premium_access(current_user),
    )
    return GexAvailableDatesResponse(
        symbol=normalized_symbol,
        dates=dates,
        count=len(dates),
    )


@router.get(
    "/gex-symbols",
    summary="Get symbols with gamma exposure data",
    response_model=GexAvailableSymbolsResponse,
)
async def get_gex_symbols(
    query: str = Query("", max_length=20),
    limit: int = Query(1000, ge=1, le=2000),
    current_user: UserInDB = Depends(get_current_user),
) -> GexAvailableSymbolsResponse:
    if options_service.db is None:
        await options_service.initialize()
    symbols = await options_service.get_gex_symbols(
        query=query,
        limit=limit,
        is_premium=_premium_access(current_user),
    )
    return GexAvailableSymbolsResponse(symbols=symbols, count=len(symbols))


@router.get(
    "/gex/{date}/{symbol}",
    summary="Get gamma exposure analysis for a trading date",
    response_model=GexAnalysis,
)
async def get_gex_by_date(
    date: str,
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
) -> GexAnalysis:
    if options_service.db is None:
        await options_service.initialize()
    result = await options_service.get_gex(
        symbol=symbol,
        date=date,
        is_premium=_premium_access(current_user),
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No gamma exposure analysis is available for this symbol and date",
        )
    return result

@router.get("/anomalies", summary="Get anomalous option trades", response_model=List[AnomalyOption])
async def get_option_anomalies(
    offset: int = Query(0, ge=0),
    limit: int = Query(MAX_OPTIONS_ACTIVITY_TICKERS, ge=1, le=MAX_OPTIONS_ACTIVITY_TICKERS),
    current_user: UserInDB = Depends(get_current_user)
) -> List[AnomalyOption]:
    """Return anomalous option trades from the database grouped by symbol"""
    try:
        # Ensure service is initialized
        if options_service.db is None:
            await options_service.initialize()

        # Check if user has premium access
        is_premium = is_premium_user(
            current_user.role,
            current_user.premium_expires_at,
            current_user.referral_access_expires_at,
        )

        # Fetch anomalies
        return await options_service.get_anomalies(limit=limit, offset=offset, is_premium=is_premium)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch option anomalies (offset={offset}, limit={limit}): {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve option anomalies"
        )

@router.get("/anomalies/latest", summary="Get anomalous option trades", response_model=List[AnomalyOption])
async def get_latest_option_anomalies(
    current_user: UserInDB = Depends(get_current_user)
) -> List[AnomalyOption]:
    """Return anomalous option trades from the database grouped by symbol"""
    try:
        # Ensure service is initialized
        if options_service.db is None:
            await options_service.initialize()

        # Check if user has premium access
        is_premium = is_premium_user(
            current_user.role,
            current_user.premium_expires_at,
            current_user.referral_access_expires_at,
        )

        # Fetch latest anomalies
        return await options_service.get_latest_anomalies(
            is_premium=is_premium,
            limit=MAX_OPTIONS_ACTIVITY_TICKERS,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch latest option anomalies: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve option anomalies"
        )

@router.get(
    "/anomalies/dates",
    summary="Get available option activity dates",
    response_model=OptionsAnomalyDatesResponse,
)
async def get_option_anomaly_dates(
    limit: int = Query(60, ge=1, le=800),
    current_user: UserInDB = Depends(get_current_user),
) -> OptionsAnomalyDatesResponse:
    """Return dates that actually exist in the anomaly collection."""
    if options_service.db is None:
        await options_service.initialize()
    dates = await options_service.get_anomaly_dates(
        limit=limit,
        is_premium=_premium_access(current_user),
    )
    return OptionsAnomalyDatesResponse(dates=dates, count=len(dates))


@router.get("/anomalies/{date}", summary="Get anomalous option trades for a specific date", response_model=List[AnomalyOption])
async def get_option_anomalies_by_date(
    date: str,
    current_user: UserInDB = Depends(get_current_user)
) -> List[AnomalyOption]:
    """Return anomalous option trades from the database for a specific date"""
    try:
        # Ensure service is initialized
        if options_service.db is None:
            await options_service.initialize()

        results = await options_service.get_anomalies_by_date(
            date=date,
            limit=MAX_OPTIONS_ACTIVITY_TICKERS,
            is_premium=is_premium_user(
                current_user.role,
                current_user.premium_expires_at,
                current_user.referral_access_expires_at,
            ),
        )
        return results
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch option anomalies for date {date}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve option anomalies"
        )
