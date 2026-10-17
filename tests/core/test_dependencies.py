"""Unit tests for the dependencies module."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException, Request

from app.core.dependencies import (
    get_current_user,
    get_current_active_user,
    get_optional_current_user,
    get_user_agent,
    get_client_ip,
)
from app.models.user import UserInDB


class TestGetUserAgent:
    """Tests for get_user_agent function."""

    @pytest.mark.asyncio
    async def test_get_user_agent_present(self):
        """Test getting user agent when header is present."""
        request = MagicMock(spec=Request)
        request.headers = {"user-agent": "Mozilla/5.0"}
        
        result = await get_user_agent(request)
        
        assert result == "Mozilla/5.0"

    @pytest.mark.asyncio
    async def test_get_user_agent_missing(self):
        """Test getting user agent when header is missing."""
        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get.return_value = "Unknown"
        
        result = await get_user_agent(request)
        
        assert result == "Unknown"


class TestGetClientIP:
    """Tests for get_client_ip function."""

    @pytest.mark.asyncio
    async def test_get_client_ip_from_x_forwarded_for(self):
        """Test getting client IP from X-Forwarded-For header."""
        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get.side_effect = lambda key: {
            "X-Forwarded-For": "192.168.1.1, 10.0.0.1",
            "X-Real-IP": None
        }.get(key)
        
        result = await get_client_ip(request)
        
        assert result == "192.168.1.1"

    @pytest.mark.asyncio
    async def test_get_client_ip_from_x_real_ip(self):
        """Test getting client IP from X-Real-IP header."""
        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get.side_effect = lambda key: {
            "X-Forwarded-For": None,
            "X-Real-IP": "192.168.1.2"
        }.get(key)
        
        result = await get_client_ip(request)
        
        assert result == "192.168.1.2"

    @pytest.mark.asyncio
    async def test_get_client_ip_from_client_host(self):
        """Test getting client IP from request client."""
        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get.return_value = None
        request.client = MagicMock()
        request.client.host = "192.168.1.3"
        
        result = await get_client_ip(request)
        
        assert result == "192.168.1.3"

    @pytest.mark.asyncio
    async def test_get_client_ip_fallback_unknown(self):
        """Test getting client IP fallback to Unknown."""
        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get.return_value = None
        # Mock client without host attribute to test fallback behavior
        request.client = MagicMock(spec=[])
        
        result = await get_client_ip(request)
        
        assert result == "Unknown"


class TestGetCurrentUser:
    """Tests for get_current_user function."""

    @pytest.mark.asyncio
    async def test_get_current_user_no_credentials(self):
        """Test that missing credentials raises 401."""
        request = MagicMock(spec=Request)
        
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request, credentials=None)
        
        assert exc_info.value.status_code == 401
        assert "Could not validate credentials" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_current_user_invalid_token(self):
        """Test that invalid token raises 401."""
        request = MagicMock(spec=Request)
        credentials = MagicMock()
        credentials.credentials = "invalid_token"
        
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request, credentials=credentials)
        
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user_valid_token(self, mock_user):
        """Test getting current user with valid token."""
        from app.core.security import create_access_token
        from datetime import timedelta
        
        request = MagicMock(spec=Request)
        credentials = MagicMock()
        
        # Create a valid token
        token = create_access_token(
            {"sub": mock_user.id, "email": mock_user.email},
            timedelta(minutes=15)
        )
        credentials.credentials = token
        
        with patch('app.core.dependencies.user_service') as mock_service:
            mock_service.get_user_by_id = AsyncMock(return_value=mock_user)
            
            result = await get_current_user(request, credentials=credentials)
            
            assert result == mock_user

    @pytest.mark.asyncio
    async def test_get_current_user_rejects_refresh_token(self, mock_user):
        """Refresh tokens must not grant access to protected routes."""
        from app.core.security import create_refresh_token
        from datetime import timedelta

        request = MagicMock(spec=Request)
        credentials = MagicMock()
        credentials.credentials = create_refresh_token(
            {"sub": mock_user.id}, timedelta(days=7)
        )

        with patch('app.core.dependencies.user_service') as mock_service:
            mock_service.get_user_by_id = AsyncMock(return_value=mock_user)

            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(request, credentials=credentials)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user_user_not_found(self, mock_user):
        """Test that missing user raises 401."""
        from app.core.security import create_access_token
        from datetime import timedelta
        
        request = MagicMock(spec=Request)
        credentials = MagicMock()
        
        token = create_access_token(
            {"sub": "nonexistent_id", "email": "test@example.com"},
            timedelta(minutes=15)
        )
        credentials.credentials = token
        
        with patch('app.core.dependencies.user_service') as mock_service:
            mock_service.get_user_by_id = AsyncMock(return_value=None)
            
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(request, credentials=credentials)
            
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user_inactive_user(self, mock_user):
        """Test that inactive user raises 403."""
        from app.core.security import create_access_token
        from datetime import timedelta
        
        request = MagicMock(spec=Request)
        credentials = MagicMock()
        
        # Create inactive user
        inactive_user = mock_user.model_copy()
        inactive_user.is_active = False
        
        token = create_access_token(
            {"sub": inactive_user.id, "email": inactive_user.email},
            timedelta(minutes=15)
        )
        credentials.credentials = token
        
        with patch('app.core.dependencies.user_service') as mock_service:
            mock_service.get_user_by_id = AsyncMock(return_value=inactive_user)
            
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(request, credentials=credentials)
            
            assert exc_info.value.status_code == 403
            assert "Inactive user" in exc_info.value.detail


class TestGetCurrentActiveUser:
    """Tests for get_current_active_user function."""

    @pytest.mark.asyncio
    async def test_get_current_active_user_active(self, mock_user):
        """Test getting active user."""
        result = await get_current_active_user(mock_user)
        
        assert result == mock_user

    @pytest.mark.asyncio
    async def test_get_current_active_user_inactive(self, mock_user):
        """Test that inactive user raises 400."""
        inactive_user = mock_user.model_copy()
        inactive_user.is_active = False
        
        with pytest.raises(HTTPException) as exc_info:
            await get_current_active_user(inactive_user)
        
        assert exc_info.value.status_code == 400
        assert "Inactive user" in exc_info.value.detail


class TestGetOptionalCurrentUser:
    """Tests for get_optional_current_user function."""

    @pytest.mark.asyncio
    async def test_get_optional_current_user_no_auth(self):
        """Test that missing auth returns None."""
        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get.return_value = None
        
        result = await get_optional_current_user(request)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_get_optional_current_user_invalid_scheme(self):
        """Test that non-bearer scheme returns None."""
        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get.return_value = "Basic dXNlcjpwYXNz"
        
        result = await get_optional_current_user(request)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_get_optional_current_user_invalid_token(self):
        """Test that invalid token returns None."""
        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get.return_value = "Bearer invalid_token"
        
        result = await get_optional_current_user(request)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_get_optional_current_user_valid_token(self, mock_user):
        """Test getting current user with valid token."""
        from app.core.security import create_access_token
        from datetime import timedelta
        
        token = create_access_token(
            {"sub": mock_user.id, "email": mock_user.email},
            timedelta(minutes=15)
        )
        
        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get.return_value = f"Bearer {token}"
        
        with patch('app.core.dependencies.user_service') as mock_service:
            mock_service.get_user_by_id = AsyncMock(return_value=mock_user)
            
            result = await get_optional_current_user(request)
            
            assert result == mock_user
