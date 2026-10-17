from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional, Union
import logging
from datetime import datetime, timedelta

from app.models.stock import (
    DailyPicksResponse, PredictionsResponse, StockDataResponse,
    BacktestResult, AvailableDatesResponse, StockListResponse,
    Direction, MatchType, TradingCalendarResponse,
    LatestPredictionsResponse, Prediction
)
from app.services.stock_service import stock_service
from app.services.predictions_service import predictions_service
from app.services.picks_service import picks_service
from app.core.dependencies import get_current_user
from app.core.security import effective_premium_fields, is_premium_user
from app.models.user import UserInDB
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stocks", tags=["Stocks"])


def _access_fields(current_user: UserInDB) -> tuple[str, Optional[datetime]]:
    return effective_premium_fields(
        current_user.role,
        current_user.premium_expires_at,
        current_user.referral_access_expires_at,
    )

@router.get("/dates", response_model=AvailableDatesResponse, summary="Get available dates")
async def get_available_dates(
    current_user: UserInDB = Depends(get_current_user)):
    """Get available dates"""
    try:
        if picks_service.db is None:
            picks_service.initialize()

        is_premium = is_premium_user(
            current_user.role,
            current_user.premium_expires_at,
            current_user.referral_access_expires_at,
        )

        dates = await picks_service.get_available_dates(limit=300, is_premium=is_premium)

        return AvailableDatesResponse(dates=dates)
    except Exception as e:
        logger.error(f"Failed to get available dates: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve available dates"
        )

@router.get("/picks/{date}", response_model=DailyPicksResponse, summary="Get daily picks")
async def get_daily_picks(
    date: str,
    current_user: UserInDB = Depends(get_current_user),
    direction: Optional[Direction] = Query(None, description="Optional direction filter (rising/falling)")
):
    """Get daily picks for specified date and optional direction

    Returns DailyPicksResponse with both rising and falling picks.
    If direction is specified, the other direction will be empty.
    If direction is not specified, both directions will contain their respective picks.
    """
    try:
        user_role, premium_expires_at = _access_fields(current_user)
        return await stock_service.get_daily_picks(
            date=date,
            direction=direction,
            user_role=user_role,
            premium_expires_at=premium_expires_at,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except Exception as e:
        direction_str = direction.value if direction else "all directions"
        logger.error(f"Failed to get daily picks for {date} {direction_str}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve daily picks"
        )


@router.get("/picks/{date}/rising", response_model=DailyPicksResponse, summary="Get rising picks for date")
async def get_rising_picks(
    date: str,
    current_user: UserInDB = Depends(get_current_user)
):
    """Get rising picks for specified date"""
    return await get_daily_picks(date, Direction.RISING, current_user)

@router.get("/picks/{date}/falling", response_model=DailyPicksResponse, summary="Get falling picks for date")
async def get_falling_picks(
    date: str,
    current_user: UserInDB = Depends(get_current_user)
):
    """Get falling picks for specified date"""
    return await get_daily_picks(date, Direction.FALLING, current_user)

@router.get("/latest-picks", response_model=DailyPicksResponse, summary="Get latest picks based on membership")
async def get_latest_picks(
    current_user: UserInDB = Depends(get_current_user)
):
    """Get latest picks based on user membership status

    Premium users get latest trading day picks.
    Free users get picks from 10 days ago.
    """
    try:
        user_role, premium_expires_at = _access_fields(current_user)
        return await stock_service.get_latest_picks(
            user_role=user_role,
            premium_expires_at=premium_expires_at,
        )
    except Exception as e:
        logger.error(f"Failed to get latest picks for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve latest picks"
        )

@router.get("/predictions/{date}", response_model=PredictionsResponse, summary="Get predictions")
async def get_predictions(
    date: str,
    current_user: UserInDB = Depends(get_current_user)
):
    """Get predictions for specified date"""
    try:
        user_role, premium_expires_at = _access_fields(current_user)
        return await stock_service.get_predictions(
            date=date,
            user_role=user_role,
            premium_expires_at=premium_expires_at,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to get predictions for {date}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve predictions"
        )

@router.get("/latest-predictions", response_model=LatestPredictionsResponse, summary="Get latest predictions")
async def get_latest_predictions(
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to get predictions for")
):
    """Get predictions for the latest available date"""
    try:
        # Initialize service if needed
        if predictions_service.db is None:
            await predictions_service.initialize()

        # Check if user has premium access

        is_premium = is_premium_user(
            current_user.role,
            current_user.premium_expires_at,
            current_user.referral_access_expires_at,
        )

        # Get latest predictions
        predictions = await predictions_service.get_latest_predictions(market, is_premium)

        if not predictions:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No predictions found for market: {market}"
            )

        # Get the date from the first prediction
        latest_date = predictions[0].date.strftime('%Y-%m-%d')

        return LatestPredictionsResponse(
            date=latest_date,
            predictions=predictions,
            total_count=len(predictions)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get latest predictions for market {market}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve latest predictions"
        )

@router.get("/predictions/{date}/{instrument}", response_model=Prediction, summary="Get prediction for specific instrument")
async def get_prediction_by_date_and_instrument(
    date: str,
    instrument: str,
    current_user: UserInDB = Depends(get_current_user),
    market: str = Query("us", description="Market to get prediction for")
):
    """Get prediction for a specific date and instrument"""
    try:
        # Initialize service if needed
        if predictions_service.db is None:
            await predictions_service.initialize()

        is_premium = is_premium_user(
            current_user.role,
            current_user.premium_expires_at,
            current_user.referral_access_expires_at,
        )

        # For non-premium users, check if the requested date is within allowed range
        if not is_premium:
            try:
                request_date = datetime.strptime(date, '%Y-%m-%d')
                cutoff_date = datetime.now() - timedelta(days=settings.free_user_days_limit)

                if request_date > cutoff_date:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Free users can only access data older than {settings.free_user_days_limit} days"
                    )
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid date format. Use YYYY-MM-DD"
                )

        # Get prediction for specific date and instrument
        prediction = await predictions_service.get_predictions_by_date_and_instrument(
            date=date,
            instrument=instrument.upper(),
            market=market
        )

        if not prediction:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No prediction found for {instrument} on {date}"
            )

        return prediction
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get prediction for {instrument} on {date}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve prediction"
        )

@router.get("/data/{symbol}", response_model=StockDataResponse, summary="Get stock historical data")
async def get_stock_data(
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
    start_date: Optional[str] = Query(None, description="Start date in YYYY-MM-DD format"),
    end_date: Optional[str] = Query(None, description="End date in YYYY-MM-DD format"),
    period: Optional[str] = Query(None, description="Period for historical data (1m, 3m, 6m, 9m, 1y, 2y, 3y)")
):
    """Get historical data for a specific stock symbol

    Filter parameters (in priority order):
    1. start_date + end_date: Get data within specific date range
    2. period: Get data for specific period (1m, 3m, 6m, 9m, 1y, 2y, 3y)
    3. No filters: Return all available data
    """
    try:
        result = await stock_service.get_stock_data(
            symbol.upper(),
            start_date=start_date,
            end_date=end_date,
            period=period
        )
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Stock data not found for symbol: {symbol}"
            )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get stock data for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve stock data"
        )

@router.get("/symbols", response_model=StockListResponse, summary="Get all available stock symbols")
async def get_all_stocks(
    current_user: UserInDB = Depends(get_current_user),
    limit: Optional[int] = Query(None, ge=1, le=1000, description="Limit number of results"),
    offset: Optional[int] = Query(0, ge=0, description="Offset for pagination")
):
    """Get all available stock symbols"""
    try:
        symbols = await stock_service.get_all_stocks()
        total_count = len(symbols)

        # Apply pagination if requested
        if limit is not None:
            end_index = offset + limit
            symbols = symbols[offset:end_index]
        elif offset > 0:
            symbols = symbols[offset:]

        return StockListResponse(
            symbols=symbols,
            total_count=total_count
        )
    except Exception as e:
        logger.error(f"Failed to get stock symbols: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve stock symbols"
        )

@router.get("/backtest/{symbol}", response_model=BacktestResult, summary="Run backtest analysis")
async def run_backtest(
    symbol: str,
    period_days: int = Query(90, ge=30, le=365, description="Number of days to analyze"),
    current_user: UserInDB = Depends(get_current_user)
):
    """Run backtest analysis for a specific stock symbol"""
    try:
        user_role, premium_expires_at = _access_fields(current_user)
        result = await stock_service.run_backtest(
            symbol=symbol.upper(),
            period_days=period_days,
            user_role=user_role,
            premium_expires_at=premium_expires_at,
        )
        return result
    except Exception as e:
        logger.error(f"Failed to run backtest for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to run backtest analysis"
        )

# Additional endpoints for more granular access

@router.get("/search", response_model=StockListResponse, summary="Search stocks by symbol")
async def search_stocks(
    q: str = Query(..., min_length=1, description="Search query for stock symbols"),
    match_type: MatchType = Query(MatchType.INFIX, description="Match type: prefix or infix"),
    limit: Optional[int] = Query(50, ge=1, le=100, description="Limit number of results"),
    current_user: UserInDB = Depends(get_current_user)
):
    """Search for stocks by symbol with configurable match type"""
    try:
        all_symbols = await stock_service.get_all_stocks()

        # Filter symbols based on match type (case insensitive)
        query = q.upper()
        if match_type == MatchType.PREFIX:
            matching_symbols = [symbol for symbol in all_symbols if symbol.startswith(query)]
        else:  # MatchType.INFIX
            matching_symbols = [symbol for symbol in all_symbols if query in symbol]

        # Apply limit
        if limit:
            matching_symbols = matching_symbols[:limit]

        return StockListResponse(
            symbols=matching_symbols,
            total_count=len(matching_symbols)
        )
    except Exception as e:
        logger.error(f"Failed to search stocks with query '{q}' and match_type '{match_type}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to search stocks"
        )
