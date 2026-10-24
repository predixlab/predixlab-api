from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from bson import ObjectId
from fastapi import HTTPException, status
import logging
import hmac

from app.database import get_database
from app.models.user import (
    UserCreate, UserInDB, User, UserRole, OAuthProvider, VerificationCode, RefreshToken,
    OAuthUserInfo, OAuthToken, UserRegister, Language
)
from app.core.security import (
    generate_refresh_token_string,
    generate_verification_code,
    get_password_hash,
    is_premium_user,
    verify_password,
)
from app.core.email import email_service
from app.config import settings
from app.services.referral_service import referral_service

logger = logging.getLogger(__name__)

MAX_VERIFICATION_ATTEMPTS = 5

class UserService:
    def __init__(self):
        self.db = None

    async def initialize(self):
        """Initialize database connection"""
        self.db = await get_database()

    @staticmethod
    def validate_user_notice_version(version: str) -> None:
        """Reject missing or stale legal notice acknowledgements."""
        if version != settings.user_notice_version:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The current User Notice and Risk Disclosure must be accepted",
            )

    async def create_user(self, user_data: UserCreate) -> UserInDB:
        """Create a new user"""
        if self.db is None:
            await self.initialize()

        # Check if user already exists
        existing_user = await self.db.users.find_one({"email": user_data.email})
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists"
            )

        # Create user document
        user_dict = user_data.model_dump()
        user_dict.update({
            "_id": str(ObjectId()),
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "email_verified": False
        })

        # Hash password if provided
        if user_data.password:
            user_dict["hashed_password"] = get_password_hash(user_data.password)
            del user_dict["password"]

        # Insert user
        await self.db.users.insert_one(user_dict)

        # Send welcome email with user's preferred language
        try:
            await email_service.send_welcome_email(
                user_data.email,
                user_dict["_id"],
                user_data.name,
                user_data.preferred_language
            )
        except Exception as e:
            logger.error(f"Failed to send welcome email to {user_data.email}: {e}")

        return UserInDB(**user_dict)

    async def get_user_by_email(self, email: str) -> Optional[UserInDB]:
        """Get user by email"""
        if self.db is None:
            await self.initialize()

        user_data = await self.db.users.find_one({"email": email})
        if user_data:
            return UserInDB(**user_data)
        return None

    async def get_user_by_id(self, user_id: str) -> Optional[UserInDB]:
        """Get user by ID"""
        if self.db is None:
            await self.initialize()

        user_data = await self.db.users.find_one({"_id": user_id})
        if user_data:
            return UserInDB(**user_data)
        return None

    async def authenticate_user(self, email: str, password: str) -> Optional[UserInDB]:
        """Authenticate user with email and password"""
        user = await self.get_user_by_email(email)
        if not user or not user.hashed_password:
            return None

        if not verify_password(password, user.hashed_password):
            return None

        return user

    async def update_user(self, user_id: str, update_data: Dict[str, Any]) -> Optional[UserInDB]:
        """Update user information"""
        if self.db is None:
            await self.initialize()

        update_data["updated_at"] = datetime.now(timezone.utc)

        result = await self.db.users.update_one(
            {"_id": user_id},
            {"$set": update_data}
        )

        if result.modified_count:
            return await self.get_user_by_id(user_id)
        return None

    async def update_last_login(self, user_id: str) -> None:
        """Update user's last login time"""
        if self.db is None:
            await self.initialize()

        await self.db.users.update_one(
            {"_id": user_id},
            {"$set": {"last_login": datetime.now(timezone.utc)}}
        )

    async def send_verification_code(self, email: str, for_registration: bool = False, language: Language = Language.ENGLISH) -> bool:
        """Send verification code to email"""
        if self.db is None:
            await self.initialize()

        # Generate verification code
        code = generate_verification_code()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

        # Store verification code with type information
        verification_data = {
            "email": email,
            "code": code,
            "expires_at": expires_at,
            "created_at": datetime.now(timezone.utc),
            "failed_attempts": 0,
            "type": "registration" if for_registration else "login"
        }

        # Remove old verification codes for this email
        await self.db.verification_codes.delete_many({"email": email})

        # Insert new verification code
        await self.db.verification_codes.insert_one(verification_data)

        # Send appropriate email based on type
        try:
            if for_registration:
                success = await email_service.send_registration_verification_code(email, code, language=language)
            else:
                success = await email_service.send_verification_code(email, code, language=language)
            return success
        except Exception as e:
            logger.error(f"Failed to send verification code to {email}: {e}")
            return False

    async def verify_email_code(self, email: str, code: str) -> bool:
        """Verify email verification code"""
        if self.db is None:
            await self.initialize()

        usable_code = {
            "expires_at": {"$gt": datetime.now(timezone.utc)},
            "$or": [
                {"failed_attempts": {"$lt": MAX_VERIFICATION_ATTEMPTS}},
                {"failed_attempts": {"$exists": False}},
            ],
        }
        verification = await self.db.verification_codes.find_one({
            "email": email,
            **usable_code,
        })
        if not verification:
            return False

        claim_query = {
            "_id": verification["_id"],
            **usable_code,
            "expires_at": {"$gt": datetime.now(timezone.utc)},
        }
        if not hmac.compare_digest(
            code.encode("utf-8"), verification["code"].encode("utf-8")
        ):
            # At five failures the document is dead, even before TTL cleanup.
            await self.db.verification_codes.update_one(
                claim_query, {"$inc": {"failed_attempts": 1}}
            )
            return False

        result = await self.db.verification_codes.delete_one(claim_query)
        if result.deleted_count != 1:
            return False

        await self.db.users.update_one(
            {"email": email},
            {"$set": {"email_verified": True}}
        )

        return True

    async def create_or_update_oauth_user(self, email: str, name: str, provider: OAuthProvider, provider_id: str) -> UserInDB:
        """Create or update OAuth user"""
        if self.db is None:
            await self.initialize()

        # Check if user exists
        existing_user = await self.get_user_by_email(email)

        if existing_user:
            # Update OAuth information
            update_data = {
                "oauth_provider": provider,
                "oauth_provider_id": provider_id,
                "email_verified": True,
                "last_login": datetime.now(timezone.utc)
            }
            if name and not existing_user.name:
                update_data["name"] = name

            return await self.update_user(existing_user.id, update_data)
        else:
            # Create new OAuth user
            user_data = UserCreate(
                email=email,
                name=name,
                role=UserRole.FREE
            )

            user_dict = user_data.model_dump()
            user_dict.update({
                "_id": str(ObjectId()),
                "oauth_provider": provider,
                "oauth_provider_id": provider_id,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "email_verified": True,
                "last_login": datetime.now(timezone.utc)
            })

            await self.db.users.insert_one(user_dict)

            # Send welcome email
            try:
                await email_service.send_welcome_email(email, user_dict["_id"], name)
            except Exception as e:
                logger.error(f"Failed to send welcome email to {email}: {e}")

            return UserInDB(**user_dict)

    async def upgrade_to_premium(self, user_id: str, expires_at: Optional[datetime] = None) -> Optional[UserInDB]:
        """Upgrade user to premium"""
        update_data = {
            "role": UserRole.PREMIUM,
            "premium_expires_at": expires_at
        }
        return await self.update_user(user_id, update_data)

    async def is_premium_active(self, user: UserInDB) -> bool:
        """Check if user's premium subscription is active"""
        return is_premium_user(
            user.role,
            user.premium_expires_at,
            user.referral_access_expires_at,
        )

    async def create_refresh_token(self, user_id: str, user_agent: Optional[str] = None, ip_address: Optional[str] = None) -> RefreshToken:
        """Create a new refresh token for user"""
        if self.db is None:
            await self.initialize()

        # Generate refresh token
        token = generate_refresh_token_string()
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)  # 7 days validity

        refresh_token_data = {
            "user_id": user_id,
            "token": token,
            "expires_at": expires_at,
            "created_at": datetime.now(timezone.utc),
            "is_revoked": False,
            "user_agent": user_agent,
            "ip_address": ip_address
        }

        # Insert refresh token
        await self.db.refresh_tokens.insert_one(refresh_token_data)

        return RefreshToken(**refresh_token_data)

    async def get_refresh_token(self, token: str) -> Optional[RefreshToken]:
        """Get refresh token by token string"""
        if self.db is None:
            await self.initialize()

        token_data = await self.db.refresh_tokens.find_one({
            "token": token,
            "is_revoked": False,
            "expires_at": {"$gt": datetime.now(timezone.utc)}
        })

        if token_data:
            return RefreshToken(**token_data)
        return None

    async def revoke_refresh_token(self, token: str) -> bool:
        """Revoke a refresh token"""
        if self.db is None:
            await self.initialize()

        result = await self.db.refresh_tokens.update_one(
            {"token": token},
            {"$set": {"is_revoked": True}}
        )

        return result.modified_count > 0

    async def revoke_all_user_refresh_tokens(self, user_id: str) -> int:
        """Revoke all refresh tokens for a user"""
        if self.db is None:
            await self.initialize()

        result = await self.db.refresh_tokens.update_many(
            {"user_id": user_id, "is_revoked": False},
            {"$set": {"is_revoked": True}}
        )

        return result.modified_count

    async def cleanup_expired_refresh_tokens(self) -> int:
        """Remove expired refresh tokens from database"""
        if self.db is None:
            await self.initialize()

        result = await self.db.refresh_tokens.delete_many({
            "expires_at": {"$lt": datetime.now(timezone.utc)}
        })

        return result.deleted_count

    # ===== OAuth login code (one-time) =====
    async def create_login_code(self, user_id: str, provider: OAuthProvider) -> str:
        """Create a short-lived, one-time login code for OAuth redirects."""
        if self.db is None:
            await self.initialize()

        code = generate_refresh_token_string()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        await self.db.login_codes.insert_one({
            "code": code,
            "user_id": user_id,
            "provider": provider,
            "expires_at": expires_at,
            "created_at": datetime.now(timezone.utc),
            "consumed": False
        })
        return code

    async def consume_login_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Consume a login code if valid and return payload."""
        if self.db is None:
            await self.initialize()

        doc = await self.db.login_codes.find_one({
            "code": code,
            "expires_at": {"$gt": datetime.now(timezone.utc)},
            "consumed": False
        })
        if not doc:
            return None

        claim_result = await self.db.login_codes.update_one(
            {
                "_id": doc["_id"],
                "consumed": False,
                "expires_at": {"$gt": datetime.now(timezone.utc)},
            },
            {"$set": {"consumed": True}},
        )
        if claim_result.matched_count != 1:
            return None

        return {"user_id": doc["user_id"], "provider": doc["provider"]}

    async def register_user(self, register_data: UserRegister) -> UserInDB:
        """Register a new user with email verification"""
        self.validate_user_notice_version(register_data.user_notice_version)

        if self.db is None:
            await self.initialize()

        inviter = None
        normalized_referral_code = None
        if register_data.referral_code:
            normalized_referral_code = referral_service.normalize_code(
                register_data.referral_code
            )
            inviter = await referral_service.get_inviter(
                normalized_referral_code,
                register_data.email,
            )
            if not inviter:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid referral code",
                )

        # Verify the email code first
        is_valid = await self.verify_email_code(register_data.email, register_data.code)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification code"
            )

        # Check if user already exists
        existing_user = await self.get_user_by_email(register_data.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification code"
            )

        # Create new user
        user_data = UserCreate(
            email=register_data.email,
            name=register_data.name,
            role=UserRole.FREE,
            preferred_language=register_data.preferred_language,
            user_notice_version=register_data.user_notice_version,
            user_notice_accepted_at=datetime.now(timezone.utc),
            referred_by_user_id=inviter["_id"] if inviter else None,
            referral_code_used=normalized_referral_code,
            referral_reward_pending=inviter is not None,
        )

        user = await self.create_user(user_data)
        if inviter and normalized_referral_code:
            try:
                await referral_service.qualify_referral(
                    inviter["_id"],
                    user.id,
                    normalized_referral_code,
                )
                refreshed = await self.get_user_by_id(user.id)
                if refreshed:
                    user = refreshed
            except Exception:
                logger.exception(
                    "Referral reward pending reconciliation for user %s",
                    user.id,
                )
        return user

    async def create_oauth_token(
        self,
        oauth_user_info: OAuthUserInfo,
        referral_code: Optional[str] = None,
    ) -> OAuthToken:
        """Create temporary OAuth token for confirmation"""
        if self.db is None:
            await self.initialize()

        token = generate_refresh_token_string()  # Reuse token generation
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)  # 15 minutes validity

        oauth_token_data = {
            "token": token,
            "user_info": oauth_user_info.model_dump(),
            "expires_at": expires_at,
            "created_at": datetime.now(timezone.utc),
            "referral_code": (
                referral_service.normalize_code(referral_code)
                if referral_code
                else None
            ),
        }

        # Remove old OAuth tokens for this email
        await self.db.oauth_tokens.delete_many({"user_info.email": oauth_user_info.email})

        # Insert new OAuth token
        await self.db.oauth_tokens.insert_one(oauth_token_data)

        return OAuthToken(**oauth_token_data)

    async def get_oauth_token(self, token: str) -> Optional[OAuthToken]:
        """Get OAuth token by token string"""
        if self.db is None:
            await self.initialize()

        token_data = await self.db.oauth_tokens.find_one({
            "token": token,
            "expires_at": {"$gt": datetime.now(timezone.utc)}
        })

        if token_data:
            return OAuthToken(**token_data)
        return None

    async def confirm_oauth_user(
        self,
        oauth_token: str,
        user_notice_version: str,
        name: Optional[str] = None,
    ) -> UserInDB:
        """Confirm OAuth user creation"""
        self.validate_user_notice_version(user_notice_version)

        if self.db is None:
            await self.initialize()

        # Get OAuth token
        token_data = await self.get_oauth_token(oauth_token)
        if not token_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired OAuth token"
            )

        oauth_info = token_data.user_info
        inviter = None
        normalized_referral_code = token_data.referral_code
        if normalized_referral_code:
            inviter = await referral_service.get_inviter(
                normalized_referral_code,
                oauth_info.email,
            )
            if not inviter:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid referral code",
                )

        # Check if user already exists
        existing_user = await self.get_user_by_email(oauth_info.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists"
            )

        # Create new OAuth user
        user_data = UserCreate(
            email=oauth_info.email,
            name=name or oauth_info.name,
            role=UserRole.FREE,
            user_notice_version=user_notice_version,
            user_notice_accepted_at=datetime.now(timezone.utc),
            referred_by_user_id=inviter["_id"] if inviter else None,
            referral_code_used=normalized_referral_code,
            referral_reward_pending=inviter is not None,
        )

        user_dict = user_data.model_dump()
        user_dict.update({
            "_id": str(ObjectId()),
            "oauth_provider": oauth_info.provider,
            "oauth_provider_id": oauth_info.provider_id,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "email_verified": True,
            "last_login": datetime.now(timezone.utc)
        })

        await self.db.users.insert_one(user_dict)

        # Remove used OAuth token
        await self.db.oauth_tokens.delete_one({"token": oauth_token})

        if inviter and normalized_referral_code:
            try:
                await referral_service.qualify_referral(
                    inviter["_id"],
                    user_dict["_id"],
                    normalized_referral_code,
                )
                refreshed = await self.get_user_by_id(user_dict["_id"])
                if refreshed:
                    user_dict = refreshed.model_dump(by_alias=True)
            except Exception:
                logger.exception(
                    "OAuth referral reward pending reconciliation for user %s",
                    user_dict["_id"],
                )

        # Send welcome email
        try:
            await email_service.send_welcome_email(
                oauth_info.email,
                user_dict["_id"],
                name or oauth_info.name,
            )
        except Exception as e:
            logger.error(f"Failed to send welcome email to {oauth_info.email}: {e}")

        return UserInDB(**user_dict)

    async def accept_user_notice(
        self,
        user_id: str,
        user_notice_version: str,
    ) -> UserInDB:
        """Record acceptance when an existing user sees a newer notice."""
        self.validate_user_notice_version(user_notice_version)
        accepted_at = datetime.now(timezone.utc)
        updated_user = await self.update_user(
            user_id,
            {
                "user_notice_version": user_notice_version,
                "user_notice_accepted_at": accepted_at,
            },
        )
        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to record User Notice acceptance",
            )
        return updated_user

    async def link_oauth_account(self, user_id: str, oauth_token: str) -> UserInDB:
        """Link OAuth account to existing user"""
        if self.db is None:
            await self.initialize()

        # Get OAuth token
        token_data = await self.get_oauth_token(oauth_token)
        if not token_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired OAuth token"
            )

        oauth_info = OAuthUserInfo(**token_data.user_info)

        # Get user
        user = await self.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Check if email matches
        if user.email != oauth_info.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth email does not match user email"
            )

        # Update user with OAuth information
        update_data = {
            "oauth_provider": oauth_info.provider,
            "oauth_provider_id": oauth_info.provider_id,
            "email_verified": True
        }

        updated_user = await self.update_user(user_id, update_data)

        # Remove used OAuth token
        await self.db.oauth_tokens.delete_one({"token": oauth_token})

        return updated_user

    async def cleanup_expired_oauth_tokens(self) -> int:
        """Remove expired OAuth tokens from database"""
        if self.db is None:
            await self.initialize()

        result = await self.db.oauth_tokens.delete_many({
            "expires_at": {"$lt": datetime.now(timezone.utc)}
        })

        return result.deleted_count

    async def unsubscribe_marketing_emails(self, user_id: str) -> bool:
        """Disable optional product emails while preserving account and billing mail."""
        if self.db is None:
            await self.initialize()

        result = await self.db.users.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "marketing_emails_enabled": False,
                    "marketing_unsubscribed_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        return result.matched_count > 0

    async def unsubscribe_daily_signals_emails(self, user_id: str) -> bool:
        """Disable Daily Signals mail without changing marketing consent."""
        if self.db is None:
            await self.initialize()

        result = await self.db.users.update_one(
            {"_id": user_id, "role": {"$in": ["premium", "admin"]}},
            {
                "$set": {
                    "daily_signals_email_enabled": False,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        return result.matched_count > 0

    async def delete_account_data(self, user: UserInDB) -> bool:
        """Delete the account and every collection owned by the user."""
        if self.db is None:
            await self.initialize()

        await self.db.refresh_tokens.delete_many({"user_id": user.id})
        await self.db.agent_api_keys.delete_many({"user_id": user.id})
        await self.db.agent_api_key_quotas.delete_one({"_id": user.id})
        await self.db.user_sessions.delete_many({"user_id": user.id})
        await self.db.login_codes.delete_many({"user_id": user.id})
        await self.db.verification_codes.delete_many({"email": user.email})
        await self.db.oauth_tokens.delete_many({"user_info.email": user.email})
        await self.db.watchlists.delete_many({"user_id": user.id})
        await self.db.alerts.delete_many({"user_id": user.id})
        await self.db.screener_strategies.delete_many({"user_id": user.id})
        if user.referral_code:
            await self.db.referral_clicks.delete_many(
                {"referral_code": user.referral_code}
            )
        await self.db.referrals.delete_many(
            {
                "$or": [
                    {"inviter_id": user.id},
                    {"invitee_id": user.id},
                ]
            }
        )
        result = await self.db.users.delete_one({"_id": user.id})
        return result.deleted_count > 0

# Create global user service instance
user_service = UserService()
