"""User watchlists and price/technical alerts.

Gives users a reason to come back: track the symbols they care about and get
notified when a condition they defined is met. Persisted in MongoDB alongside
the rest of the user data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.database import AsyncDatabase

from app.database import get_database
from app.models.market import (
    Alert,
    AlertCheckResponse,
    AlertCreateRequest,
    AlertType,
    TriggeredAlert,
    Watchlist,
    WatchlistItem,
)
from app.services.analysis_service import analysis_service

logger = logging.getLogger(__name__)

MAX_WATCHLISTS_PER_USER = 20
MAX_SYMBOLS_PER_WATCHLIST = 100
MAX_ALERTS_PER_USER = 100


class WatchlistService:
    """CRUD for watchlists plus evaluation of user-defined alerts."""

    def __init__(self, analysis=None):
        self.db: Optional[AsyncDatabase] = None
        self.analysis = analysis or analysis_service

    async def initialize(self) -> None:
        if self.db is None:
            self.db = await get_database()
        await self._ensure_indexes()

    async def _ensure_indexes(self) -> None:
        if self.db is None:
            return
        try:
            await self.db.watchlists.create_index([("user_id", 1), ("name", 1)], unique=True)
            await self.db.alerts.create_index([("user_id", 1), ("is_active", 1)])
            await self.db.alerts.create_index("symbol")
        except Exception as exc:  # pragma: no cover - index creation is best effort
            logger.warning(f"Failed to create watchlist indexes: {exc}")

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _to_watchlist(doc: Dict[str, Any]) -> Watchlist:
        return Watchlist(
            id=str(doc["_id"]),
            user_id=doc["user_id"],
            name=doc.get("name", "Default"),
            items=[WatchlistItem(**item) for item in doc.get("items", [])],
            created_at=doc.get("created_at", datetime.now(timezone.utc)),
            updated_at=doc.get("updated_at", datetime.now(timezone.utc)),
        )

    @staticmethod
    def _to_alert(doc: Dict[str, Any]) -> Alert:
        return Alert(
            id=str(doc["_id"]),
            user_id=doc["user_id"],
            symbol=doc["symbol"],
            alert_type=doc["alert_type"],
            threshold=doc.get("threshold"),
            is_active=doc.get("is_active", True),
            triggered_at=doc.get("triggered_at"),
            last_checked_at=doc.get("last_checked_at"),
            note=doc.get("note"),
            created_at=doc.get("created_at", datetime.now(timezone.utc)),
        )

    @staticmethod
    def _object_id(value: str) -> Optional[ObjectId]:
        try:
            return ObjectId(value)
        except (InvalidId, TypeError):
            return None

    # -- watchlists -------------------------------------------------------

    async def list_watchlists(self, user_id: str) -> List[Watchlist]:
        cursor = self.db.watchlists.find({"user_id": user_id}).sort("created_at", 1)
        docs = await cursor.to_list(length=MAX_WATCHLISTS_PER_USER)
        return [self._to_watchlist(doc) for doc in docs]

    async def create_watchlist(self, user_id: str, name: str, symbols: List[str]) -> Watchlist:
        existing = await self.db.watchlists.count_documents({"user_id": user_id})
        if existing >= MAX_WATCHLISTS_PER_USER:
            raise ValueError(f"A user may hold at most {MAX_WATCHLISTS_PER_USER} watchlists")

        duplicate = await self.db.watchlists.find_one({"user_id": user_id, "name": name})
        if duplicate:
            raise ValueError(f"Watchlist '{name}' already exists")

        now = datetime.now(timezone.utc)
        unique_symbols = _dedupe_symbols(symbols)[:MAX_SYMBOLS_PER_WATCHLIST]
        doc = {
            "user_id": user_id,
            "name": name,
            "items": [{"symbol": s, "note": None, "added_at": now} for s in unique_symbols],
            "created_at": now,
            "updated_at": now,
        }
        result = await self.db.watchlists.insert_one(doc)
        doc["_id"] = result.inserted_id
        return self._to_watchlist(doc)

    async def get_watchlist(self, user_id: str, watchlist_id: str) -> Optional[Watchlist]:
        oid = self._object_id(watchlist_id)
        if oid is None:
            return None
        doc = await self.db.watchlists.find_one({"_id": oid, "user_id": user_id})
        return self._to_watchlist(doc) if doc else None

    async def delete_watchlist(self, user_id: str, watchlist_id: str) -> bool:
        oid = self._object_id(watchlist_id)
        if oid is None:
            return False
        result = await self.db.watchlists.delete_one({"_id": oid, "user_id": user_id})
        return result.deleted_count > 0

    async def add_symbol(
        self,
        user_id: str,
        watchlist_id: str,
        symbol: str,
        note: Optional[str] = None,
    ) -> Optional[Watchlist]:
        oid = self._object_id(watchlist_id)
        if oid is None:
            return None

        normalized = symbol.strip().upper()
        doc = await self.db.watchlists.find_one({"_id": oid, "user_id": user_id})
        if not doc:
            return None

        items = doc.get("items", [])
        if any(item["symbol"] == normalized for item in items):
            return self._to_watchlist(doc)
        if len(items) >= MAX_SYMBOLS_PER_WATCHLIST:
            raise ValueError(f"A watchlist may hold at most {MAX_SYMBOLS_PER_WATCHLIST} symbols")

        now = datetime.now(timezone.utc)
        await self.db.watchlists.update_one(
            {"_id": oid, "user_id": user_id},
            {
                "$push": {"items": {"symbol": normalized, "note": note, "added_at": now}},
                "$set": {"updated_at": now},
            },
        )
        updated = await self.db.watchlists.find_one({"_id": oid, "user_id": user_id})
        return self._to_watchlist(updated) if updated else None

    async def remove_symbol(self, user_id: str, watchlist_id: str, symbol: str) -> Optional[Watchlist]:
        oid = self._object_id(watchlist_id)
        if oid is None:
            return None

        normalized = symbol.strip().upper()
        await self.db.watchlists.update_one(
            {"_id": oid, "user_id": user_id},
            {
                "$pull": {"items": {"symbol": normalized}},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )
        updated = await self.db.watchlists.find_one({"_id": oid, "user_id": user_id})
        return self._to_watchlist(updated) if updated else None

    async def get_watchlist_quotes(self, user_id: str, watchlist_id: str):
        """Return the latest quote for every symbol in the watchlist."""
        watchlist = await self.get_watchlist(user_id, watchlist_id)
        if watchlist is None:
            return None
        symbols = [item.symbol for item in watchlist.items]
        quotes = await self.analysis.get_quotes(symbols)
        return watchlist, quotes

    # -- alerts -----------------------------------------------------------

    async def list_alerts(self, user_id: str, active_only: bool = False) -> List[Alert]:
        query: Dict[str, Any] = {"user_id": user_id}
        if active_only:
            query["is_active"] = True
        cursor = self.db.alerts.find(query).sort("created_at", -1)
        docs = await cursor.to_list(length=MAX_ALERTS_PER_USER)
        return [self._to_alert(doc) for doc in docs]

    async def create_alert(self, user_id: str, request: AlertCreateRequest) -> Alert:
        count = await self.db.alerts.count_documents({"user_id": user_id})
        if count >= MAX_ALERTS_PER_USER:
            raise ValueError(f"A user may hold at most {MAX_ALERTS_PER_USER} alerts")

        if request.threshold is None and request.alert_type not in (
            AlertType.CROSS_ABOVE_SMA50,
            AlertType.CROSS_BELOW_SMA50,
        ):
            raise ValueError(f"Alert type '{request.alert_type.value}' requires a threshold")

        doc = {
            "user_id": user_id,
            "symbol": request.symbol.strip().upper(),
            "alert_type": request.alert_type.value,
            "threshold": request.threshold,
            "is_active": True,
            "triggered_at": None,
            "last_checked_at": None,
            "note": request.note,
            "created_at": datetime.now(timezone.utc),
        }
        result = await self.db.alerts.insert_one(doc)
        doc["_id"] = result.inserted_id
        return self._to_alert(doc)

    async def delete_alert(self, user_id: str, alert_id: str) -> bool:
        oid = self._object_id(alert_id)
        if oid is None:
            return False
        result = await self.db.alerts.delete_one({"_id": oid, "user_id": user_id})
        return result.deleted_count > 0

    async def check_alerts(self, user_id: str) -> AlertCheckResponse:
        """Evaluate every active alert for a user against the latest data."""
        alerts = await self.list_alerts(user_id, active_only=True)
        triggered: List[TriggeredAlert] = []
        now = datetime.now(timezone.utc)

        snapshots: Dict[str, Dict[str, Optional[float]]] = {}
        for alert in alerts:
            if alert.symbol not in snapshots:
                snapshot = await self.analysis.get_indicator_snapshot(alert.symbol)
                snapshots[alert.symbol] = snapshot.values if snapshot else {}

            values = snapshots[alert.symbol]
            quote = await self.analysis.get_quote(alert.symbol)
            outcome = evaluate_alert(alert, values, quote)
            if outcome is not None:
                current_value, message = outcome
                triggered.append(
                    TriggeredAlert(alert=alert, current_value=current_value, message=message)
                )
                oid = self._object_id(alert.id) if alert.id else None
                if oid is not None:
                    await self.db.alerts.update_one(
                        {"_id": oid}, {"$set": {"triggered_at": now, "last_checked_at": now}}
                    )
            elif alert.id:
                oid = self._object_id(alert.id)
                if oid is not None:
                    await self.db.alerts.update_one({"_id": oid}, {"$set": {"last_checked_at": now}})

        return AlertCheckResponse(checked=len(alerts), triggered=triggered)


def evaluate_alert(alert: Alert, values: Dict[str, Optional[float]], quote) -> Optional[tuple]:
    """Return ``(current_value, message)`` when the alert condition is met."""
    threshold = alert.threshold
    close = values.get("close") if values else None
    if close is None and quote is not None:
        close = quote.close

    change_percent = quote.change_percent if quote is not None else None
    rsi_value = values.get("rsi_14") if values else None
    volume_ratio = values.get("volume_ratio") if values else None
    sma50 = values.get("sma_50") if values else None

    if alert.alert_type == AlertType.PRICE_ABOVE and close is not None and threshold is not None:
        if close > threshold:
            return close, f"{alert.symbol} traded at {close}, above your {threshold} trigger."

    elif alert.alert_type == AlertType.PRICE_BELOW and close is not None and threshold is not None:
        if close < threshold:
            return close, f"{alert.symbol} traded at {close}, below your {threshold} trigger."

    elif alert.alert_type == AlertType.PERCENT_CHANGE_UP and change_percent is not None and threshold is not None:
        if change_percent >= threshold:
            return change_percent, (
                f"{alert.symbol} gained {change_percent * 100:.2f}%, "
                f"above your {threshold * 100:.2f}% trigger."
            )

    elif alert.alert_type == AlertType.PERCENT_CHANGE_DOWN and change_percent is not None and threshold is not None:
        if change_percent <= threshold:
            return change_percent, (
                f"{alert.symbol} lost {change_percent * 100:.2f}%, "
                f"below your {threshold * 100:.2f}% trigger."
            )

    elif alert.alert_type == AlertType.RSI_ABOVE and rsi_value is not None and threshold is not None:
        if rsi_value > threshold:
            return rsi_value, f"{alert.symbol} RSI is {rsi_value:.1f}, above your {threshold} trigger."

    elif alert.alert_type == AlertType.RSI_BELOW and rsi_value is not None and threshold is not None:
        if rsi_value < threshold:
            return rsi_value, f"{alert.symbol} RSI is {rsi_value:.1f}, below your {threshold} trigger."

    elif alert.alert_type == AlertType.VOLUME_SPIKE and volume_ratio is not None and threshold is not None:
        if volume_ratio >= threshold:
            return volume_ratio, (
                f"{alert.symbol} traded {volume_ratio:.1f}x its average volume, "
                f"above your {threshold}x trigger."
            )

    elif alert.alert_type == AlertType.CROSS_ABOVE_SMA50 and close is not None and sma50 is not None:
        if close > sma50:
            return close, f"{alert.symbol} closed at {close}, above its 50-day average of {sma50:.2f}."

    elif alert.alert_type == AlertType.CROSS_BELOW_SMA50 and close is not None and sma50 is not None:
        if close < sma50:
            return close, f"{alert.symbol} closed at {close}, below its 50-day average of {sma50:.2f}."

    return None


def _dedupe_symbols(symbols: List[str]) -> List[str]:
    """Upper-case, trim and de-duplicate while preserving order."""
    seen: set = set()
    result: List[str] = []
    for symbol in symbols:
        normalized = symbol.strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


watchlist_service = WatchlistService()
