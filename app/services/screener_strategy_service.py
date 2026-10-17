"""Saved screens.

A preset answers "show me a setup everyone knows". A saved strategy answers
"show me the one I worked out last week". The second only has value if it
survives the session, so it lives in MongoDB next to the rest of the user data
and follows the same shape as watchlists: per-user, name-unique, capped.

The criteria are stored as the ``ScreenerFilters`` model dumps them rather than
as free-form JSON, so a filter added later reads back as unset on an old
document instead of failing validation.
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
    ScreenerFilters,
    ScreenerStrategy,
    ScreenerStrategyCreateRequest,
    ScreenerStrategyUpdateRequest,
    SortDirection,
)

logger = logging.getLogger(__name__)

MAX_STRATEGIES_PER_USER = 50


class ScreenerStrategyService:
    """CRUD for the screens a user chose to keep."""

    def __init__(self):
        self.db: Optional[AsyncDatabase] = None

    async def initialize(self) -> None:
        if self.db is None:
            self.db = await get_database()
        await self._ensure_indexes()

    async def _ensure_indexes(self) -> None:
        if self.db is None:
            return
        try:
            await self.db.screener_strategies.create_index(
                [("user_id", 1), ("name", 1)], unique=True
            )
        except Exception as exc:  # pragma: no cover - index creation is best effort
            logger.warning(f"Failed to create screener strategy indexes: {exc}")

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _to_strategy(doc: Dict[str, Any]) -> ScreenerStrategy:
        return ScreenerStrategy(
            id=str(doc["_id"]),
            user_id=doc["user_id"],
            name=doc.get("name", "Untitled"),
            # An old document predates any filter added since it was written,
            # and a future one may carry a filter this build does not know.
            # Both must read back as a usable screen, so unknown keys are
            # dropped rather than raising.
            filters=ScreenerFilters.model_validate(doc.get("filters") or {}),
            sort_by=doc.get("sort_by", "change_percent"),
            sort_direction=doc.get("sort_direction", SortDirection.DESC),
            created_at=doc.get("created_at", datetime.now(timezone.utc)),
            updated_at=doc.get("updated_at", datetime.now(timezone.utc)),
        )

    @staticmethod
    def _object_id(value: str) -> Optional[ObjectId]:
        try:
            return ObjectId(value)
        except (InvalidId, TypeError):
            return None

    # -- CRUD -------------------------------------------------------------

    async def list_strategies(self, user_id: str) -> List[ScreenerStrategy]:
        cursor = self.db.screener_strategies.find({"user_id": user_id}).sort("created_at", 1)
        docs = await cursor.to_list(length=MAX_STRATEGIES_PER_USER)
        return [self._to_strategy(doc) for doc in docs]

    async def create_strategy(
        self, user_id: str, request: ScreenerStrategyCreateRequest
    ) -> ScreenerStrategy:
        existing = await self.db.screener_strategies.count_documents({"user_id": user_id})
        if existing >= MAX_STRATEGIES_PER_USER:
            raise ValueError(f"A user may keep at most {MAX_STRATEGIES_PER_USER} strategies")

        name = request.name.strip()
        if await self.db.screener_strategies.find_one({"user_id": user_id, "name": name}):
            raise ValueError(f"Strategy '{name}' already exists")

        now = datetime.now(timezone.utc)
        doc = {
            "user_id": user_id,
            "name": name,
            "filters": request.filters.model_dump(mode="json", exclude_none=True),
            "sort_by": request.sort_by,
            "sort_direction": request.sort_direction.value,
            "created_at": now,
            "updated_at": now,
        }
        result = await self.db.screener_strategies.insert_one(doc)
        doc["_id"] = result.inserted_id
        return self._to_strategy(doc)

    async def get_strategy(self, user_id: str, strategy_id: str) -> Optional[ScreenerStrategy]:
        oid = self._object_id(strategy_id)
        if oid is None:
            return None
        doc = await self.db.screener_strategies.find_one({"_id": oid, "user_id": user_id})
        return self._to_strategy(doc) if doc else None

    async def update_strategy(
        self, user_id: str, strategy_id: str, request: ScreenerStrategyUpdateRequest
    ) -> Optional[ScreenerStrategy]:
        oid = self._object_id(strategy_id)
        if oid is None:
            return None

        update: Dict[str, Any] = {}
        if request.name is not None:
            name = request.name.strip()
            duplicate = await self.db.screener_strategies.find_one(
                {"user_id": user_id, "name": name, "_id": {"$ne": oid}}
            )
            if duplicate:
                raise ValueError(f"Strategy '{name}' already exists")
            update["name"] = name
        if request.filters is not None:
            update["filters"] = request.filters.model_dump(mode="json", exclude_none=True)
        if request.sort_by is not None:
            update["sort_by"] = request.sort_by
        if request.sort_direction is not None:
            update["sort_direction"] = request.sort_direction.value

        if not update:
            return await self.get_strategy(user_id, strategy_id)

        update["updated_at"] = datetime.now(timezone.utc)
        doc = await self.db.screener_strategies.find_one_and_update(
            {"_id": oid, "user_id": user_id},
            {"$set": update},
            return_document=True,
        )
        return self._to_strategy(doc) if doc else None

    async def delete_strategy(self, user_id: str, strategy_id: str) -> bool:
        oid = self._object_id(strategy_id)
        if oid is None:
            return False
        result = await self.db.screener_strategies.delete_one(
            {"_id": oid, "user_id": user_id}
        )
        return result.deleted_count > 0


screener_strategy_service = ScreenerStrategyService()
