from pydantic import BaseModel, EmailStr, Field, field_serializer
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from enum import Enum

class UserRole(str, Enum):
    FREE = "free"
    PREMIUM = "premium"
    ADMIN = "admin"

class OAuthProvider(str, Enum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"

class Language(str, Enum):
    CHINESE = "zh"
    ENGLISH = "en"

class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    CANCELED = "canceled"
    PAST_DUE = "past_due"
    UNPAID = "unpaid"
    TRIALING = "trialing"

class BillingInterval(str, Enum):
    MONTHLY = "month"
    YEARLY = "year"

class UserBase(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    role: UserRole = UserRole.FREE
    is_active: bool = True
    preferred_language: Language = Language.ENGLISH
    marketing_emails_enabled: bool = True
    daily_signals_email_enabled: Optional[bool] = None

class UserCreate(UserBase):
    password: Optional[str] = None  # Optional for OAuth users
    user_notice_version: Optional[str] = None
    user_notice_accepted_at: Optional[datetime] = None
    referred_by_user_id: Optional[str] = None
    referral_code_used: Optional[str] = None
    referral_reward_pending: bool = False

class UserRegister(BaseModel):
    email: EmailStr
    code: str
    name: Optional[str] = None
    preferred_language: Optional[Language] = Language.ENGLISH
    user_notice_version: str = Field(min_length=1)
    referral_code: Optional[str] = Field(default=None, min_length=4, max_length=32)

class UserUpdate(BaseModel):
    name: Optional[str] = None
    preferred_language: Optional[Language] = None
    daily_signals_email_enabled: Optional[bool] = None

class UserInDB(UserBase):
    id: str = Field(alias="_id")
    hashed_password: Optional[str] = None
    oauth_provider: Optional[OAuthProvider] = None
    oauth_provider_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_login: Optional[datetime] = None
    email_verified: bool = False
    premium_expires_at: Optional[datetime] = None
    user_notice_version: Optional[str] = None
    user_notice_accepted_at: Optional[datetime] = None
    marketing_unsubscribed_at: Optional[datetime] = None
    referral_code: Optional[str] = None
    referred_by_user_id: Optional[str] = None
    referral_code_used: Optional[str] = None
    referral_reward_pending: bool = False
    referral_access_expires_at: Optional[datetime] = None
    referral_reward_seconds_balance: int = 0
    applied_referral_rewards: list[str] = Field(default_factory=list)
    referral_rewards_granted: int = 0
    reserved_referral_ids: list[str] = Field(default_factory=list)
    # Stripe subscription fields
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    subscription_status: Optional[SubscriptionStatus] = None
    billing_interval: Optional[BillingInterval] = None
    trial_end: Optional[datetime] = None

    @field_serializer(
        'created_at',
        'updated_at',
        'last_login',
        'premium_expires_at',
        'trial_end',
        'user_notice_accepted_at',
        'marketing_unsubscribed_at',
        'referral_access_expires_at',
    )
    def serialize_datetime(self, dt: Optional[datetime], _info) -> Optional[str]:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt is None:
            return None
        # Ensure datetime is UTC aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Convert to UTC and format with Z suffix
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

    class Config:
        populate_by_name = True

class User(UserBase):
    daily_signals_email_enabled: bool
    id: str
    created_at: datetime
    updated_at: datetime
    last_login: Optional[datetime] = None
    email_verified: bool = False
    premium_expires_at: Optional[datetime] = None
    user_notice_version: Optional[str] = None
    user_notice_accepted_at: Optional[datetime] = None
    marketing_unsubscribed_at: Optional[datetime] = None
    has_premium_access: bool = False
    premium_access_expires_at: Optional[datetime] = None
    referral_access_expires_at: Optional[datetime] = None

    @field_serializer(
        'created_at',
        'updated_at',
        'last_login',
        'premium_expires_at',
        'user_notice_accepted_at',
        'marketing_unsubscribed_at',
        'premium_access_expires_at',
        'referral_access_expires_at',
    )
    def serialize_datetime(self, dt: Optional[datetime], _info) -> Optional[str]:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt is None:
            return None
        # Ensure datetime is UTC aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Convert to UTC and format with Z suffix
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

def to_public_user(user: UserInDB) -> User:
    """Convert the stored user model without dropping public consent metadata."""
    now = datetime.now(timezone.utc)
    premium_expires_at = user.premium_expires_at
    if premium_expires_at is not None and premium_expires_at.tzinfo is None:
        premium_expires_at = premium_expires_at.replace(tzinfo=timezone.utc)
    referral_expires_at = user.referral_access_expires_at
    if referral_expires_at is not None and referral_expires_at.tzinfo is None:
        referral_expires_at = referral_expires_at.replace(tzinfo=timezone.utc)
    referral_active = referral_expires_at is not None and referral_expires_at > now
    paid_active = user.role == UserRole.ADMIN or (
        user.role == UserRole.PREMIUM
        and (premium_expires_at is None or premium_expires_at > now)
    )
    daily_signals_email_enabled = (
        user.role in {UserRole.PREMIUM, UserRole.ADMIN}
        and user.daily_signals_email_enabled is not False
    )

    access_expires_at: Optional[datetime] = None
    if user.role != UserRole.ADMIN and not (
        user.role == UserRole.PREMIUM and user.premium_expires_at is None
    ):
        candidates = [
            expires_at
            for expires_at in (premium_expires_at, referral_expires_at)
            if expires_at is not None and expires_at > now
        ]
        access_expires_at = max(candidates) if candidates else None

    return User.model_validate(
        {
            **user.model_dump(),
            "has_premium_access": paid_active or referral_active,
            "premium_access_expires_at": access_expires_at,
            "daily_signals_email_enabled": daily_signals_email_enabled,
        }
    )

class VerificationCode(BaseModel):
    email: EmailStr
    code: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_serializer('expires_at', 'created_at')
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

class UserSession(BaseModel):
    user_id: str
    session_token: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None

    @field_serializer('expires_at', 'created_at')
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

class EmailRegisterCodeRequest(BaseModel):
    email: EmailStr
    preferred_language: Language = Language.ENGLISH

class EmailLoginRequest(BaseModel):
    email: EmailStr

class EmailLoginVerify(BaseModel):
    email: EmailStr
    code: str
    redirect_uri: Optional[str] = None

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: User

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class RefreshToken(BaseModel):
    user_id: str
    token: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_revoked: bool = False
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None

    @field_serializer('expires_at', 'created_at')
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

class TokenData(BaseModel):
    user_id: Optional[str] = None
    email: Optional[str] = None
    token_type: Optional[str] = None

# OAuth related models
class OAuthUserInfo(BaseModel):
    email: EmailStr
    name: Optional[str]
    provider: OAuthProvider
    provider_id: str

class OAuthConfirmRequest(BaseModel):
    oauth_token: str
    confirmed: bool = True
    name: Optional[str] = None
    user_notice_version: str = Field(min_length=1)

class UserNoticeAcceptanceRequest(BaseModel):
    user_notice_version: str = Field(min_length=1)

class AccountDeletionRequest(BaseModel):
    confirmation: EmailStr

class OAuthLinkRequest(BaseModel):
    oauth_token: str

class OAuthToken(BaseModel):
    token: str
    user_info: OAuthUserInfo
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    referral_code: Optional[str] = None

    @field_serializer('expires_at', 'created_at')
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

class OAuthExchangeRequest(BaseModel):
    code: str
    provider: OAuthProvider

# Stripe subscription models
class CreateCheckoutSessionRequest(BaseModel):
    billing_interval: BillingInterval
    success_url: str
    cancel_url: str

class CheckoutSessionResponse(BaseModel):
    checkout_url: str
    session_id: str

class SubscriptionInfo(BaseModel):
    status: Optional[SubscriptionStatus] = None
    billing_interval: Optional[BillingInterval] = None
    current_period_end: Optional[datetime] = None
    trial_end: Optional[datetime] = None
    cancel_at_period_end: Optional[bool] = None

class WebhookEvent(BaseModel):
    id: str
    type: str
    data: Dict[str, Any]
    created: int

# Subscription pricing models
class SubscriptionPrice(BaseModel):
    id: str
    interval: str
    amount: int
    currency: str
    product: str

class SubscriptionPricesResponse(BaseModel):
    prices: list[SubscriptionPrice]
    trial_period_days: int
