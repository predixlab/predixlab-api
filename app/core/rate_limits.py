"""MongoDB-backed authentication limits shared by all API replicas."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import ceil
from typing import Literal

from fastapi import HTTPException, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.database import get_database


def _key(scope: str, value: str, *, prefix: str = "otp") -> str:
    return f"{prefix}:{scope}:{sha256(value.encode('utf-8')).hexdigest()}"


def _too_many_requests(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests. Please try again later.",
        headers={"Retry-After": str(max(1, retry_after))},
    )


async def _enforce_fixed_window_limits(
    limits: list[tuple[str, int]],
    window: int,
    now: datetime,
) -> None:
    db = await get_database()
    bucket = int(now.timestamp()) // window
    expires_at = datetime.fromtimestamp((bucket + 1) * window, timezone.utc)
    limited = False
    for key, limit in limits:
        query = {
            "_id": f"{key}:{bucket}",
            "expires_at": {"$gt": now},
        }
        update = {
            "$inc": {"count": 1},
            "$setOnInsert": {"expires_at": expires_at},
        }
        try:
            counter = await db.rate_limits.find_one_and_update(
                query, update, upsert=True, return_document=ReturnDocument.AFTER
            )
        except DuplicateKeyError:
            # Two replicas may try to insert the first counter simultaneously.
            counter = await db.rate_limits.find_one_and_update(
                query, update, upsert=True, return_document=ReturnDocument.AFTER
            )
        if counter["count"] > limit:
            limited = True

    if limited:
        raise _too_many_requests(ceil((expires_at - now).total_seconds()))


async def enforce_ip_rate_limit(
    client_ip: str,
    *,
    action: Literal["oauth_exchange", "refresh"],
) -> None:
    """Limit token endpoints before processing credentials."""
    if action == "oauth_exchange":
        limit = settings.oauth_exchange_ip_limit
        window = settings.oauth_exchange_window_seconds
    else:
        limit = settings.refresh_ip_limit
        window = settings.refresh_window_seconds
    await _enforce_fixed_window_limits(
        [(_key(f"{action}:ip", client_ip, prefix="auth"), limit)],
        window,
        datetime.now(timezone.utc),
    )


async def enforce_code_rate_limit(
    email: str,
    client_ip: str,
    *,
    action: Literal["request", "verify"],
) -> None:
    """Count both dimensions, including rejected requests, before account lookup."""
    now = datetime.now(timezone.utc)
    if action == "request":
        window = settings.otp_request_window_seconds
        email_limit = settings.otp_request_email_limit
        ip_limit = settings.otp_request_ip_limit
    else:
        window = settings.otp_verify_window_seconds
        email_limit = settings.otp_verify_email_limit
        ip_limit = settings.otp_verify_ip_limit
    await _enforce_fixed_window_limits(
        [
            (_key(f"{action}:email", email.strip().casefold()), email_limit),
            (_key(f"{action}:ip", client_ip), ip_limit),
        ],
        window,
        now,
    )

    if action == "request":
        db = await get_database()
        cooldown = settings.otp_resend_cooldown_seconds
        cooldown_key = _key("cooldown", email.strip().casefold())
        try:
            await db.rate_limits.find_one_and_update(
                {"_id": cooldown_key, "expires_at": {"$lte": now}},
                {"$set": {"expires_at": now + timedelta(seconds=cooldown)}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # An unexpired document cannot match the upsert's expiry predicate.
            previous = await db.rate_limits.find_one({
                "_id": cooldown_key, "expires_at": {"$gt": now}
            })
            retry_after = cooldown
            if previous:
                expiry = previous["expires_at"].replace(tzinfo=timezone.utc)
                retry_after = ceil((expiry - now).total_seconds())
            raise _too_many_requests(retry_after) from None
