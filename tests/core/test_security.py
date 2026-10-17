"""Unit tests for the security module."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    verify_token,
    generate_verification_code,
    generate_session_token,
    create_refresh_token,
    verify_refresh_token,
    generate_refresh_token_string,
    create_email_verification_token,
    verify_email_verification_token,
    is_premium_user,
    can_access_recent_data,
)
from app.config import settings
from fastapi import HTTPException
from jose import jwt


class TestPasswordHashing:
    """Tests for password hashing functions."""

    def test_get_password_hash(self):
        """Test that password hash is generated correctly."""
        password = "test_password_123"
        hashed = get_password_hash(password)
        
        assert hashed is not None
        assert hashed != password
        assert len(hashed) > 0

    def test_verify_password_correct(self):
        """Test that correct password is verified successfully."""
        password = "test_password_123"
        hashed = get_password_hash(password)
        
        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """Test that incorrect password verification fails."""
        password = "test_password_123"
        wrong_password = "wrong_password"
        hashed = get_password_hash(password)
        
        assert verify_password(wrong_password, hashed) is False

    def test_password_hash_unique(self):
        """Test that same password produces different hashes (due to salting)."""
        password = "test_password_123"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)
        
        # Both hashes should verify the same password
        assert verify_password(password, hash1) is True
        assert verify_password(password, hash2) is True
        # But hashes should be different due to salting
        assert hash1 != hash2


class TestAccessToken:
    """Tests for access token functions."""

    def test_create_access_token_with_expiry(self):
        """Test creating access token with custom expiry."""
        data = {"sub": "user123", "email": "test@example.com"}
        expires_delta = timedelta(minutes=15)
        
        token = create_access_token(data, expires_delta)
        
        assert token is not None
        assert len(token) > 0

    def test_create_access_token_default_expiry(self):
        """Test creating access token with default expiry."""
        data = {"sub": "user123", "email": "test@example.com"}
        
        token = create_access_token(data)
        
        assert token is not None
        assert len(token) > 0

    def test_verify_token_valid(self):
        """Test that valid token is verified correctly."""
        data = {"sub": "user123", "email": "test@example.com"}
        token = create_access_token(data, timedelta(minutes=15))
        
        payload = verify_token(token)
        
        assert payload["sub"] == "user123"
        assert payload["email"] == "test@example.com"
        assert "exp" in payload
        assert payload["type"] == "access"

    def test_verify_token_accepts_unexpired_legacy_access_token(self):
        """Unexpired access JWTs issued before purpose claims remain valid."""
        legacy_token = jwt.encode(
            {
                "sub": "user123",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
            },
            settings.secret_key,
            algorithm=settings.algorithm,
        )

        payload = verify_token(legacy_token)

        assert payload["sub"] == "user123"
        assert "type" not in payload

    @pytest.mark.parametrize(
        "token_type", ["refresh", "email_verification", "unknown", None]
    )
    def test_verify_token_rejects_explicit_non_access_type(self, token_type):
        """JWTs with an explicit non-access purpose cannot authenticate requests."""
        token = jwt.encode(
            {
                "sub": "user123",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
                "type": token_type,
            },
            settings.secret_key,
            algorithm=settings.algorithm,
        )

        with pytest.raises(HTTPException) as exc_info:
            verify_token(token)

        assert exc_info.value.status_code == 401

    def test_verify_token_invalid(self):
        """Test that invalid token raises exception."""
        with pytest.raises(HTTPException) as exc_info:
            verify_token("invalid_token")
        
        assert exc_info.value.status_code == 401
        assert "Could not validate credentials" in exc_info.value.detail


class TestRefreshToken:
    """Tests for refresh token functions."""

    def test_create_refresh_token_with_expiry(self):
        """Test creating refresh token with custom expiry."""
        data = {"sub": "user123"}
        expires_delta = timedelta(days=7)
        
        token = create_refresh_token(data, expires_delta)
        
        assert token is not None
        assert len(token) > 0

    def test_create_refresh_token_default_expiry(self):
        """Test creating refresh token with default expiry."""
        data = {"sub": "user123"}
        
        token = create_refresh_token(data)
        
        assert token is not None
        assert len(token) > 0

    def test_verify_refresh_token_valid(self):
        """Test that valid refresh token is verified correctly."""
        data = {"sub": "user123"}
        token = create_refresh_token(data, timedelta(days=7))
        
        payload = verify_refresh_token(token)
        
        assert payload["sub"] == "user123"
        assert payload["type"] == "refresh"

    def test_verify_refresh_token_invalid(self):
        """Test that invalid refresh token raises exception."""
        with pytest.raises(HTTPException) as exc_info:
            verify_refresh_token("invalid_token")
        
        assert exc_info.value.status_code == 401

    def test_verify_access_token_as_refresh_fails(self):
        """Test that access token cannot be used as refresh token."""
        data = {"sub": "user123"}
        access_token = create_access_token(data, timedelta(minutes=15))
        
        with pytest.raises(HTTPException) as exc_info:
            verify_refresh_token(access_token)
        
        assert exc_info.value.status_code == 401


class TestTokenGeneration:
    """Tests for token generation functions."""

    def test_generate_verification_code_default_length(self):
        """Test generating verification code with default length."""
        code = generate_verification_code()
        
        assert len(code) == 6
        assert code.isdigit()

    def test_generate_verification_code_custom_length(self):
        """Test generating verification code with custom length."""
        code = generate_verification_code(length=8)
        
        assert len(code) == 8
        assert code.isdigit()

    def test_generate_verification_code_uniqueness(self):
        """Test that generated codes are generally unique."""
        codes = [generate_verification_code() for _ in range(100)]
        # Not guaranteed to be all unique, but most should be
        unique_codes = set(codes)
        # Allow for some collisions but expect most to be unique
        assert len(unique_codes) > 80

    def test_generate_session_token(self):
        """Test generating session token."""
        token = generate_session_token()
        
        assert token is not None
        assert len(token) > 20

    def test_generate_session_token_uniqueness(self):
        """Test that session tokens are unique."""
        tokens = [generate_session_token() for _ in range(100)]
        unique_tokens = set(tokens)
        
        assert len(unique_tokens) == 100

    def test_generate_refresh_token_string(self):
        """Test generating refresh token string."""
        token = generate_refresh_token_string()
        
        assert token is not None
        assert len(token) > 40


class TestEmailVerificationToken:
    """Tests for email verification token functions."""

    def test_create_email_verification_token(self):
        """Test creating email verification token."""
        email = "test@example.com"
        
        token = create_email_verification_token(email)
        
        assert token is not None
        assert len(token) > 0

    def test_verify_email_verification_token_valid(self):
        """Test verifying valid email verification token."""
        email = "test@example.com"
        token = create_email_verification_token(email)
        
        result = verify_email_verification_token(token)
        
        assert result == email

    def test_verify_email_verification_token_invalid(self):
        """Test verifying invalid email verification token."""
        result = verify_email_verification_token("invalid_token")
        
        assert result is None


class TestPremiumUserCheck:
    """Tests for premium user check function."""

    def test_is_premium_user_admin(self):
        """Test that admin users are considered premium."""
        assert is_premium_user("admin") is True

    def test_is_premium_user_premium_no_expiry(self):
        """Test that premium user without expiry is considered premium (lifetime)."""
        assert is_premium_user("premium", None) is True

    def test_is_premium_user_premium_active(self):
        """Test that premium user with future expiry is considered premium."""
        future_date = datetime.now(timezone.utc) + timedelta(days=30)
        assert is_premium_user("premium", future_date) is True

    def test_is_premium_user_premium_expired(self):
        """Test that premium user with past expiry is not considered premium."""
        past_date = datetime.now(timezone.utc) - timedelta(days=1)
        assert is_premium_user("premium", past_date) is False

    def test_is_premium_user_free(self):
        """Test that free users are not considered premium."""
        assert is_premium_user("free") is False

    def test_is_premium_user_free_with_referral_access(self):
        referral_expiry = datetime.now(timezone.utc) + timedelta(days=3)
        assert is_premium_user("free", None, referral_expiry) is True


class TestDataAccess:
    """Tests for data access permission function."""

    def test_can_access_recent_data_admin(self):
        """Test that admin can access any data."""
        recent_date = datetime.now(timezone.utc)
        assert can_access_recent_data("admin", None, recent_date) is True

    def test_can_access_recent_data_premium(self):
        """Test that premium user can access recent data."""
        recent_date = datetime.now(timezone.utc)
        premium_expiry = datetime.now(timezone.utc) + timedelta(days=30)
        assert can_access_recent_data("premium", premium_expiry, recent_date) is True

    def test_can_access_recent_data_free_recent(self):
        """Test that free user cannot access recent data."""
        recent_date = datetime.now(timezone.utc)
        assert can_access_recent_data("free", None, recent_date) is False

    def test_can_access_recent_data_free_old(self):
        """Test that free user can access old data."""
        old_date = datetime.now(timezone.utc) - timedelta(days=settings.free_user_days_limit + 1)
        assert can_access_recent_data("free", None, old_date) is True

    def test_can_access_recent_data_no_date(self):
        """Test that access is denied when no date is provided for free user."""
        assert can_access_recent_data("free", None, None) is False
