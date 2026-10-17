from datetime import datetime, timedelta, timezone
import logging
import secrets
from typing import Any, Optional

from bson import ObjectId
from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.database import get_database
from app.models.referral import (
    ReferralActivity,
    ReferralStatus,
    ReferralSummary,
    ReferralValidationResponse,
)
from app.models.user import SubscriptionStatus, UserInDB, UserRole

logger = logging.getLogger(__name__)

REFERRAL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SECONDS_PER_DAY = 24 * 60 * 60


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class ReferralService:
    def __init__(self) -> None:
        self.db = None

    async def initialize(self) -> None:
        self.db = await get_database()

    async def _ensure_db(self) -> None:
        if self.db is None:
            await self.initialize()

    @staticmethod
    def normalize_code(code: str) -> str:
        return code.strip().upper()

    async def get_inviter(
        self,
        referral_code: str,
        invitee_email: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        await self._ensure_db()
        if not settings.referral_program_enabled:
            return None

        code = self.normalize_code(referral_code)
        inviter = await self.db.users.find_one(
            {"referral_code": code, "is_active": {"$ne": False}}
        )
        if inviter and invitee_email and inviter["email"].lower() == invitee_email.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A referral code cannot be used by its owner",
            )
        return inviter

    async def validate_code(
        self,
        referral_code: str,
    ) -> ReferralValidationResponse:
        inviter = await self.get_inviter(referral_code)
        if not inviter:
            return ReferralValidationResponse(valid=False)

        rewards_granted = inviter.get("referral_rewards_granted", 0)
        reward_available = (
            rewards_granted < settings.referral_max_rewarded_referrals
        )
        return ReferralValidationResponse(
            valid=True,
            invitee_reward_days=(
                settings.referral_invitee_reward_days if reward_available else 0
            ),
        )

    async def track_click(
        self,
        referral_code: str,
        visitor_id: str,
    ) -> ReferralValidationResponse:
        validation = await self.validate_code(referral_code)
        if not validation.valid:
            return validation

        await self._ensure_db()
        code = self.normalize_code(referral_code)
        now = datetime.now(timezone.utc)
        await self.db.referral_clicks.update_one(
            {"referral_code": code, "visitor_id": visitor_id},
            {
                "$setOnInsert": {
                    "_id": str(ObjectId()),
                    "referral_code": code,
                    "visitor_id": visitor_id,
                    "created_at": now,
                }
            },
            upsert=True,
        )
        return validation

    async def get_or_create_code(self, user_id: str) -> str:
        await self._ensure_db()
        user = await self.db.users.find_one(
            {"_id": user_id},
            {"referral_code": 1},
        )
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        if user.get("referral_code"):
            return user["referral_code"]

        for _ in range(8):
            code = "".join(
                secrets.choice(REFERRAL_ALPHABET)
                for _ in range(settings.referral_code_length)
            )
            try:
                result = await self.db.users.update_one(
                    {
                        "_id": user_id,
                        "$or": [
                            {"referral_code": {"$exists": False}},
                            {"referral_code": None},
                        ],
                    },
                    {"$set": {"referral_code": code}},
                )
            except DuplicateKeyError:
                continue

            if result.modified_count:
                return code
            user = await self.db.users.find_one(
                {"_id": user_id},
                {"referral_code": 1},
            )
            if user and user.get("referral_code"):
                return user["referral_code"]

        raise RuntimeError("Unable to allocate a unique referral code")

    async def _reserve_reward_slot(
        self,
        inviter_id: str,
        referral_id: str,
    ) -> bool:
        inviter = await self.db.users.find_one(
            {"_id": inviter_id},
            {"reserved_referral_ids": 1, "referral_rewards_granted": 1},
        )
        if not inviter:
            return False
        if referral_id in inviter.get("reserved_referral_ids", []):
            return True
        if (
            inviter.get("referral_rewards_granted", 0)
            >= settings.referral_max_rewarded_referrals
        ):
            return False

        result = await self.db.users.update_one(
            {
                "_id": inviter_id,
                "reserved_referral_ids": {"$ne": referral_id},
                "$or": [
                    {"referral_rewards_granted": {"$exists": False}},
                    {
                        "referral_rewards_granted": {
                            "$lt": settings.referral_max_rewarded_referrals
                        }
                    },
                ],
            },
            {
                "$inc": {"referral_rewards_granted": 1},
                "$addToSet": {"reserved_referral_ids": referral_id},
            },
        )
        if result.modified_count:
            return True

        inviter = await self.db.users.find_one(
            {"_id": inviter_id},
            {"reserved_referral_ids": 1},
        )
        return bool(
            inviter and referral_id in inviter.get("reserved_referral_ids", [])
        )

    async def _apply_reward(
        self,
        user_id: str,
        reward_id: str,
        reward_days: int,
    ) -> None:
        for _ in range(8):
            user = await self.db.users.find_one(
                {"_id": user_id},
                {
                    "role": 1,
                    "premium_expires_at": 1,
                    "referral_access_expires_at": 1,
                    "referral_reward_seconds_balance": 1,
                    "applied_referral_rewards": 1,
                    "stripe_subscription_id": 1,
                    "subscription_status": 1,
                },
            )
            if not user:
                raise RuntimeError(f"Referral reward user {user_id} no longer exists")
            if reward_id in user.get("applied_referral_rewards", []):
                return

            now = datetime.now(timezone.utc)
            role = user.get("role", UserRole.FREE)
            paid_expires_at = _utc(user.get("premium_expires_at"))
            referral_expires_at = _utc(user.get("referral_access_expires_at"))
            subscription_status = user.get("subscription_status")
            has_active_paid_subscription = bool(
                user.get("stripe_subscription_id")
                and (
                    subscription_status
                    in {
                        SubscriptionStatus.ACTIVE,
                        SubscriptionStatus.TRIALING,
                    }
                    or (
                        role == UserRole.PREMIUM
                        and (
                            paid_expires_at is None
                            or paid_expires_at > now
                        )
                    )
                )
            )

            if has_active_paid_subscription:
                result = await self.db.users.update_one(
                    {
                        "_id": user_id,
                        "role": role,
                        "applied_referral_rewards": {"$ne": reward_id},
                    },
                    {
                        "$addToSet": {
                            "applied_referral_rewards": reward_id
                        },
                        "$inc": {
                            "referral_reward_seconds_balance": (
                                reward_days * SECONDS_PER_DAY
                            )
                        },
                        "$set": {"updated_at": now},
                    },
                )
                if result.modified_count:
                    return
                continue

            update: dict[str, Any] = {
                "$addToSet": {"applied_referral_rewards": reward_id},
                "$set": {"updated_at": now},
            }
            if role != UserRole.ADMIN and not (
                role == UserRole.PREMIUM and paid_expires_at is None
            ):
                base = max(
                    value
                    for value in (now, paid_expires_at, referral_expires_at)
                    if value is not None
                )
                update["$set"]["referral_access_expires_at"] = (
                    base + timedelta(days=reward_days)
                )

            query: dict[str, Any] = {
                "_id": user_id,
                "role": role,
                "applied_referral_rewards": {"$ne": reward_id},
            }
            if referral_expires_at is None:
                query["$or"] = [
                    {"referral_access_expires_at": {"$exists": False}},
                    {"referral_access_expires_at": None},
                ]
            else:
                query["referral_access_expires_at"] = referral_expires_at

            result = await self.db.users.update_one(query, update)
            if result.modified_count:
                return

        raise RuntimeError(f"Unable to apply referral reward {reward_id}")

    async def qualify_referral(
        self,
        inviter_id: str,
        invitee_id: str,
        referral_code: str,
    ) -> None:
        await self._ensure_db()
        code = self.normalize_code(referral_code)
        now = datetime.now(timezone.utc)
        referral = await self.db.referrals.find_one({"invitee_id": invitee_id})
        if not referral:
            referral = {
                "_id": str(ObjectId()),
                "inviter_id": inviter_id,
                "invitee_id": invitee_id,
                "referral_code": code,
                "status": ReferralStatus.PENDING,
                "created_at": now,
                "updated_at": now,
            }
            try:
                await self.db.referrals.insert_one(referral)
            except DuplicateKeyError:
                referral = await self.db.referrals.find_one(
                    {"invitee_id": invitee_id}
                )

        if not referral:
            raise RuntimeError("Unable to persist referral attribution")
        if referral["inviter_id"] != inviter_id:
            raise RuntimeError("Referral attribution is immutable")
        if referral.get("status") in {
            ReferralStatus.REWARDED,
            ReferralStatus.CAPPED,
        }:
            await self.db.users.update_one(
                {"_id": invitee_id},
                {"$set": {"referral_reward_pending": False}},
            )
            return

        referral_id = referral["_id"]
        reward_eligible = await self._reserve_reward_slot(
            inviter_id,
            referral_id,
        )
        if not reward_eligible:
            await self.db.referrals.update_one(
                {"_id": referral_id},
                {
                    "$set": {
                        "status": ReferralStatus.CAPPED,
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
            )
            await self.db.users.update_one(
                {"_id": invitee_id},
                {"$set": {"referral_reward_pending": False}},
            )
            return

        await self._apply_reward(
            inviter_id,
            f"referrer:{referral_id}",
            settings.referral_inviter_reward_days,
        )
        await self._apply_reward(
            invitee_id,
            f"invitee:{referral_id}",
            settings.referral_invitee_reward_days,
        )
        completed_at = datetime.now(timezone.utc)
        await self.db.referrals.update_one(
            {"_id": referral_id},
            {
                "$set": {
                    "status": ReferralStatus.REWARDED,
                    "rewarded_at": completed_at,
                    "updated_at": completed_at,
                }
            },
        )
        await self.db.users.update_one(
            {"_id": invitee_id},
            {"$set": {"referral_reward_pending": False}},
        )

    async def reconcile_user(self, user: UserInDB) -> UserInDB:
        if not user.referral_reward_pending or not user.referred_by_user_id:
            return user
        if not user.referral_code_used:
            logger.error("User %s has incomplete referral attribution", user.id)
            return user

        await self.qualify_referral(
            user.referred_by_user_id,
            user.id,
            user.referral_code_used,
        )
        refreshed = await self.db.users.find_one({"_id": user.id})
        return UserInDB(**refreshed) if refreshed else user

    async def get_summary(self, user: UserInDB) -> ReferralSummary:
        await self._ensure_db()
        if not settings.referral_program_enabled:
            return ReferralSummary(
                enabled=False,
                referral_code="",
                referral_url="",
                clicks=0,
                signups=0,
                successful_referrals=0,
                capped_referrals=0,
                conversion_rate=0,
                reward_days_earned=0,
                deferred_reward_days=0,
                inviter_reward_days=settings.referral_inviter_reward_days,
                invitee_reward_days=settings.referral_invitee_reward_days,
                max_rewarded_referrals=settings.referral_max_rewarded_referrals,
                recent_activity=[],
            )

        code = await self.get_or_create_code(user.id)
        clicks = await self.db.referral_clicks.count_documents(
            {"referral_code": code}
        )
        signups = await self.db.referrals.count_documents(
            {"inviter_id": user.id}
        )
        successful = await self.db.referrals.count_documents(
            {"inviter_id": user.id, "status": ReferralStatus.REWARDED}
        )
        capped = await self.db.referrals.count_documents(
            {"inviter_id": user.id, "status": ReferralStatus.CAPPED}
        )

        recent_activity: list[ReferralActivity] = []
        cursor = (
            self.db.referrals.find({"inviter_id": user.id})
            .sort("created_at", -1)
            .limit(10)
        )
        async for referral in cursor:
            referral_status = ReferralStatus(referral["status"])
            recent_activity.append(
                ReferralActivity(
                    created_at=referral["created_at"],
                    status=referral_status,
                    reward_days=(
                        settings.referral_inviter_reward_days
                        if referral_status == ReferralStatus.REWARDED
                        else 0
                    ),
                )
            )

        separator = "&" if "?" in settings.referral_frontend_url else "?"
        return ReferralSummary(
            enabled=settings.referral_program_enabled,
            referral_code=code,
            referral_url=(
                f"{settings.referral_frontend_url}{separator}ref={code}"
            ),
            clicks=clicks,
            signups=signups,
            successful_referrals=successful,
            capped_referrals=capped,
            conversion_rate=round((signups / clicks * 100) if clicks else 0, 1),
            reward_days_earned=(
                successful * settings.referral_inviter_reward_days
            ),
            deferred_reward_days=round(
                user.referral_reward_seconds_balance / SECONDS_PER_DAY,
                1,
            ),
            reward_access_expires_at=user.referral_access_expires_at,
            inviter_reward_days=settings.referral_inviter_reward_days,
            invitee_reward_days=settings.referral_invitee_reward_days,
            max_rewarded_referrals=settings.referral_max_rewarded_referrals,
            recent_activity=recent_activity,
        )


referral_service = ReferralService()
