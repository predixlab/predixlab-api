from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ReferralStatus(str, Enum):
    PENDING = "pending"
    REWARDED = "rewarded"
    CAPPED = "capped"


class ReferralClickRequest(BaseModel):
    referral_code: str = Field(min_length=4, max_length=32)
    visitor_id: str = Field(min_length=8, max_length=64)


class ReferralValidationResponse(BaseModel):
    valid: bool
    invitee_reward_days: int = 0


class ReferralActivity(BaseModel):
    created_at: datetime
    status: ReferralStatus
    reward_days: int


class ReferralSummary(BaseModel):
    enabled: bool
    referral_code: str
    referral_url: str
    clicks: int
    signups: int
    successful_referrals: int
    capped_referrals: int
    conversion_rate: float
    reward_days_earned: int
    deferred_reward_days: float
    reward_access_expires_at: Optional[datetime] = None
    inviter_reward_days: int
    invitee_reward_days: int
    max_rewarded_referrals: int
    recent_activity: list[ReferralActivity]
