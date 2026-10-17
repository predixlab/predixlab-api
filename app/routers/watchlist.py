"""Watchlist and alert endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_current_user
from app.models.market import (
    Alert,
    AlertCheckResponse,
    AlertCreateRequest,
    AlertListResponse,
    Watchlist,
    WatchlistCreateRequest,
    WatchlistListResponse,
    WatchlistQuotesResponse,
    WatchlistSymbolRequest,
)
from app.models.user import UserInDB
from app.services.watchlist_service import watchlist_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlist", tags=["Watchlist"])


@router.get("", response_model=WatchlistListResponse, summary="List watchlists")
async def list_watchlists(current_user: UserInDB = Depends(get_current_user)):
    """Every watchlist owned by the current user."""
    try:
        watchlists = await watchlist_service.list_watchlists(current_user.id)
        return WatchlistListResponse(watchlists=watchlists, total_count=len(watchlists))
    except Exception as exc:
        logger.error(f"Failed to list watchlists for {current_user.id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list watchlists",
        )


@router.post("", response_model=Watchlist, status_code=status.HTTP_201_CREATED, summary="Create a watchlist")
async def create_watchlist(
    request: WatchlistCreateRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Create a named watchlist, optionally pre-filled with symbols."""
    try:
        return await watchlist_service.create_watchlist(
            current_user.id, request.name, request.symbols
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to create watchlist for {current_user.id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create watchlist",
        )


@router.get("/{watchlist_id}", response_model=Watchlist, summary="Get a watchlist")
async def get_watchlist(
    watchlist_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """A single watchlist owned by the current user."""
    watchlist = await watchlist_service.get_watchlist(current_user.id, watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist not found")
    return watchlist


@router.delete("/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a watchlist")
async def delete_watchlist(
    watchlist_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Permanently remove a watchlist."""
    deleted = await watchlist_service.delete_watchlist(current_user.id, watchlist_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist not found")


@router.post("/{watchlist_id}/symbols", response_model=Watchlist, summary="Add a symbol")
async def add_symbol(
    watchlist_id: str,
    request: WatchlistSymbolRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Add a symbol to a watchlist. Adding an existing symbol is a no-op."""
    try:
        watchlist = await watchlist_service.add_symbol(
            current_user.id, watchlist_id, request.symbol, request.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if watchlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist not found")
    return watchlist


@router.delete("/{watchlist_id}/symbols/{symbol}", response_model=Watchlist, summary="Remove a symbol")
async def remove_symbol(
    watchlist_id: str,
    symbol: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Remove a symbol from a watchlist."""
    watchlist = await watchlist_service.remove_symbol(current_user.id, watchlist_id, symbol)
    if watchlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist not found")
    return watchlist


@router.get("/{watchlist_id}/quotes", response_model=WatchlistQuotesResponse, summary="Watchlist quotes")
async def get_watchlist_quotes(
    watchlist_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Latest session snapshot for every symbol in a watchlist."""
    result = await watchlist_service.get_watchlist_quotes(current_user.id, watchlist_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist not found")

    watchlist, quotes = result
    return WatchlistQuotesResponse(
        watchlist_id=watchlist.id,
        name=watchlist.name,
        quotes=quotes,
        total_count=len(quotes),
    )


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

alerts_router = APIRouter(prefix="/alerts", tags=["Alerts"])


@alerts_router.get("", response_model=AlertListResponse, summary="List alerts")
async def list_alerts(
    current_user: UserInDB = Depends(get_current_user),
    active_only: bool = Query(False, description="Only return alerts that are still armed"),
):
    """Every alert configured by the current user."""
    alerts = await watchlist_service.list_alerts(current_user.id, active_only=active_only)
    return AlertListResponse(alerts=alerts, total_count=len(alerts))


@alerts_router.post("", response_model=Alert, status_code=status.HTTP_201_CREATED, summary="Create an alert")
async def create_alert(
    request: AlertCreateRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Arm a price, percentage, RSI or volume condition on a symbol."""
    try:
        return await watchlist_service.create_alert(current_user.id, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to create alert for {current_user.id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create alert",
        )


@alerts_router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an alert")
async def delete_alert(
    alert_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Remove an alert."""
    deleted = await watchlist_service.delete_alert(current_user.id, alert_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")


@alerts_router.post("/check", response_model=AlertCheckResponse, summary="Evaluate alerts now")
async def check_alerts(current_user: UserInDB = Depends(get_current_user)):
    """Evaluate every armed alert against the latest available data."""
    try:
        return await watchlist_service.check_alerts(current_user.id)
    except Exception as exc:
        logger.error(f"Failed to check alerts for {current_user.id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check alerts",
        )
