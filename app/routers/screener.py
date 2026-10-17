"""Stock screener endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_current_user
from app.models.market import (
    ScreenerPresetsResponse,
    ScreenerRequest,
    ScreenerResponse,
    ScreenerStrategy,
    ScreenerStrategyCreateRequest,
    ScreenerStrategyListResponse,
    ScreenerStrategyUpdateRequest,
    StockIndex,
)
from app.models.user import UserInDB
from app.services.screener_service import screener_service
from app.services.screener_strategy_service import screener_strategy_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/screener", tags=["Screener"])


@router.post("/run", response_model=ScreenerResponse, summary="Run a custom screen")
async def run_screen(
    request: ScreenerRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Filter the universe by price, momentum, trend, volatility and volume criteria."""
    try:
        return await screener_service.screen(request)
    except Exception as exc:
        logger.error(f"Screener run failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to run screener",
        )


@router.get("/presets", response_model=ScreenerPresetsResponse, summary="List preset strategies")
async def list_presets(current_user: UserInDB = Depends(get_current_user)):
    """Ready-made screening strategies users can run without any configuration."""
    return ScreenerPresetsResponse(presets=screener_service.get_presets())


@router.get("/presets/{preset_id}", response_model=ScreenerResponse, summary="Run a preset strategy")
async def run_preset(
    preset_id: str,
    current_user: UserInDB = Depends(get_current_user),
    index: StockIndex = Query(
        StockIndex.ALL, description="Index universe to run the preset over"
    ),
    limit: int = Query(50, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
):
    """Execute one of the built-in screening strategies.

    ``index`` is a parameter rather than part of the preset because the two are
    independent: every preset describes a *shape* of setup, and any of them is
    a reasonable question to ask of either index.
    """
    preset = next((p for p in screener_service.get_presets() if p.id == preset_id), None)
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown preset: {preset_id}",
        )

    filters = preset.filters.model_copy(update={"index": index})
    request = ScreenerRequest(
        filters=filters,
        sort_by=preset.sort_by,
        sort_direction=preset.sort_direction,
        limit=limit,
        offset=offset,
    )
    try:
        return await screener_service.screen(request)
    except Exception as exc:
        logger.error(f"Preset screen '{preset_id}' failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to run preset screener",
        )


# ---------------------------------------------------------------------------
# Saved strategies
# ---------------------------------------------------------------------------

@router.get(
    "/strategies",
    response_model=ScreenerStrategyListResponse,
    summary="List saved strategies",
)
async def list_strategies(current_user: UserInDB = Depends(get_current_user)):
    """Every screen the current user has saved."""
    try:
        strategies = await screener_strategy_service.list_strategies(current_user.id)
        return ScreenerStrategyListResponse(
            strategies=strategies, total_count=len(strategies)
        )
    except Exception as exc:
        logger.error(f"Failed to list strategies for {current_user.id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list strategies",
        )


@router.post(
    "/strategies",
    response_model=ScreenerStrategy,
    status_code=status.HTTP_201_CREATED,
    summary="Save a strategy",
)
async def create_strategy(
    request: ScreenerStrategyCreateRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Keep the current criteria under a name."""
    try:
        return await screener_strategy_service.create_strategy(current_user.id, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to create strategy for {current_user.id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create strategy",
        )


@router.patch(
    "/strategies/{strategy_id}",
    response_model=ScreenerStrategy,
    summary="Rename or re-scope a strategy",
)
async def update_strategy(
    strategy_id: str,
    request: ScreenerStrategyUpdateRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Patch a saved strategy; omitted fields are left alone."""
    try:
        strategy = await screener_strategy_service.update_strategy(
            current_user.id, strategy_id, request
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to update strategy {strategy_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update strategy",
        )

    if strategy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found"
        )
    return strategy


@router.delete(
    "/strategies/{strategy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a strategy",
)
async def delete_strategy(
    strategy_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Remove a saved strategy."""
    try:
        deleted = await screener_strategy_service.delete_strategy(
            current_user.id, strategy_id
        )
    except Exception as exc:
        logger.error(f"Failed to delete strategy {strategy_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete strategy",
        )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found"
        )


@router.get(
    "/strategies/{strategy_id}/run",
    response_model=ScreenerResponse,
    summary="Run a saved strategy",
)
async def run_strategy(
    strategy_id: str,
    current_user: UserInDB = Depends(get_current_user),
    index: Optional[StockIndex] = Query(
        None, description="Override the universe the strategy was saved with"
    ),
    limit: int = Query(50, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
):
    """Execute a saved strategy.

    ``index`` is an override rather than a replacement: a strategy saved against
    the NASDAQ-100 keeps that scope unless the caller asks for another, which is
    what lets the universe switch stay live while a saved screen is selected.
    """
    strategy = await screener_strategy_service.get_strategy(current_user.id, strategy_id)
    if strategy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found"
        )

    filters = strategy.filters
    if index is not None:
        filters = filters.model_copy(update={"index": index})

    request = ScreenerRequest(
        filters=filters,
        sort_by=strategy.sort_by,
        sort_direction=strategy.sort_direction,
        limit=limit,
        offset=offset,
    )
    try:
        return await screener_service.screen(request)
    except Exception as exc:
        logger.error(f"Strategy screen '{strategy_id}' failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to run strategy",
        )
