from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.user import SubscriptionStatus
from app.services.stripe_service import StripeService


@pytest.mark.asyncio
async def test_paid_subscription_freezes_active_referral_access(mock_db):
    service = StripeService()
    service.db = mock_db
    referral_expiry = datetime.now(timezone.utc) + timedelta(days=7)
    mock_db.users.find_one = AsyncMock(
        return_value={
            "_id": "user",
            "role": "free",
            "premium_expires_at": None,
            "referral_access_expires_at": referral_expiry,
        }
    )
    mock_db.users.update_one = AsyncMock(
        return_value=MagicMock(matched_count=1)
    )

    await service._set_paid_subscription_state(
        "user",
        {
            "role": "premium",
            "subscription_status": SubscriptionStatus.ACTIVE,
            "premium_expires_at": datetime.now(timezone.utc)
            + timedelta(days=30),
        },
    )

    update = mock_db.users.update_one.call_args.args[1]
    frozen_seconds = update["$inc"]["referral_reward_seconds_balance"]
    assert 6.99 * 86400 <= frozen_seconds <= 7 * 86400
    assert update["$unset"] == {"referral_access_expires_at": ""}


@pytest.mark.asyncio
async def test_paid_renewal_freezes_only_referral_tail(mock_db):
    service = StripeService()
    service.db = mock_db
    paid_expiry = datetime.now(timezone.utc) + timedelta(days=30)
    referral_expiry = paid_expiry + timedelta(days=15)
    mock_db.users.find_one = AsyncMock(
        return_value={
            "_id": "user",
            "role": "premium",
            "premium_expires_at": paid_expiry,
            "referral_access_expires_at": referral_expiry,
        }
    )
    mock_db.users.update_one = AsyncMock(
        return_value=MagicMock(matched_count=1)
    )

    await service._set_paid_subscription_state(
        "user",
        {
            "role": "premium",
            "subscription_status": SubscriptionStatus.ACTIVE,
            "premium_expires_at": paid_expiry + timedelta(days=30),
        },
    )

    update = mock_db.users.update_one.call_args.args[1]
    assert (
        update["$inc"]["referral_reward_seconds_balance"]
        == 15 * 86400
    )


@pytest.mark.asyncio
async def test_inactive_subscription_activates_deferred_balance(mock_db):
    service = StripeService()
    service.db = mock_db
    paid_expiry = datetime.now(timezone.utc) + timedelta(days=2)
    mock_db.users.find_one = AsyncMock(
        return_value={
            "_id": "user",
            "premium_expires_at": paid_expiry,
            "referral_access_expires_at": None,
            "referral_reward_seconds_balance": 15 * 86400,
        }
    )
    mock_db.users.update_one = AsyncMock(
        return_value=MagicMock(matched_count=1)
    )

    await service._set_inactive_subscription_state(
        "user",
        {
            "role": "free",
            "subscription_status": SubscriptionStatus.CANCELED,
            "premium_expires_at": paid_expiry,
        },
    )

    update = mock_db.users.update_one.call_args.args[1]["$set"]
    assert update["referral_access_expires_at"] == (
        paid_expiry + timedelta(days=15)
    )
    assert update["referral_reward_seconds_balance"] == 0


@pytest.mark.asyncio
async def test_subscription_webhook_uses_balance_transition_helpers():
    service = StripeService()
    service._set_paid_subscription_state = AsyncMock()
    service._set_inactive_subscription_state = AsyncMock()

    await service.handle_subscription_updated(
        {
            "id": "sub_active",
            "metadata": {"user_id": "user"},
            "status": "active",
            "current_period_end": 1_800_000_000,
        }
    )
    service._set_paid_subscription_state.assert_awaited_once()
    service._set_inactive_subscription_state.assert_not_awaited()

    service._set_paid_subscription_state.reset_mock()
    await service.handle_subscription_updated(
        {
            "id": "sub_canceled",
            "metadata": {"user_id": "user"},
            "status": "canceled",
            "current_period_end": 1_800_000_000,
        }
    )
    service._set_inactive_subscription_state.assert_awaited_once()
    service._set_paid_subscription_state.assert_not_awaited()
