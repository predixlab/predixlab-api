import stripe
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import math
from app.config import settings
from app.models.user import BillingInterval, SubscriptionStatus, UserInDB, Language
from app.database import get_database
from app.core.email import email_service
import logging

logger = logging.getLogger(__name__)
SECONDS_PER_DAY = 24 * 60 * 60

# Initialize Stripe
stripe.api_key = settings.stripe_secret_key

class StripeService:
    def __init__(self):
        self.db = None

    async def initialize(self):
        if self.db is None:
            self.db = await get_database()

    @staticmethod
    def _utc(value: Optional[datetime]) -> Optional[datetime]:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _optional_datetime_filter(
        field: str,
        value: Optional[datetime],
    ) -> Dict[str, Any]:
        if value is None:
            return {
                "$or": [
                    {field: {"$exists": False}},
                    {field: None},
                ]
            }
        return {field: value}

    async def _set_paid_subscription_state(
        self,
        user_id: str,
        update_data: Dict[str, Any],
    ) -> None:
        """Freeze unused referral access while Stripe access is active."""
        for _ in range(8):
            user = await self.db.users.find_one(
                {"_id": user_id},
                {
                    "role": 1,
                    "premium_expires_at": 1,
                    "referral_access_expires_at": 1,
                },
            )
            if not user:
                raise RuntimeError(f"Subscription user {user_id} not found")

            now = datetime.now(timezone.utc)
            paid_expires_at = self._utc(user.get("premium_expires_at"))
            referral_expires_at = self._utc(
                user.get("referral_access_expires_at")
            )
            referral_boundary = now
            if (
                user.get("role") == "premium"
                and paid_expires_at is not None
                and paid_expires_at > referral_boundary
            ):
                referral_boundary = paid_expires_at

            remaining_seconds = 0
            if (
                referral_expires_at is not None
                and referral_expires_at > referral_boundary
            ):
                remaining_seconds = math.ceil(
                    (referral_expires_at - referral_boundary).total_seconds()
                )

            query: Dict[str, Any] = {"_id": user_id}
            query.update(
                self._optional_datetime_filter(
                    "referral_access_expires_at",
                    referral_expires_at,
                )
            )
            update: Dict[str, Any] = {"$set": update_data}
            if referral_expires_at is not None:
                update["$unset"] = {"referral_access_expires_at": ""}
            if remaining_seconds:
                update["$inc"] = {
                    "referral_reward_seconds_balance": remaining_seconds
                }

            result = await self.db.users.update_one(query, update)
            if result.matched_count:
                return

        raise RuntimeError(
            f"Unable to freeze referral access for subscription user {user_id}"
        )

    async def _set_inactive_subscription_state(
        self,
        user_id: str,
        update_data: Dict[str, Any],
    ) -> None:
        """Activate deferred referral rewards after paid Stripe access ends."""
        for _ in range(8):
            user = await self.db.users.find_one(
                {"_id": user_id},
                {
                    "premium_expires_at": 1,
                    "referral_access_expires_at": 1,
                    "referral_reward_seconds_balance": 1,
                },
            )
            if not user:
                raise RuntimeError(f"Subscription user {user_id} not found")

            now = datetime.now(timezone.utc)
            referral_expires_at = self._utc(
                user.get("referral_access_expires_at")
            )
            balance = int(user.get("referral_reward_seconds_balance", 0))
            paid_expires_at = self._utc(
                update_data.get("premium_expires_at")
                or user.get("premium_expires_at")
            )

            conditions = [
                self._optional_datetime_filter(
                    "referral_access_expires_at",
                    referral_expires_at,
                )
            ]
            if balance:
                conditions.append(
                    {"referral_reward_seconds_balance": balance}
                )
            else:
                conditions.append(
                    {
                        "$or": [
                            {
                                "referral_reward_seconds_balance": {
                                    "$exists": False
                                }
                            },
                            {"referral_reward_seconds_balance": 0},
                        ]
                    }
                )

            query: Dict[str, Any] = {
                "_id": user_id,
                "$and": conditions,
            }
            final_update = dict(update_data)
            if balance:
                base = max(
                    value
                    for value in (
                        now,
                        paid_expires_at,
                        referral_expires_at,
                    )
                    if value is not None
                )
                final_update["referral_access_expires_at"] = (
                    base + timedelta(seconds=balance)
                )
                final_update["referral_reward_seconds_balance"] = 0

            result = await self.db.users.update_one(
                query,
                {"$set": final_update},
            )
            if result.matched_count:
                return

        raise RuntimeError(
            f"Unable to activate referral balance for user {user_id}"
        )

    async def create_or_get_customer(self, user: UserInDB) -> str:
        """Create or retrieve a Stripe customer for the user"""
        try:
            # If user already has a Stripe customer ID, return it
            if user.stripe_customer_id:
                # Verify the customer still exists in Stripe
                try:
                    customer = stripe.Customer.retrieve(user.stripe_customer_id)
                    return customer.id
                except stripe.error.InvalidRequestError:
                    # Customer doesn't exist, create a new one
                    pass

            # Create new customer
            customer = stripe.Customer.create(
                email=user.email,
                name=user.name,
                metadata={
                    "user_id": user.id,
                    "app_name": settings.app_name
                }
            )

            await self.db.users.update_one(
                {"_id": user.id},
                {"$set": {"stripe_customer_id": customer.id}}
            )

            return customer.id

        except Exception as e:
            logger.error(f"Error creating/retrieving Stripe customer: {e}")
            raise

    async def create_checkout_session(
        self,
        user: UserInDB,
        billing_interval: BillingInterval,
        success_url: str,
        cancel_url: str
    ) -> Dict[str, str]:
        """Create a Stripe checkout session with free trial (only for first-time subscribers)"""
        try:
            customer_id = await self.create_or_get_customer(user)

            # Determine price ID based on billing interval
            price_id = (
                settings.stripe_monthly_price_id
                if billing_interval == BillingInterval.MONTHLY
                else settings.stripe_yearly_price_id
            )

            if not price_id:
                raise ValueError(f"Price ID not configured for {billing_interval} billing")

            # Check if user has ever used trial before
            # User has used trial if they have:
            # 1. trial_end field (previously had a trial), OR
            # 2. stripe_subscription_id (had a subscription before), OR
            # 3. Check Stripe customer's subscription history
            has_used_trial = False
            
            if user.trial_end or user.stripe_subscription_id:
                has_used_trial = True
            else:
                # Check if customer has any past subscriptions in Stripe
                try:
                    subscriptions = stripe.Subscription.list(
                        customer=customer_id,
                        limit=1
                    )
                    if subscriptions.data:
                        has_used_trial = True
                except Exception as e:
                    logger.warning(f"Error checking customer subscription history: {e}")

            # Create checkout session
            session_params = {
                'customer': customer_id,
                'payment_method_types': ['card'],
                'line_items': [{
                    'price': price_id,
                    'quantity': 1,
                }],
                'mode': 'subscription',
                'success_url': success_url,
                'cancel_url': cancel_url,
                'subscription_data': {
                    'metadata': {
                        'user_id': user.id,
                        'billing_interval': billing_interval.value
                    }
                },
                'metadata': {
                    'user_id': user.id,
                    'billing_interval': billing_interval.value
                }
            }

            # Only add trial period for first-time subscribers
            if not has_used_trial and settings.stripe_trial_period_days > 0:
                session_params['subscription_data']['trial_period_days'] = settings.stripe_trial_period_days
                logger.info(f"Creating checkout session with {settings.stripe_trial_period_days} days trial for user {user.id}")
            else:
                logger.info(f"Creating checkout session without trial for user {user.id} (has_used_trial={has_used_trial})")

            session = stripe.checkout.Session.create(**session_params)

            return {
                "checkout_url": session.url,
                "session_id": session.id
            }

        except Exception as e:
            logger.error(f"Error creating checkout session: {e}")
            raise

    async def handle_checkout_completed(self, session: Dict[str, Any]) -> None:
        """Handle successful checkout completion"""
        try:
            user_id = session.get('metadata', {}).get('user_id')
            if not user_id:
                logger.error("No user_id found in checkout session metadata")
                return

            subscription_id = session.get('subscription')
            if not subscription_id:
                logger.error("No subscription_id found in checkout session")
                return

            # Get subscription details from Stripe
            subscription = stripe.Subscription.retrieve(subscription_id)

            # Calculate trial end and premium expiration
            trial_end = None
            premium_expires_at = None

            if subscription.trial_end:
                trial_end = datetime.fromtimestamp(subscription.trial_end, tz=timezone.utc)
                premium_expires_at = trial_end
            else:
                # If no trial, set expiration to current period end
                premium_expires_at = datetime.fromtimestamp(
                    subscription.current_period_end,
                    tz=timezone.utc
                )

            # Determine billing interval from price
            billing_interval = BillingInterval.MONTHLY
            for item in subscription['items']['data']:
                if item.price.id == settings.stripe_yearly_price_id:
                    billing_interval = BillingInterval.YEARLY
                    break

            update_data = {
                "stripe_subscription_id": subscription_id,
                "subscription_status": SubscriptionStatus.TRIALING if subscription.status == 'trialing' else SubscriptionStatus.ACTIVE,
                "billing_interval": billing_interval,
                "premium_expires_at": premium_expires_at,
                "role": "premium",
                "updated_at": datetime.now(timezone.utc)
            }

            if trial_end:
                update_data["trial_end"] = trial_end

            await self._set_paid_subscription_state(user_id, update_data)

            logger.info(f"User {user_id} subscription activated successfully")

            # Send subscription success email
            try:
                user_doc = await self.db.users.find_one({"_id": user_id})
                if user_doc:
                    user_language = user_doc.get("language", Language.ENGLISH)

                    # Format billing interval for display
                    billing_interval_display = "Monthly" if billing_interval == BillingInterval.MONTHLY else "Yearly"
                    if user_language == Language.CHINESE:
                        billing_interval_display = "月付" if billing_interval == BillingInterval.MONTHLY else "年付"

                    # Format dates for display
                    trial_end_str = trial_end.strftime("%Y-%m-%d %H:%M UTC") if trial_end else None
                    next_billing_str = premium_expires_at.strftime("%Y-%m-%d %H:%M UTC") if premium_expires_at else None

                    await email_service.send_subscription_success_email(
                        to_email=user_doc.get("email"),
                        user_id=user_id,
                        user_name=user_doc.get("name"),
                        billing_interval=billing_interval_display,
                        trial_end=trial_end_str,
                        next_billing_date=next_billing_str,
                        language=user_language
                    )
                    logger.info(f"Subscription success email sent to user {user_id}")
            except Exception as email_error:
                logger.error(f"Failed to send subscription success email to user {user_id}: {email_error}")

        except Exception as e:
            logger.error(f"Error handling checkout completion: {e}")
            raise

    async def handle_invoice_paid(self, invoice: Dict[str, Any]) -> None:
        """Handle successful invoice payment"""
        try:
            subscription_id = invoice.get('subscription')
            if not subscription_id:
                logger.error("No subscription_id found in invoice")
                return

            # Get subscription details
            subscription = stripe.Subscription.retrieve(subscription_id)
            user_id = subscription.metadata.get('user_id')

            if not user_id:
                # Try to find user by customer ID
                customer_id = subscription.customer
                user_doc = await self.db.users.find_one({"stripe_customer_id": customer_id})
                if user_doc:
                    user_id = user_doc["_id"]
                else:
                    logger.error(f"No user found for subscription {subscription_id}")
                    return

            # Calculate new premium expiration date
            premium_expires_at = datetime.fromtimestamp(
                subscription.current_period_end,
                tz=timezone.utc
            )

            # Update user subscription status
            update_data = {
                "subscription_status": SubscriptionStatus.ACTIVE,
                "premium_expires_at": premium_expires_at,
                "role": "premium",
                "updated_at": datetime.now(timezone.utc)
            }

            # Clear trial_end if this is a paid invoice (not trial)
            if subscription.status == 'active':
                update_data["trial_end"] = None

            await self._set_paid_subscription_state(user_id, update_data)

            logger.info(f"User {user_id} subscription renewed until {premium_expires_at}")

            # Send subscription renewed email (only for renewals, not initial subscriptions)
            try:
                # Check if this is a renewal (not the first payment after trial)
                is_renewal = invoice.get('billing_reason') in ['subscription_cycle', 'subscription_update']

                if is_renewal:
                    user_doc = await self.db.users.find_one({"_id": user_id})
                    if user_doc:
                        user_language = user_doc.get("language", Language.ENGLISH)

                        # Determine billing interval from subscription
                        billing_interval = BillingInterval.MONTHLY
                        for item in subscription['items']['data']:
                            if item.price.id == settings.stripe_yearly_price_id:
                                billing_interval = BillingInterval.YEARLY
                                break

                        # Format billing interval for display
                        billing_interval_display = "Monthly" if billing_interval == BillingInterval.MONTHLY else "Yearly"
                        if user_language == Language.CHINESE:
                            billing_interval_display = "月付" if billing_interval == BillingInterval.MONTHLY else "年付"

                        # Get amount from invoice
                        amount = f"{invoice.get('amount_paid', 0) / 100:.2f}"

                        # Calculate next billing date
                        next_billing_date = datetime.fromtimestamp(
                            subscription.current_period_end,
                            tz=timezone.utc
                        )

                        # Format dates for display
                        next_billing_str = next_billing_date.strftime("%Y-%m-%d %H:%M UTC")
                        premium_expires_str = premium_expires_at.strftime("%Y-%m-%d %H:%M UTC")

                        await email_service.send_subscription_renewed_email(
                            to_email=user_doc.get("email"),
                            user_id=user_id,
                            user_name=user_doc.get("name"),
                            billing_interval=billing_interval_display,
                            amount=amount,
                            next_billing_date=next_billing_str,
                            premium_expires_at=premium_expires_str,
                            language=user_language
                        )
                        logger.info(f"Subscription renewed email sent to user {user_id}")
            except Exception as email_error:
                logger.error(f"Failed to send subscription renewed email to user {user_id}: {email_error}")

        except Exception as e:
            logger.error(f"Error handling invoice payment: {e}")
            raise

    async def handle_subscription_updated(self, subscription: Dict[str, Any]) -> None:
        """Handle subscription status changes"""
        try:
            subscription_id = subscription.get('id')
            user_id = subscription.get('metadata', {}).get('user_id')

            if not user_id:
                # Try to find user by customer ID
                customer_id = subscription.get('customer')
                user_doc = await self.db.users.find_one({"stripe_customer_id": customer_id})
                if user_doc:
                    user_id = user_doc["_id"]
                else:
                    logger.error(f"No user found for subscription {subscription_id}")
                    return

            # Map Stripe status to our enum
            status_mapping = {
                'active': SubscriptionStatus.ACTIVE,
                'canceled': SubscriptionStatus.CANCELED,
                'past_due': SubscriptionStatus.PAST_DUE,
                'unpaid': SubscriptionStatus.UNPAID,
                'trialing': SubscriptionStatus.TRIALING
            }

            subscription_status = status_mapping.get(
                subscription.get('status'),
                SubscriptionStatus.CANCELED
            )

            # Update user role based on subscription status
            user_role = "premium" if subscription_status in [
                SubscriptionStatus.ACTIVE,
                SubscriptionStatus.TRIALING
            ] else "free"

            # Calculate premium expiration
            premium_expires_at = None
            if subscription.get('current_period_end'):
                premium_expires_at = datetime.fromtimestamp(
                    subscription['current_period_end'],
                    tz=timezone.utc
                )

            # Update user in database
            update_data = {
                "subscription_status": subscription_status,
                "role": user_role,
                "updated_at": datetime.now(timezone.utc)
            }

            if premium_expires_at:
                update_data["premium_expires_at"] = premium_expires_at

            if user_role == "premium":
                await self._set_paid_subscription_state(
                    user_id,
                    update_data,
                )
            else:
                await self._set_inactive_subscription_state(
                    user_id,
                    update_data,
                )

            logger.info(f"User {user_id} subscription status updated to {subscription_status}")

        except Exception as e:
            logger.error(f"Error handling subscription update: {e}")
            raise

    async def cancel_subscription(self, user: UserInDB) -> bool:
        """Cancel user's subscription at period end"""
        try:
            if not user.stripe_subscription_id:
                return False

            # Get subscription info before cancellation
            subscription = stripe.Subscription.retrieve(user.stripe_subscription_id)

            # Cancel subscription at period end
            stripe.Subscription.modify(
                user.stripe_subscription_id,
                cancel_at_period_end=True
            )

            # Update user status
            await self.db.users.update_one(
                {"_id": user.id},
                {"$set": {
                    "subscription_status": SubscriptionStatus.CANCELED,
                    "updated_at": datetime.now(timezone.utc)
                }}
            )

            # Send subscription cancelled email
            try:
                user_language = user.language if hasattr(user, 'language') else Language.ENGLISH

                # Get premium expiration date
                premium_expires_at = datetime.fromtimestamp(
                    subscription.current_period_end,
                    tz=timezone.utc
                )

                # Format dates for display
                cancellation_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                premium_expires_str = premium_expires_at.strftime("%Y-%m-%d %H:%M UTC")

                await email_service.send_subscription_cancelled_email(
                    to_email=user.email,
                    user_id=user.id,
                    user_name=user.name,
                    cancellation_date=cancellation_date_str,
                    premium_expires_at=premium_expires_str,
                    language=user_language
                )
                logger.info(f"Subscription cancelled email sent to user {user.id}")
            except Exception as email_error:
                logger.error(f"Failed to send subscription cancelled email to user {user.id}: {email_error}")

            return True

        except Exception as e:
            logger.error(f"Error canceling subscription: {e}")
            return False

    async def get_subscription_info(self, user: UserInDB) -> Optional[Dict[str, Any]]:
        """Get user's subscription information"""
        try:
            if not user.stripe_subscription_id:
                return None

            subscription = stripe.Subscription.retrieve(user.stripe_subscription_id)

            return {
                "status": subscription.status,
                "current_period_end": datetime.fromtimestamp(
                    subscription.current_period_end,
                    tz=timezone.utc
                ),
                "cancel_at_period_end": subscription.cancel_at_period_end,
                "trial_end": (
                    datetime.fromtimestamp(subscription.trial_end, tz=timezone.utc)
                    if subscription.trial_end
                    else None
                )
            }

        except Exception as e:
            logger.error(f"Error getting subscription info: {e}")
            return None

    async def delete_customer_account(self, user: UserInDB) -> None:
        """End Stripe billing before the local account is permanently deleted."""
        if user.stripe_subscription_id:
            try:
                subscription = stripe.Subscription.retrieve(user.stripe_subscription_id)
                if subscription.status != "canceled":
                    stripe.Subscription.delete(user.stripe_subscription_id)
            except stripe.error.InvalidRequestError:
                logger.warning(
                    "Stripe subscription %s no longer exists during account deletion",
                    user.stripe_subscription_id,
                )

        if user.stripe_customer_id:
            try:
                stripe.Customer.delete(user.stripe_customer_id)
            except stripe.error.InvalidRequestError:
                logger.warning(
                    "Stripe customer %s no longer exists during account deletion",
                    user.stripe_customer_id,
                )

stripe_service = StripeService()
