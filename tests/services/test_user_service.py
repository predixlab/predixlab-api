"""Unit tests for the user service."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from fastapi import HTTPException

from app.services.user_service import UserService
from app.config import settings
from app.models.user import (
    UserCreate, UserInDB, UserRole, OAuthProvider, UserRegister, Language,
    OAuthToken, OAuthUserInfo, RefreshToken, to_public_user
)


class TestUserServiceCreate:
    """Tests for user creation methods."""

    @pytest.mark.asyncio
    async def test_create_user_success(self, mock_db):
        """Test creating a new user successfully."""
        service = UserService()
        service.db = mock_db
        mock_db.users.find_one = AsyncMock(return_value=None)
        mock_db.users.insert_one = AsyncMock()

        user_data = UserCreate(
            email="test@example.com",
            name="Test User",
            role=UserRole.FREE,
            password="test_password"
        )

        with patch('app.services.user_service.email_service') as mock_email:
            mock_email.send_welcome_email = AsyncMock(return_value=True)
            result = await service.create_user(user_data)

        assert result.email == "test@example.com"
        assert result.name == "Test User"
        assert result.role == UserRole.FREE
        mock_db.users.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_user_already_exists(self, mock_db):
        """Test creating a user that already exists raises exception."""
        service = UserService()
        service.db = mock_db
        mock_db.users.find_one = AsyncMock(return_value={"email": "existing@example.com"})

        user_data = UserCreate(
            email="existing@example.com",
            name="Test User",
            role=UserRole.FREE
        )

        with pytest.raises(HTTPException) as exc_info:
            await service.create_user(user_data)

        assert exc_info.value.status_code == 400
        assert "already exists" in exc_info.value.detail


class TestUserServiceNoticeAcceptance:
    """Tests for versioned User Notice acceptance."""

    @pytest.mark.asyncio
    async def test_registration_persists_current_notice(self, mock_user):
        service = UserService()
        service.verify_email_code = AsyncMock(return_value=True)
        service.get_user_by_email = AsyncMock(return_value=None)
        service.create_user = AsyncMock(return_value=mock_user)
        register_data = UserRegister(
            email="new@example.com",
            code="123456",
            name="New User",
            user_notice_version=settings.user_notice_version,
        )

        await service.register_user(register_data)

        created_user = service.create_user.call_args.args[0]
        assert created_user.user_notice_version == settings.user_notice_version
        assert created_user.user_notice_accepted_at is not None

    @pytest.mark.asyncio
    async def test_registration_persists_and_rewards_referral(self, mock_user):
        service = UserService()
        service.verify_email_code = AsyncMock(return_value=True)
        service.get_user_by_email = AsyncMock(return_value=None)
        referred_user = mock_user.model_copy(
            update={
                "id": "invitee",
                "email": "new@example.com",
                "referral_reward_pending": True,
            }
        )
        rewarded_user = referred_user.model_copy(
            update={
                "referral_reward_pending": False,
                "referral_access_expires_at": (
                    datetime.now(timezone.utc) + timedelta(days=3)
                ),
            }
        )
        service.create_user = AsyncMock(return_value=referred_user)
        service.get_user_by_id = AsyncMock(return_value=rewarded_user)
        register_data = UserRegister(
            email="new@example.com",
            code="123456",
            name="New User",
            user_notice_version=settings.user_notice_version,
            referral_code="code1234",
        )

        with patch(
            "app.services.user_service.referral_service"
        ) as mock_referral:
            mock_referral.normalize_code.return_value = "CODE1234"
            mock_referral.get_inviter = AsyncMock(
                return_value={"_id": "inviter"}
            )
            mock_referral.qualify_referral = AsyncMock()
            result = await service.register_user(register_data)

        created_user = service.create_user.call_args.args[0]
        assert created_user.referred_by_user_id == "inviter"
        assert created_user.referral_code_used == "CODE1234"
        assert created_user.referral_reward_pending is True
        mock_referral.qualify_referral.assert_awaited_once_with(
            "inviter",
            "invitee",
            "CODE1234",
        )
        assert result.referral_reward_pending is False

    @pytest.mark.asyncio
    async def test_invalid_referral_does_not_consume_verification_code(self):
        service = UserService()
        service.db = MagicMock()
        service.verify_email_code = AsyncMock(return_value=True)
        register_data = UserRegister(
            email="new@example.com",
            code="123456",
            user_notice_version=settings.user_notice_version,
            referral_code="bad-code",
        )

        with patch(
            "app.services.user_service.referral_service"
        ) as mock_referral:
            mock_referral.normalize_code.return_value = "BAD-CODE"
            mock_referral.get_inviter = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc_info:
                await service.register_user(register_data)

        assert exc_info.value.status_code == 400
        service.verify_email_code.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_registration_rejects_stale_notice_before_using_code(self):
        service = UserService()
        service.verify_email_code = AsyncMock(return_value=True)
        register_data = UserRegister(
            email="new@example.com",
            code="123456",
            user_notice_version="outdated",
        )

        with pytest.raises(HTTPException) as exc_info:
            await service.register_user(register_data)

        assert exc_info.value.status_code == 400
        service.verify_email_code.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_existing_user_accepts_current_notice(self, mock_user):
        service = UserService()
        accepted_user = mock_user.model_copy(
            update={"user_notice_version": settings.user_notice_version}
        )
        service.update_user = AsyncMock(return_value=accepted_user)

        result = await service.accept_user_notice(
            mock_user.id,
            settings.user_notice_version,
        )

        assert result.user_notice_version == settings.user_notice_version
        update = service.update_user.call_args.args[1]
        assert update["user_notice_version"] == settings.user_notice_version
        assert update["user_notice_accepted_at"] is not None

    @pytest.mark.asyncio
    async def test_oauth_registration_persists_current_notice(self, mock_db):
        service = UserService()
        service.db = mock_db
        oauth_info = OAuthUserInfo(
            email="oauth@example.com",
            name="OAuth User",
            provider=OAuthProvider.GOOGLE,
            provider_id="google-id",
        )
        service.get_oauth_token = AsyncMock(
            return_value=OAuthToken(
                token="oauth-token",
                user_info=oauth_info,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        service.get_user_by_email = AsyncMock(return_value=None)
        mock_db.users.insert_one = AsyncMock()
        mock_db.oauth_tokens.delete_one = AsyncMock()

        with patch("app.services.user_service.email_service") as mock_email:
            mock_email.send_welcome_email = AsyncMock(return_value=True)
            result = await service.confirm_oauth_user(
                "oauth-token",
                settings.user_notice_version,
            )

        assert result.user_notice_version == settings.user_notice_version
        assert result.user_notice_accepted_at is not None
        inserted_user = mock_db.users.insert_one.call_args.args[0]
        assert inserted_user["user_notice_version"] == settings.user_notice_version


class TestUserServiceGet:
    """Tests for user retrieval methods."""

    @pytest.mark.asyncio
    async def test_get_user_by_email_found(self, mock_db, mock_user):
        """Test getting user by email when user exists."""
        service = UserService()
        service.db = mock_db
        mock_db.users.find_one = AsyncMock(return_value={
            "_id": mock_user.id,
            "email": mock_user.email,
            "name": mock_user.name,
            "role": mock_user.role,
            "is_active": mock_user.is_active,
            "preferred_language": mock_user.preferred_language,
            "created_at": mock_user.created_at,
            "updated_at": mock_user.updated_at
        })

        result = await service.get_user_by_email(mock_user.email)

        assert result is not None
        assert result.email == mock_user.email
        mock_db.users.find_one.assert_called_once_with({"email": mock_user.email})

    @pytest.mark.asyncio
    async def test_get_user_by_email_not_found(self, mock_db):
        """Test getting user by email when user doesn't exist."""
        service = UserService()
        service.db = mock_db
        mock_db.users.find_one = AsyncMock(return_value=None)

        result = await service.get_user_by_email("nonexistent@example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_by_id_found(self, mock_db, mock_user):
        """Test getting user by ID when user exists."""
        service = UserService()
        service.db = mock_db
        mock_db.users.find_one = AsyncMock(return_value={
            "_id": mock_user.id,
            "email": mock_user.email,
            "name": mock_user.name,
            "role": mock_user.role,
            "is_active": mock_user.is_active,
            "preferred_language": mock_user.preferred_language,
            "created_at": mock_user.created_at,
            "updated_at": mock_user.updated_at
        })

        result = await service.get_user_by_id(mock_user.id)

        assert result is not None
        assert result.id == mock_user.id
        mock_db.users.find_one.assert_called_once_with({"_id": mock_user.id})

    @pytest.mark.asyncio
    async def test_get_user_by_id_not_found(self, mock_db):
        """Test getting user by ID when user doesn't exist."""
        service = UserService()
        service.db = mock_db
        mock_db.users.find_one = AsyncMock(return_value=None)

        result = await service.get_user_by_id("nonexistent_id")

        assert result is None


class TestUserServiceUpdate:
    """Tests for user update methods."""

    @pytest.mark.asyncio
    async def test_update_user_success(self, mock_db, mock_user):
        """Test updating user information."""
        service = UserService()
        service.db = mock_db

        mock_result = MagicMock()
        mock_result.modified_count = 1
        mock_db.users.update_one = AsyncMock(return_value=mock_result)
        mock_db.users.find_one = AsyncMock(return_value={
            "_id": mock_user.id,
            "email": mock_user.email,
            "name": "Updated Name",
            "role": mock_user.role,
            "is_active": mock_user.is_active,
            "preferred_language": mock_user.preferred_language,
            "created_at": mock_user.created_at,
            "updated_at": datetime.now(timezone.utc)
        })

        result = await service.update_user(mock_user.id, {"name": "Updated Name"})

        assert result is not None
        assert result.name == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_user_not_modified(self, mock_db, mock_user):
        """Test updating user when no changes are made."""
        service = UserService()
        service.db = mock_db

        mock_result = MagicMock()
        mock_result.modified_count = 0
        mock_db.users.update_one = AsyncMock(return_value=mock_result)

        result = await service.update_user(mock_user.id, {"name": "Same Name"})

        assert result is None

    @pytest.mark.asyncio
    async def test_update_last_login(self, mock_db, mock_user):
        """Test updating user's last login time."""
        service = UserService()
        service.db = mock_db
        mock_db.users.update_one = AsyncMock()

        await service.update_last_login(mock_user.id)

        mock_db.users.update_one.assert_called_once()
        call_args = mock_db.users.update_one.call_args
        assert call_args[0][0] == {"_id": mock_user.id}
        assert "last_login" in call_args[0][1]["$set"]


class TestUserServiceVerification:
    """Tests for verification code methods."""

    @pytest.mark.asyncio
    async def test_send_verification_code_success(self, mock_db):
        """Test sending verification code successfully."""
        service = UserService()
        service.db = mock_db
        mock_db.verification_codes.delete_many = AsyncMock()
        mock_db.verification_codes.insert_one = AsyncMock()

        with patch('app.services.user_service.email_service') as mock_email:
            mock_email.send_verification_code = AsyncMock(return_value=True)

            result = await service.send_verification_code(
                "test@example.com",
                for_registration=False
            )

        assert result is True
        mock_db.verification_codes.delete_many.assert_called_once()
        mock_db.verification_codes.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_registration_verification_code(self, mock_db):
        """Test sending registration verification code."""
        service = UserService()
        service.db = mock_db
        mock_db.verification_codes.delete_many = AsyncMock()
        mock_db.verification_codes.insert_one = AsyncMock()

        with patch('app.services.user_service.email_service') as mock_email:
            mock_email.send_registration_verification_code = AsyncMock(return_value=True)

            result = await service.send_verification_code(
                "test@example.com",
                for_registration=True
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_email_code_valid(self, mock_db):
        """Test verifying a valid email code."""
        service = UserService()
        service.db = mock_db

        verification_doc = {
            "_id": "verification_id",
            "email": "test@example.com",
            "code": "123456",
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5)
        }
        mock_db.verification_codes.find_one = AsyncMock(return_value=verification_doc)
        mock_db.verification_codes.delete_one = AsyncMock()
        mock_db.users.update_one = AsyncMock()

        result = await service.verify_email_code("test@example.com", "123456")

        assert result is True
        mock_db.verification_codes.delete_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_verify_email_code_invalid(self, mock_db):
        """Test verifying an invalid email code."""
        service = UserService()
        service.db = mock_db
        mock_db.verification_codes.find_one = AsyncMock(return_value=None)

        result = await service.verify_email_code("test@example.com", "wrong_code")

        assert result is False


class TestUserServiceRefreshToken:
    """Tests for refresh token methods."""

    @pytest.mark.asyncio
    async def test_create_refresh_token(self, mock_db):
        """Test creating a refresh token."""
        service = UserService()
        service.db = mock_db
        mock_db.refresh_tokens.insert_one = AsyncMock()

        result = await service.create_refresh_token(
            user_id="user123",
            user_agent="Mozilla/5.0",
            ip_address="192.168.1.1"
        )

        assert result is not None
        assert result.user_id == "user123"
        assert result.token is not None
        mock_db.refresh_tokens.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_refresh_token_valid(self, mock_db):
        """Test getting a valid refresh token."""
        service = UserService()
        service.db = mock_db

        token_data = {
            "user_id": "user123",
            "token": "valid_token",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
            "created_at": datetime.now(timezone.utc),
            "is_revoked": False
        }
        mock_db.refresh_tokens.find_one = AsyncMock(return_value=token_data)

        result = await service.get_refresh_token("valid_token")

        assert result is not None
        assert result.token == "valid_token"

    @pytest.mark.asyncio
    async def test_get_refresh_token_not_found(self, mock_db):
        """Test getting a non-existent refresh token."""
        service = UserService()
        service.db = mock_db
        mock_db.refresh_tokens.find_one = AsyncMock(return_value=None)

        result = await service.get_refresh_token("invalid_token")

        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_refresh_token(self, mock_db):
        """Test revoking a refresh token."""
        service = UserService()
        service.db = mock_db

        mock_result = MagicMock()
        mock_result.modified_count = 1
        mock_db.refresh_tokens.update_one = AsyncMock(return_value=mock_result)

        result = await service.revoke_refresh_token("token_to_revoke")

        assert result is True

    @pytest.mark.asyncio
    async def test_revoke_all_user_refresh_tokens(self, mock_db):
        """Test revoking all refresh tokens for a user."""
        service = UserService()
        service.db = mock_db

        mock_result = MagicMock()
        mock_result.modified_count = 5
        mock_db.refresh_tokens.update_many = AsyncMock(return_value=mock_result)

        result = await service.revoke_all_user_refresh_tokens("user123")

        assert result == 5


class TestUserServicePremium:
    """Tests for premium subscription methods."""

    @pytest.mark.asyncio
    async def test_upgrade_to_premium(self, mock_db, mock_user):
        """Test upgrading user to premium."""
        service = UserService()
        service.db = mock_db

        expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        mock_result = MagicMock()
        mock_result.modified_count = 1
        mock_db.users.update_one = AsyncMock(return_value=mock_result)
        mock_db.users.find_one = AsyncMock(return_value={
            "_id": mock_user.id,
            "email": mock_user.email,
            "name": mock_user.name,
            "role": UserRole.PREMIUM,
            "is_active": True,
            "preferred_language": Language.ENGLISH,
            "created_at": mock_user.created_at,
            "updated_at": datetime.now(timezone.utc),
            "premium_expires_at": expires_at
        })

        result = await service.upgrade_to_premium(mock_user.id, expires_at)

        assert result is not None
        assert result.role == UserRole.PREMIUM

    @pytest.mark.asyncio
    async def test_is_premium_active_admin(self, mock_admin_user):
        """Test that admin is considered premium active."""
        service = UserService()

        result = await service.is_premium_active(mock_admin_user)

        assert result is True

    @pytest.mark.asyncio
    async def test_is_premium_active_premium_lifetime(self, mock_premium_user):
        """Test that premium user without expiry is active."""
        service = UserService()
        mock_premium_user.premium_expires_at = None

        result = await service.is_premium_active(mock_premium_user)

        assert result is True

    @pytest.mark.asyncio
    async def test_is_premium_active_premium_valid(self, mock_premium_user):
        """Test that premium user with future expiry is active."""
        service = UserService()

        result = await service.is_premium_active(mock_premium_user)

        assert result is True

    @pytest.mark.asyncio
    async def test_is_premium_active_premium_expired(self, mock_expired_premium_user):
        """Test that premium user with past expiry is not active."""
        service = UserService()

        result = await service.is_premium_active(mock_expired_premium_user)

        assert result is False

    @pytest.mark.asyncio
    async def test_is_premium_active_free_user(self, mock_user):
        """Test that free user is not premium active."""
        service = UserService()

        result = await service.is_premium_active(mock_user)

        assert result is False

    def test_public_user_exposes_referral_access(self, mock_user):
        referral_expiry = datetime.now(timezone.utc) + timedelta(days=3)
        public_user = to_public_user(
            mock_user.model_copy(
                update={"referral_access_expires_at": referral_expiry}
            )
        )

        assert public_user.role == UserRole.FREE
        assert public_user.has_premium_access is True
        assert public_user.premium_access_expires_at == referral_expiry


class TestUserServiceOAuth:
    """Tests for OAuth-related methods."""

    @pytest.mark.asyncio
    async def test_create_oauth_token_preserves_referral_code(self, mock_db):
        service = UserService()
        service.db = mock_db
        mock_db.oauth_tokens.delete_many = AsyncMock()
        mock_db.oauth_tokens.insert_one = AsyncMock()
        oauth_info = OAuthUserInfo(
            email="oauth@example.com",
            name="OAuth User",
            provider=OAuthProvider.GOOGLE,
            provider_id="google-id",
        )

        token = await service.create_oauth_token(
            oauth_info,
            referral_code="code1234",
        )

        assert token.referral_code == "CODE1234"
        inserted = mock_db.oauth_tokens.insert_one.call_args.args[0]
        assert inserted["referral_code"] == "CODE1234"

    @pytest.mark.asyncio
    async def test_create_oauth_token(self, mock_db):
        """Test creating OAuth token for new user."""
        service = UserService()
        service.db = mock_db
        mock_db.oauth_tokens.delete_many = AsyncMock()
        mock_db.oauth_tokens.insert_one = AsyncMock()

        oauth_info = OAuthUserInfo(
            email="oauth@example.com",
            name="OAuth User",
            provider=OAuthProvider.GOOGLE,
            provider_id="google123"
        )

        result = await service.create_oauth_token(oauth_info)

        assert result is not None
        assert result.token is not None
        assert result.user_info.email == "oauth@example.com"

    @pytest.mark.asyncio
    async def test_get_oauth_token_valid(self, mock_db):
        """Test getting a valid OAuth token."""
        service = UserService()
        service.db = mock_db

        token_data = {
            "token": "valid_oauth_token",
            "user_info": {
                "email": "oauth@example.com",
                "name": "OAuth User",
                "provider": "google",
                "provider_id": "google123"
            },
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
            "created_at": datetime.now(timezone.utc)
        }
        mock_db.oauth_tokens.find_one = AsyncMock(return_value=token_data)

        result = await service.get_oauth_token("valid_oauth_token")

        assert result is not None
        assert result.token == "valid_oauth_token"

    @pytest.mark.asyncio
    async def test_get_oauth_token_not_found(self, mock_db):
        """Test getting a non-existent OAuth token."""
        service = UserService()
        service.db = mock_db
        mock_db.oauth_tokens.find_one = AsyncMock(return_value=None)

        result = await service.get_oauth_token("invalid_token")

        assert result is None


class TestUserServiceLoginCode:
    """Tests for login code methods."""

    @pytest.mark.asyncio
    async def test_create_login_code(self, mock_db):
        """Test creating a login code."""
        service = UserService()
        service.db = mock_db
        mock_db.login_codes.insert_one = AsyncMock()

        result = await service.create_login_code("user123", OAuthProvider.GOOGLE)

        assert result is not None
        assert len(result) > 0
        mock_db.login_codes.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_consume_login_code_valid(self, mock_db):
        """Test consuming a valid login code."""
        service = UserService()
        service.db = mock_db

        code_doc = {
            "_id": "code_id",
            "code": "valid_code",
            "user_id": "user123",
            "provider": OAuthProvider.GOOGLE,
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "consumed": False
        }
        mock_db.login_codes.find_one = AsyncMock(return_value=code_doc)
        mock_db.login_codes.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )

        result = await service.consume_login_code("valid_code")

        assert result is not None
        assert result["user_id"] == "user123"
        mock_db.login_codes.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_consume_login_code_returns_none_when_already_claimed(self, mock_db):
        """A concurrent exchange must not consume the same OAuth code twice."""
        service = UserService()
        service.db = mock_db

        code_doc = {
            "_id": "code_id",
            "code": "valid_code",
            "user_id": "user123",
            "provider": OAuthProvider.GOOGLE,
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "consumed": False,
        }
        mock_db.login_codes.find_one = AsyncMock(return_value=code_doc)
        mock_db.login_codes.update_one = AsyncMock(
            return_value=MagicMock(matched_count=0)
        )

        result = await service.consume_login_code("valid_code")

        assert result is None
        claim_filter = mock_db.login_codes.update_one.call_args.args[0]
        assert claim_filter["_id"] == "code_id"
        assert claim_filter["consumed"] is False
        assert claim_filter["expires_at"]["$gt"] <= datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_consume_login_code_invalid(self, mock_db):
        """Test consuming an invalid login code."""
        service = UserService()
        service.db = mock_db
        mock_db.login_codes.find_one = AsyncMock(return_value=None)

        result = await service.consume_login_code("invalid_code")

        assert result is None


class TestUserServiceCleanup:
    """Tests for cleanup methods."""

    @pytest.mark.asyncio
    async def test_cleanup_expired_refresh_tokens(self, mock_db):
        """Test cleaning up expired refresh tokens."""
        service = UserService()
        service.db = mock_db

        mock_result = MagicMock()
        mock_result.deleted_count = 10
        mock_db.refresh_tokens.delete_many = AsyncMock(return_value=mock_result)

        result = await service.cleanup_expired_refresh_tokens()

        assert result == 10

    @pytest.mark.asyncio
    async def test_cleanup_expired_oauth_tokens(self, mock_db):
        """Test cleaning up expired OAuth tokens."""
        service = UserService()
        service.db = mock_db

        mock_result = MagicMock()
        mock_result.deleted_count = 5
        mock_db.oauth_tokens.delete_many = AsyncMock(return_value=mock_result)

        result = await service.cleanup_expired_oauth_tokens()

        assert result == 5
