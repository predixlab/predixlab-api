"""Unit tests for the auth router."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from app.routers.auth import (
    request_registration_code, register_user, request_login_code, login_user,
    refresh_access_token, logout, logout_all_devices
)
from app.models.user import (
    UserInDB, UserRole, EmailLoginRequest, EmailRegisterCodeRequest,
    EmailLoginVerify, UserRegister, RefreshTokenRequest, Language
)


class TestRequestRegistrationCode:
    """Tests for request_registration_code endpoint."""

    @pytest.mark.asyncio
    async def test_request_registration_code_success(self):
        """Test requesting registration code successfully."""
        request_data = EmailRegisterCodeRequest(
            email="new@example.com",
            preferred_language=Language.ENGLISH
        )

        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.get_user_by_email = AsyncMock(return_value=None)
            mock_service.send_verification_code = AsyncMock(return_value=True)

            mock_request = MagicMock()
            result = await request_registration_code(
                request_data,
                mock_request,
                user_agent="Mozilla/5.0",
                client_ip="127.0.0.1"
            )

        assert result["email"] == "new@example.com"
        assert "Verification code sent" in result["message"]

    @pytest.mark.asyncio
    async def test_request_registration_code_user_exists(self):
        """Test requesting registration code when user already exists."""
        request_data = EmailRegisterCodeRequest(
            email="existing@example.com"
        )

        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.get_user_by_email = AsyncMock(return_value=MagicMock())

            mock_request = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await request_registration_code(
                    request_data,
                    mock_request,
                    user_agent="Mozilla/5.0",
                    client_ip="127.0.0.1"
                )

            assert exc_info.value.status_code == 400
            assert "already exists" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_request_registration_code_send_failure(self):
        """Test requesting registration code when sending fails."""
        request_data = EmailRegisterCodeRequest(
            email="new@example.com"
        )

        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.get_user_by_email = AsyncMock(return_value=None)
            mock_service.send_verification_code = AsyncMock(return_value=False)

            mock_request = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await request_registration_code(
                    request_data,
                    mock_request,
                    user_agent="Mozilla/5.0",
                    client_ip="127.0.0.1"
                )

            assert exc_info.value.status_code == 500


class TestRequestLoginCode:
    """Tests for request_login_code endpoint."""

    @pytest.mark.asyncio
    async def test_request_login_code_success(self, mock_user):
        """Test requesting login code successfully."""
        request_data = EmailLoginRequest(email="test@example.com")

        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.get_user_by_email = AsyncMock(return_value=mock_user)
            mock_service.send_verification_code = AsyncMock(return_value=True)

            mock_request = MagicMock()
            result = await request_login_code(
                request_data,
                mock_request,
                user_agent="Mozilla/5.0",
                client_ip="127.0.0.1"
            )

        assert result["email"] == "test@example.com"

    @pytest.mark.asyncio
    async def test_request_login_code_user_not_found(self):
        """Test requesting login code when user doesn't exist."""
        request_data = EmailLoginRequest(email="nonexistent@example.com")

        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.get_user_by_email = AsyncMock(return_value=None)

            mock_request = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await request_login_code(
                    request_data,
                    mock_request,
                    user_agent="Mozilla/5.0",
                    client_ip="127.0.0.1"
                )

            assert exc_info.value.status_code == 404


class TestLoginUser:
    """Tests for login_user endpoint."""

    @pytest.mark.asyncio
    async def test_login_user_success(self, mock_user):
        """Test logging in user successfully."""
        verify_data = EmailLoginVerify(
            email="test@example.com",
            code="123456"
        )

        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.verify_email_code = AsyncMock(return_value=True)
            mock_service.get_user_by_email = AsyncMock(return_value=mock_user)
            mock_service.update_last_login = AsyncMock()
            mock_service.create_refresh_token = AsyncMock(return_value=MagicMock(token="refresh_token"))

            with patch('app.routers.auth.create_access_token') as mock_create_token:
                mock_create_token.return_value = "access_token"

                mock_request = MagicMock()
                result = await login_user(
                    verify_data,
                    mock_request,
                    user_agent="Mozilla/5.0",
                    client_ip="127.0.0.1"
                )

        assert result.access_token == "access_token"
        assert result.refresh_token == "refresh_token"
        assert result.user.email == mock_user.email

    @pytest.mark.asyncio
    async def test_login_user_invalid_code(self):
        """Test logging in with invalid code."""
        verify_data = EmailLoginVerify(
            email="test@example.com",
            code="wrong_code"
        )

        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.verify_email_code = AsyncMock(return_value=False)

            mock_request = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await login_user(
                    verify_data,
                    mock_request,
                    user_agent="Mozilla/5.0",
                    client_ip="127.0.0.1"
                )

            assert exc_info.value.status_code == 400
            assert "Invalid or expired" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_login_user_inactive_user(self, mock_user):
        """Test logging in when user is inactive."""
        mock_user.is_active = False

        verify_data = EmailLoginVerify(
            email="test@example.com",
            code="123456"
        )

        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.verify_email_code = AsyncMock(return_value=True)
            mock_service.get_user_by_email = AsyncMock(return_value=mock_user)

            mock_request = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await login_user(
                    verify_data,
                    mock_request,
                    user_agent="Mozilla/5.0",
                    client_ip="127.0.0.1"
                )

            assert exc_info.value.status_code == 400
            assert "inactive" in exc_info.value.detail


class TestRefreshToken:
    """Tests for refresh_access_token endpoint."""

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, mock_user):
        """Test refreshing token successfully."""
        refresh_request = RefreshTokenRequest(refresh_token="valid_token")

        mock_refresh_token = MagicMock()
        mock_refresh_token.user_id = mock_user.id

        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.get_refresh_token = AsyncMock(return_value=mock_refresh_token)
            mock_service.get_user_by_id = AsyncMock(return_value=mock_user)
            mock_service.revoke_refresh_token = AsyncMock()
            mock_service.create_refresh_token = AsyncMock(return_value=MagicMock(token="new_refresh_token"))

            with patch('app.routers.auth.create_access_token') as mock_create_token:
                mock_create_token.return_value = "new_access_token"

                mock_request = MagicMock()
                result = await refresh_access_token(
                    refresh_request,
                    mock_request,
                    user_agent="Mozilla/5.0",
                    client_ip="127.0.0.1"
                )

        assert result.access_token == "new_access_token"
        assert result.refresh_token == "new_refresh_token"

    @pytest.mark.asyncio
    async def test_refresh_token_invalid(self):
        """Test refreshing with invalid token."""
        refresh_request = RefreshTokenRequest(refresh_token="invalid_token")

        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.get_refresh_token = AsyncMock(return_value=None)

            mock_request = MagicMock()
            with pytest.raises(HTTPException) as exc_info:
                await refresh_access_token(
                    refresh_request,
                    mock_request,
                    user_agent="Mozilla/5.0",
                    client_ip="127.0.0.1"
                )

            assert exc_info.value.status_code == 401


class TestLogout:
    """Tests for logout endpoint."""

    @pytest.mark.asyncio
    async def test_logout_success(self, mock_user):
        """Test logging out successfully."""
        refresh_request = RefreshTokenRequest(refresh_token="token_to_revoke")

        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.revoke_refresh_token = AsyncMock()

            mock_request = MagicMock()
            result = await logout(
                refresh_request,
                mock_user,
                mock_request,
                client_ip="127.0.0.1"
            )

        assert "Successfully logged out" in result["message"]

    @pytest.mark.asyncio
    async def test_logout_without_refresh_token(self, mock_user):
        """Test logging out without providing refresh token."""
        mock_request = MagicMock()
        result = await logout(
            None,
            mock_user,
            mock_request,
            client_ip="127.0.0.1"
        )

        assert "Successfully logged out" in result["message"]


class TestLogoutAllDevices:
    """Tests for logout_all_devices endpoint."""

    @pytest.mark.asyncio
    async def test_logout_all_devices_success(self, mock_user):
        """Test logging out from all devices successfully."""
        with patch('app.routers.auth.user_service') as mock_service:
            mock_service.revoke_all_user_refresh_tokens = AsyncMock(return_value=5)

            result = await logout_all_devices(
                mock_user,
                client_ip="127.0.0.1"
            )

        assert result["revoked_tokens"] == 5
        assert "all devices" in result["message"]
