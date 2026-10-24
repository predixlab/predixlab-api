"""Unit tests for the user router."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from app.config import settings
from app.routers.user import (
    accept_user_notice,
    delete_account,
    get_user_profile,
    unsubscribe_daily_signals_email,
    unsubscribe_daily_signals_email_one_click,
    unsubscribe_marketing_email,
    unsubscribe_marketing_email_one_click,
    update_user_profile,
)
from app.models.user import (
    AccountDeletionRequest,
    Language,
    UserInDB,
    UserNoticeAcceptanceRequest,
    UserRole,
    UserUpdate,
)
from app.core.email import email_service
from app.services.user_service import UserService


class TestGetUserProfile:
    """Tests for get_user_profile endpoint."""

    @pytest.mark.asyncio
    async def test_get_user_profile_success(self, mock_user):
        """Test getting user profile successfully."""
        result = await get_user_profile(mock_user)

        assert result.id == mock_user.id
        assert result.email == mock_user.email
        assert result.name == mock_user.name
        assert result.role == mock_user.role

    @pytest.mark.asyncio
    async def test_get_user_profile_premium_user(self, mock_premium_user):
        """Test getting premium user profile."""
        result = await get_user_profile(mock_premium_user)

        assert result.id == mock_premium_user.id
        assert result.email == mock_premium_user.email
        assert result.role == UserRole.PREMIUM
        assert result.premium_expires_at is not None
        assert result.daily_signals_email_enabled is True

    @pytest.mark.asyncio
    async def test_free_user_daily_signals_email_is_disabled(self, mock_user):
        result = await get_user_profile(mock_user)

        assert result.daily_signals_email_enabled is False


class TestUpdateUserProfile:
    """Tests for update_user_profile endpoint."""

    @pytest.mark.asyncio
    async def test_update_user_profile_name(self, mock_user):
        """Test updating user name."""
        update_data = UserUpdate(name="New Name")

        with patch('app.routers.user.user_service') as mock_service:
            updated_user = mock_user.model_copy()
            updated_user.name = "New Name"
            mock_service.update_user = AsyncMock(return_value=updated_user)

            result = await update_user_profile(update_data, mock_user)

        assert result.name == "New Name"

    @pytest.mark.asyncio
    async def test_update_user_profile_language(self, mock_user):
        """Test updating user preferred language."""
        update_data = UserUpdate(preferred_language=Language.CHINESE)

        with patch('app.routers.user.user_service') as mock_service:
            updated_user = mock_user.model_copy()
            updated_user.preferred_language = Language.CHINESE
            mock_service.update_user = AsyncMock(return_value=updated_user)

            result = await update_user_profile(update_data, mock_user)

        assert result.preferred_language == Language.CHINESE

    @pytest.mark.asyncio
    async def test_premium_user_can_update_daily_signals_email(
        self,
        mock_premium_user,
    ):
        update_data = UserUpdate(daily_signals_email_enabled=False)

        with patch('app.routers.user.user_service') as mock_service:
            updated_user = mock_premium_user.model_copy(
                update={"daily_signals_email_enabled": False}
            )
            mock_service.update_user = AsyncMock(return_value=updated_user)

            result = await update_user_profile(
                update_data,
                mock_premium_user,
            )

        assert result.daily_signals_email_enabled is False
        mock_service.update_user.assert_awaited_once_with(
            mock_premium_user.id,
            {"daily_signals_email_enabled": False},
        )

    @pytest.mark.asyncio
    async def test_free_user_cannot_update_daily_signals_email(
        self,
        mock_user,
    ):
        update_data = UserUpdate(daily_signals_email_enabled=True)

        with patch('app.routers.user.user_service') as mock_service:
            with pytest.raises(HTTPException) as exc_info:
                await update_user_profile(update_data, mock_user)

        assert exc_info.value.status_code == 403
        mock_service.update_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_user_profile_failure(self, mock_user):
        """Test update failure raises exception."""
        update_data = UserUpdate(name="New Name")

        with patch('app.routers.user.user_service') as mock_service:
            mock_service.update_user = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await update_user_profile(update_data, mock_user)

            assert exc_info.value.status_code == 500
            assert "Failed to update" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_update_user_profile_empty_update(self, mock_user):
        """Test update with no changes."""
        update_data = UserUpdate()

        with patch('app.routers.user.user_service') as mock_service:
            mock_service.update_user = AsyncMock(return_value=mock_user)

            result = await update_user_profile(update_data, mock_user)

        assert result.id == mock_user.id


class TestAcceptUserNotice:
    """Tests for versioned notice acceptance."""

    @pytest.mark.asyncio
    async def test_accept_user_notice_success(self, mock_user):
        updated_user = mock_user.model_copy(
            update={"user_notice_version": settings.user_notice_version}
        )
        acceptance = UserNoticeAcceptanceRequest(
            user_notice_version=settings.user_notice_version
        )

        with patch("app.routers.user.user_service") as mock_service:
            mock_service.accept_user_notice = AsyncMock(return_value=updated_user)

            result = await accept_user_notice(acceptance, current_user=mock_user)

        mock_service.accept_user_notice.assert_awaited_once_with(
            mock_user.id,
            settings.user_notice_version,
        )
        assert result.user_notice_version == settings.user_notice_version


class TestMarketingEmailUnsubscribe:
    @pytest.mark.asyncio
    async def test_signed_unsubscribe_disables_optional_email(self, mock_user):
        token = email_service.create_unsubscribe_url(
            mock_user.id,
            mock_user.preferred_language,
        ).split("token=", 1)[1]

        with patch("app.routers.user.user_service") as mock_service:
            mock_service.unsubscribe_marketing_emails = AsyncMock(return_value=True)
            response = await unsubscribe_marketing_email_one_click(token)

        mock_service.unsubscribe_marketing_emails.assert_awaited_once_with(mock_user.id)
        assert response.status_code == 200
        assert b"Optional product emails are now disabled" in response.body

    @pytest.mark.asyncio
    async def test_browser_link_requires_confirmation(self, mock_user):
        token = email_service.create_unsubscribe_url(
            mock_user.id,
            mock_user.preferred_language,
        ).split("token=", 1)[1]

        with patch("app.routers.user.user_service") as mock_service:
            mock_service.unsubscribe_marketing_emails = AsyncMock(return_value=True)
            response = await unsubscribe_marketing_email(token)

        mock_service.unsubscribe_marketing_emails.assert_not_awaited()
        assert response.status_code == 200
        assert b"Stop product emails" in response.body

    @pytest.mark.asyncio
    async def test_invalid_unsubscribe_token_is_rejected(self):
        response = await unsubscribe_marketing_email("invalid")

        assert response.status_code == 400
        assert b"Invalid unsubscribe link" in response.body


class TestDailySignalsEmailUnsubscribe:
    @pytest.mark.asyncio
    async def test_signed_unsubscribe_disables_only_daily_signals(
        self,
        mock_premium_user,
    ):
        token = email_service.daily_signals_unsubscribe_serializer.dumps(
            {
                "user_id": mock_premium_user.id,
                "language": mock_premium_user.preferred_language.value,
            }
        )

        with patch("app.routers.user.user_service") as mock_service:
            mock_service.unsubscribe_daily_signals_emails = AsyncMock(
                return_value=True
            )
            response = await unsubscribe_daily_signals_email_one_click(token)

        mock_service.unsubscribe_daily_signals_emails.assert_awaited_once_with(
            mock_premium_user.id
        )
        mock_service.unsubscribe_marketing_emails.assert_not_called()
        assert response.status_code == 200
        assert b"Daily Signals emails disabled" in response.body

    @pytest.mark.asyncio
    async def test_daily_signals_browser_link_requires_confirmation(
        self,
        mock_premium_user,
    ):
        token = email_service.daily_signals_unsubscribe_serializer.dumps(
            {
                "user_id": mock_premium_user.id,
                "language": mock_premium_user.preferred_language.value,
            }
        )

        with patch("app.routers.user.user_service") as mock_service:
            response = await unsubscribe_daily_signals_email(token)

        mock_service.unsubscribe_daily_signals_emails.assert_not_called()
        assert response.status_code == 200
        assert b"Stop Daily Signals emails" in response.body


class TestDeleteAccount:
    @pytest.mark.asyncio
    async def test_delete_account_ends_billing_then_deletes_data(self, mock_user):
        request = AccountDeletionRequest(confirmation=mock_user.email)

        with (
            patch("app.routers.user.stripe_service") as mock_stripe,
            patch("app.routers.user.user_service") as mock_service,
        ):
            mock_stripe.delete_customer_account = AsyncMock()
            mock_service.delete_account_data = AsyncMock(return_value=True)

            response = await delete_account(request, current_user=mock_user)

        mock_stripe.delete_customer_account.assert_awaited_once_with(mock_user)
        mock_service.delete_account_data.assert_awaited_once_with(mock_user)
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_account_requires_matching_email(self, mock_user):
        request = AccountDeletionRequest(confirmation="other@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await delete_account(request, current_user=mock_user)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_account_keeps_local_data_when_billing_fails(self, mock_user):
        request = AccountDeletionRequest(confirmation=mock_user.email)

        with (
            patch("app.routers.user.stripe_service") as mock_stripe,
            patch("app.routers.user.user_service") as mock_service,
        ):
            mock_stripe.delete_customer_account = AsyncMock(
                side_effect=RuntimeError("Stripe unavailable")
            )
            mock_service.delete_account_data = AsyncMock(return_value=True)

            with pytest.raises(HTTPException) as exc_info:
                await delete_account(request, current_user=mock_user)

        assert exc_info.value.status_code == 502
        mock_service.delete_account_data.assert_not_awaited()


class TestUserDataDeletion:
    @pytest.mark.asyncio
    async def test_deletes_all_user_owned_collections(self, mock_user):
        service = UserService()
        service.db = MagicMock()
        collection_names = (
            "refresh_tokens",
            "agent_api_keys",
            "user_sessions",
            "login_codes",
            "verification_codes",
            "oauth_tokens",
            "watchlists",
            "alerts",
            "screener_strategies",
            "referrals",
        )
        for name in collection_names:
            getattr(service.db, name).delete_many = AsyncMock()

        service.db.users.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=1)
        )
        service.db.agent_api_key_quotas.delete_one = AsyncMock()

        deleted = await service.delete_account_data(mock_user)

        assert deleted is True
        service.db.refresh_tokens.delete_many.assert_awaited_once_with(
            {"user_id": mock_user.id}
        )
        service.db.agent_api_keys.delete_many.assert_awaited_once_with(
            {"user_id": mock_user.id}
        )
        service.db.agent_api_key_quotas.delete_one.assert_awaited_once_with(
            {"_id": mock_user.id}
        )
        service.db.user_sessions.delete_many.assert_awaited_once_with(
            {"user_id": mock_user.id}
        )
        service.db.login_codes.delete_many.assert_awaited_once_with(
            {"user_id": mock_user.id}
        )
        service.db.verification_codes.delete_many.assert_awaited_once_with(
            {"email": mock_user.email}
        )
        service.db.oauth_tokens.delete_many.assert_awaited_once_with(
            {"user_info.email": mock_user.email}
        )
        service.db.watchlists.delete_many.assert_awaited_once_with(
            {"user_id": mock_user.id}
        )
        service.db.alerts.delete_many.assert_awaited_once_with(
            {"user_id": mock_user.id}
        )
        service.db.screener_strategies.delete_many.assert_awaited_once_with(
            {"user_id": mock_user.id}
        )
        service.db.users.delete_one.assert_awaited_once_with({"_id": mock_user.id})
