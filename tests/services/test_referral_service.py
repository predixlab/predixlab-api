from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.models.referral import ReferralStatus
from app.services.referral_service import ReferralService


class AsyncCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, *_args):
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __aiter__(self):
        async def iterate():
            for document in self.documents:
                yield document

        return iterate()


@pytest.mark.asyncio
async def test_track_click_is_idempotent_for_visitor(mock_db):
    service = ReferralService()
    service.db = mock_db
    mock_db.users.find_one = AsyncMock(
        return_value={
            "_id": "inviter",
            "email": "owner@example.com",
            "referral_rewards_granted": 0,
        }
    )
    mock_db.referral_clicks.update_one = AsyncMock()

    result = await service.track_click("abcd1234", "visitor-12345678")

    assert result.valid is True
    assert result.invitee_reward_days == settings.referral_invitee_reward_days
    query = mock_db.referral_clicks.update_one.call_args.args[0]
    assert query == {
        "referral_code": "ABCD1234",
        "visitor_id": "visitor-12345678",
    }
    assert mock_db.referral_clicks.update_one.call_args.kwargs["upsert"] is True


@pytest.mark.asyncio
async def test_qualify_referral_rewards_both_users_once(mock_db):
    service = ReferralService()
    service.db = mock_db
    referral = {
        "_id": "referral-1",
        "inviter_id": "inviter",
        "invitee_id": "invitee",
        "referral_code": "CODE1234",
        "status": ReferralStatus.PENDING,
        "created_at": datetime.now(timezone.utc),
    }
    mock_db.referrals.find_one = AsyncMock(return_value=referral)
    mock_db.referrals.update_one = AsyncMock()
    mock_db.users.update_one = AsyncMock()
    service._reserve_reward_slot = AsyncMock(return_value=True)
    service._apply_reward = AsyncMock()

    await service.qualify_referral("inviter", "invitee", "code1234")

    assert service._apply_reward.await_count == 2
    service._apply_reward.assert_any_await(
        "inviter",
        "referrer:referral-1",
        settings.referral_inviter_reward_days,
    )
    service._apply_reward.assert_any_await(
        "invitee",
        "invitee:referral-1",
        settings.referral_invitee_reward_days,
    )
    referral_update = mock_db.referrals.update_one.call_args.args[1]["$set"]
    assert referral_update["status"] == ReferralStatus.REWARDED


@pytest.mark.asyncio
async def test_reward_is_deferred_during_active_paid_subscription(mock_db):
    service = ReferralService()
    service.db = mock_db
    paid_expiry = datetime.now(timezone.utc) + timedelta(days=30)
    mock_db.users.find_one = AsyncMock(
        return_value={
            "_id": "user",
            "role": "premium",
            "premium_expires_at": paid_expiry,
            "referral_access_expires_at": None,
            "referral_reward_seconds_balance": 0,
            "applied_referral_rewards": [],
            "stripe_subscription_id": "sub_123",
            "subscription_status": "active",
        }
    )
    mock_db.users.update_one = AsyncMock(
        return_value=MagicMock(modified_count=1)
    )

    await service._apply_reward("user", "reward-1", 7)

    update = mock_db.users.update_one.call_args.args[1]
    assert update["$inc"]["referral_reward_seconds_balance"] == 7 * 86400
    assert update["$addToSet"]["applied_referral_rewards"] == "reward-1"


@pytest.mark.asyncio
async def test_reward_extends_immediately_without_paid_subscription(mock_db):
    service = ReferralService()
    service.db = mock_db
    existing_expiry = datetime.now(timezone.utc) + timedelta(days=3)
    mock_db.users.find_one = AsyncMock(
        return_value={
            "_id": "user",
            "role": "free",
            "premium_expires_at": None,
            "referral_access_expires_at": existing_expiry,
            "referral_reward_seconds_balance": 0,
            "applied_referral_rewards": [],
            "stripe_subscription_id": None,
            "subscription_status": None,
        }
    )
    mock_db.users.update_one = AsyncMock(
        return_value=MagicMock(modified_count=1)
    )

    await service._apply_reward("user", "reward-1", 7)

    update = mock_db.users.update_one.call_args.args[1]
    assert update["$set"]["referral_access_expires_at"] == (
        existing_expiry + timedelta(days=7)
    )


@pytest.mark.asyncio
async def test_summary_reports_funnel_without_invitee_identity(mock_db, mock_user):
    service = ReferralService()
    service.db = mock_db
    service.get_or_create_code = AsyncMock(return_value="CODE1234")
    mock_db.referral_clicks.count_documents = AsyncMock(return_value=10)
    mock_db.referrals.count_documents = AsyncMock(
        side_effect=[4, 3, 1]
    )
    mock_db.referrals.find.return_value = AsyncCursor(
        [
            {
                "created_at": datetime.now(timezone.utc),
                "status": ReferralStatus.REWARDED,
            }
        ]
    )

    summary = await service.get_summary(
        mock_user.model_copy(
            update={"referral_reward_seconds_balance": 15 * 86400}
        )
    )

    assert summary.clicks == 10
    assert summary.signups == 4
    assert summary.successful_referrals == 3
    assert summary.conversion_rate == 40.0
    assert summary.reward_days_earned == (
        3 * settings.referral_inviter_reward_days
    )
    assert summary.deferred_reward_days == 15
    assert summary.recent_activity[0].reward_days == (
        settings.referral_inviter_reward_days
    )
