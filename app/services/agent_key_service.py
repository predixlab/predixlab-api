"""Hashed, scoped API keys with atomic rotation and revocation."""

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, NoReturn

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.core.dependencies import get_active_user_by_id
from app.core.indexes import AGENT_API_KEYS_LIST_INDEX
from app.core.rate_limits import enforce_mcp_request_limit
from app.database import get_database
from app.models.agent import (
    AgentApiKey,
    AgentApiKeyCreate,
    AgentApiKeyIssued,
    AgentApiKeyList,
)
from app.models.user import UserInDB

logger = logging.getLogger(__name__)
KEY_PATTERN = re.compile(r"plmcp_([0-9a-f]{32})_([A-Za-z0-9_-]{43})", re.ASCII)


def _unauthorized(reason: str) -> HTTPException:
    logger.warning("MCP API-key authentication rejected: reason=%s", reason)
    return HTTPException(
        status_code=401,
        detail="Invalid or expired MCP API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _new_secret(key_id: str) -> tuple[str, str]:
    token = f"plmcp_{key_id}_{secrets.token_urlsafe(32)}"
    return token, hashlib.sha256(token.encode("ascii")).hexdigest()


async def _key_collection() -> AsyncCollection[dict[str, Any]]:
    db = await get_database()
    if db is None:
        raise RuntimeError("MongoDB connection is not initialized")
    return db.agent_api_keys


def _public_key(document: dict[str, Any]) -> AgentApiKey:
    now = datetime.now(timezone.utc)
    revoked_at = document.get("revoked_at")
    expires_at = _utc(document["expires_at"])
    status = "revoked" if revoked_at else "expired" if expires_at <= now else "active"
    return AgentApiKey(
        id=document["_id"],
        name=document["name"],
        prefix=f"plmcp_{document['_id'][:8]}",
        created_at=_utc(document["created_at"]),
        expires_at=expires_at,
        last_used_at=_utc(document["last_used_at"]) if document.get("last_used_at") else None,
        revoked_at=_utc(revoked_at) if revoked_at else None,
        status=status,
        version=document["version"],
        expires_in_days=document["expires_in_days"],
    )


class AgentKeyService:
    async def _persist(self, document: dict[str, Any]) -> dict[str, Any]:
        collection = await _key_collection()
        try:
            await collection.insert_one(document)
            return document
        except DuplicateKeyError:
            existing = await collection.find_one({"_id": document["_id"]})
            if existing is None or existing["user_id"] != document["user_id"]:
                raise
            return existing

    async def _quota(self, user_id: str) -> AsyncCollection[dict[str, Any]]:
        db = await get_database()
        if db is None:
            raise RuntimeError("MongoDB connection is not initialized")
        quotas = db.agent_api_key_quotas
        quota = await quotas.find_one({"_id": user_id})
        if quota is None:
            existing = await db.agent_api_keys.find(
                {"user_id": user_id, "revoked_at": None}
            ).to_list(length=None)
            try:
                quota = await quotas.find_one_and_update(
                    {"_id": user_id},
                    {"$setOnInsert": {"key_ids": [key["_id"] for key in existing]}},
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
            except DuplicateKeyError:
                quota = await quotas.find_one({"_id": user_id})
        if quota is None:
            raise RuntimeError("MCP API-key quota is not initialized")
        for key_id, document in quota.get("pending", {}).items():
            if document["_id"] != key_id or document["user_id"] != user_id:
                raise RuntimeError("Invalid pending MCP API-key creation")
            await self._persist(document)
            await quotas.update_one(
                {"_id": user_id}, {"$unset": {f"pending.{key_id}": ""}}
            )
        return quotas

    async def _reserve(
        self, user_id: str, document: dict[str, Any]
    ) -> AsyncCollection[dict[str, Any]]:
        key_id = document["_id"]
        quotas = await self._quota(user_id)
        quota = await quotas.find_one({"_id": user_id})
        if quota is None:
            raise RuntimeError("MCP API-key quota is not initialized")
        keys = await _key_collection()
        revoked = await keys.find({
            "user_id": user_id,
            "_id": {"$in": quota["key_ids"]},
            "revoked_at": {"$ne": None},
        }).to_list(length=None)
        if revoked:
            await quotas.update_one(
                {"_id": user_id},
                {"$pull": {"key_ids": {"$in": [key["_id"] for key in revoked]}}},
            )
        reserved = await quotas.find_one_and_update(
            {
                "_id": user_id,
                f"key_ids.{settings.mcp_api_keys_per_user - 1}": {"$exists": False},
            },
            {
                "$addToSet": {"key_ids": key_id},
                # Durable intent makes an interrupted creation visible and manageable.
                "$set": {f"pending.{key_id}": document},
            },
            return_document=ReturnDocument.AFTER,
        )
        if reserved is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "mcp_key_limit",
                    "message": "API key limit reached. Revoke an unused key before creating another.",
                    "max_keys": settings.mcp_api_keys_per_user,
                },
            )
        return quotas

    async def create(self, user_id: str, request: AgentApiKeyCreate) -> AgentApiKeyIssued:
        now = datetime.now(timezone.utc)
        key_id = secrets.token_hex(16)
        token, digest = _new_secret(key_id)
        document = {
            "_id": key_id,
            "user_id": user_id,
            "name": request.name,
            "key_hash": digest,
            "scope": "mcp:read",
            "created_at": now,
            "expires_at": now + timedelta(days=request.expires_in_days),
            "expires_in_days": request.expires_in_days,
            "last_used_at": None,
            "revoked_at": None,
            "version": 1,
        }
        quotas = await self._reserve(user_id, document)
        stored = await self._persist(document)
        await quotas.update_one({"_id": user_id}, {"$unset": {f"pending.{key_id}": ""}})
        if stored.get("key_hash") != digest or stored.get("version") != 1:
            await self._conflict(user_id, key_id)
        logger.info("Created MCP API key %s for user %s", key_id, user_id)
        return AgentApiKeyIssued(api_key=token, key=_public_key(stored))

    async def list(self, user_id: str, limit: int, offset: int) -> AgentApiKeyList:
        await self._quota(user_id)
        collection = await _key_collection()
        query = {"user_id": user_id}
        cursor = (
            collection.find(query)
            .sort(AGENT_API_KEYS_LIST_INDEX)
            .skip(offset)
            .limit(limit)
        )
        documents = await cursor.to_list(length=limit)
        total = await collection.count_documents(query)
        non_revoked_count = await collection.count_documents(
            {"user_id": user_id, "revoked_at": None}
        )
        return AgentApiKeyList(
            items=[_public_key(document) for document in documents],
            total_count=total,
            limit=limit,
            offset=offset,
            max_keys=settings.mcp_api_keys_per_user,
            non_revoked_count=non_revoked_count,
        )

    async def _conflict(self, user_id: str, key_id: str) -> NoReturn:
        collection = await _key_collection()
        owned = await collection.find_one({"_id": key_id, "user_id": user_id})
        if owned is None:
            raise HTTPException(status_code=404, detail="MCP API key not found")
        raise HTTPException(
            status_code=409,
            detail="This API key changed or was revoked. Reload the list and try again.",
        )

    async def rotate(self, user_id: str, key_id: str, version: int) -> AgentApiKeyIssued:
        collection = await _key_collection()
        current = await collection.find_one({
            "_id": key_id, "user_id": user_id, "version": version, "revoked_at": None,
        })
        if current is None:
            await self._conflict(user_id, key_id)
        now = datetime.now(timezone.utc)
        token, digest = _new_secret(key_id)
        updated = await collection.find_one_and_update(
            {
                "_id": key_id, "user_id": user_id,
                "version": version, "revoked_at": None,
            },
            {
                "$set": {
                    "key_hash": digest,
                    "expires_at": now + timedelta(days=current["expires_in_days"]),
                    "last_used_at": None,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            await self._conflict(user_id, key_id)
        logger.info("Rotated MCP API key %s for user %s", key_id, user_id)
        return AgentApiKeyIssued(api_key=token, key=_public_key(updated))

    async def revoke(self, user_id: str, key_id: str, version: int) -> None:
        collection = await _key_collection()
        quotas = await self._quota(user_id)
        updated = await collection.find_one_and_update(
            {
                "_id": key_id, "user_id": user_id,
                "version": version, "revoked_at": None,
            },
            {
                "$set": {"revoked_at": datetime.now(timezone.utc), "key_hash": None},
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            await self._conflict(user_id, key_id)
        await quotas.update_one({"_id": user_id}, {"$pull": {"key_ids": key_id}})
        logger.info("Revoked MCP API key %s for user %s", key_id, user_id)

    async def authenticate(self, token: str) -> UserInDB:
        match = KEY_PATTERN.fullmatch(token)
        if match is None:
            reason = (
                "unresolved_input" if "${input:" in token
                else "unresolved_environment" if "${env:" in token
                else "invalid_format"
            )
            raise _unauthorized(reason)
        key_id = match.group(1)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        collection = await _key_collection()
        document = await collection.find_one({"_id": key_id})
        if document is None:
            raise _unauthorized("unknown_key")
        if document.get("revoked_at") is not None:
            raise _unauthorized("revoked")
        if document.get("scope") != "mcp:read":
            raise _unauthorized("invalid_scope")
        if _utc(document["expires_at"]) <= datetime.now(timezone.utc):
            raise _unauthorized("expired")
        if not isinstance(document.get("key_hash"), str):
            raise _unauthorized("missing_verifier")
        if not hmac.compare_digest(document["key_hash"], digest):
            raise _unauthorized("secret_mismatch")

        try:
            user = await get_active_user_by_id(document["user_id"])
        except HTTPException as exc:
            logger.warning("MCP API-key account check rejected: status=%s", exc.status_code)
            raise
        await enforce_mcp_request_limit(user.id, key_id)
        # Recheck the generation after awaited account/limit lookups.
        now = datetime.now(timezone.utc)
        result = await collection.update_one(
            {
                "_id": key_id, "key_hash": digest, "revoked_at": None,
                "expires_at": {"$gt": now},
            },
            {"$set": {"last_used_at": now}},
        )
        if result.matched_count != 1:
            raise _unauthorized("key_changed_during_authentication")
        return user


agent_key_service = AgentKeyService()
