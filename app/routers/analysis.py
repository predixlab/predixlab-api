"""Per-symbol analysis endpoints."""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_current_user
from app.core.security import is_premium_user
from app.models.market import (
    CorrelationResponse,
    IndicatorSeriesResponse,
    IndicatorSnapshot,
    PeerComparisonResponse,
    RiskMetrics,
    SeasonalityResponse,
    SymbolProfileResponse,
    TechnicalAnalysisResponse,
)
from app.models.user import UserInDB
from app.routers.market import _parse_symbols
from app.services.analysis_service import analysis_service
from app.services.predictions_service import predictions_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["Analysis"])


@router.get(
    "/{symbol}/technical",
    response_model=TechnicalAnalysisResponse,
    summary="Technical analysis with plain-language interpretation",
)
async def get_technical_analysis(
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Trend, momentum, key levels and an explainable narrative for a symbol."""
    result = await analysis_service.get_technical_analysis(symbol.upper())
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No price history available for {symbol.upper()}",
        )
    return result


@router.get("/{symbol}/indicators", response_model=IndicatorSnapshot, summary="Latest indicator values")
async def get_indicator_snapshot(
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """The most recent value of every supported technical indicator."""
    result = await analysis_service.get_indicator_snapshot(symbol.upper())
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No price history available for {symbol.upper()}",
        )
    return result


@router.get(
    "/{symbol}/indicators/series",
    response_model=IndicatorSeriesResponse,
    summary="Indicator time series for charting",
)
async def get_indicator_series(
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
    names: Optional[str] = Query(
        None, description="Comma separated indicator names, e.g. sma_20,sma_50,rsi_14"
    ),
    limit: int = Query(250, ge=10, le=1000, description="Number of most recent points"),
):
    """Indicator values per date, ready to overlay on a price chart."""
    requested = _parse_symbols(names, limit=12) if names else []
    result = await analysis_service.get_indicator_series(
        symbol.upper(), [n.lower() for n in requested], limit=limit
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No price history available for {symbol.upper()}",
        )
    return result


@router.get("/{symbol}/risk", response_model=RiskMetrics, summary="Risk and performance metrics")
async def get_risk_metrics(
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
    window_days: int = Query(252, ge=30, le=2520, description="Lookback in trading days"),
    benchmark: Optional[str] = Query(None, description="Benchmark symbol for beta/alpha"),
):
    """Volatility, Sharpe, Sortino, drawdown, VaR and benchmark-relative metrics."""
    result = await analysis_service.get_risk_metrics(
        symbol.upper(), window_days=window_days, benchmark=benchmark.upper() if benchmark else None
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No price history available for {symbol.upper()}",
        )
    return result


@router.get("/{symbol}/peers", response_model=PeerComparisonResponse, summary="Peer comparison")
async def compare_peers(
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
    peers: str = Query(..., description="Comma separated peer symbols"),
    window_days: int = Query(252, ge=30, le=2520, description="Lookback in trading days"),
):
    """Return, risk and correlation of a symbol against a chosen peer group."""
    peer_list = _parse_symbols(peers, limit=20)
    if not peer_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one peer symbol is required",
        )

    result = await analysis_service.compare_peers(symbol.upper(), peer_list, window_days=window_days)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No price history available for {symbol.upper()}",
        )
    return result


@router.get("/{symbol}/seasonality", response_model=SeasonalityResponse, summary="Seasonality statistics")
async def get_seasonality(
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
    granularity: str = Query("month", pattern="^(month|weekday)$", description="month or weekday"),
):
    """Historical average return by calendar month or weekday."""
    result = await analysis_service.get_seasonality(symbol.upper(), granularity=granularity)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Not enough price history for {symbol.upper()}",
        )
    return result


@router.get("/{symbol}/profile", response_model=SymbolProfileResponse, summary="Complete symbol profile")
async def get_symbol_profile(
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
    benchmark: Optional[str] = Query(None, description="Benchmark symbol for beta/alpha"),
    include_prediction: bool = Query(True, description="Attach the latest model prediction when available"),
):
    """Quote, technicals, risk, key levels and the model prediction in one call."""
    prediction_payload = None
    if include_prediction:
        prediction_payload = await _load_prediction(symbol.upper(), current_user)

    result = await analysis_service.get_symbol_profile(
        symbol.upper(),
        benchmark=benchmark.upper() if benchmark else None,
        prediction=prediction_payload,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No price history available for {symbol.upper()}",
        )
    return result


@router.get("/correlation", response_model=CorrelationResponse, summary="Correlation matrix")
async def get_correlation_matrix(
    current_user: UserInDB = Depends(get_current_user),
    symbols: str = Query(..., description="Comma separated symbols, at least two"),
    window_days: int = Query(252, ge=30, le=2520, description="Lookback in trading days"),
):
    """Pairwise return correlation for a basket of symbols."""
    symbol_list = _parse_symbols(symbols, limit=30)
    if len(symbol_list) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least two symbols are required",
        )
    return await analysis_service.get_correlation_matrix(symbol_list, window_days=window_days)


async def _load_prediction(symbol: str, current_user: UserInDB) -> Optional[dict]:
    """Best-effort lookup of the latest model prediction for a symbol."""
    try:
        if predictions_service.db is None:
            await predictions_service.initialize()

        is_premium = is_premium_user(
            current_user.role,
            current_user.premium_expires_at,
            current_user.referral_access_expires_at,
        )
        predictions = await predictions_service.get_latest_predictions("us", is_premium)
        for prediction in predictions or []:
            if prediction.instrument.upper() == symbol:
                return prediction.model_dump()
    except Exception as exc:
        logger.debug(f"Prediction lookup failed for {symbol}: {exc}")
    return None
