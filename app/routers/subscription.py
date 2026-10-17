from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from typing import Optional
import stripe
import logging
from app.config import settings
from app.models.user import (
    CreateCheckoutSessionRequest,
    CheckoutSessionResponse,
    SubscriptionInfo,
    SubscriptionPricesResponse,
    UserInDB,
    BillingInterval
)
from app.core.dependencies import get_current_user
from app.services.stripe_service import stripe_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscription", tags=["subscription"])

@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    request: CreateCheckoutSessionRequest,
    current_user: UserInDB = Depends(get_current_user)
):
    """Create a Stripe checkout session for subscription with free trial"""
    try:
        # Check if user already has an active subscription
        if current_user.stripe_subscription_id:
            subscription_info = await stripe_service.get_subscription_info(current_user)
            if subscription_info and subscription_info["status"] in ["active", "trialing"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User already has an active subscription"
                )

        # Create checkout session
        session_data = await stripe_service.create_checkout_session(
            user=current_user,
            billing_interval=request.billing_interval,
            success_url=request.success_url,
            cancel_url=request.cancel_url
        )

        return CheckoutSessionResponse(**session_data)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create checkout session"
        )

@router.get("/info", response_model=Optional[SubscriptionInfo])
async def get_subscription_info(
    current_user: UserInDB = Depends(get_current_user)
):
    """Get current user's subscription information"""
    try:
        if not current_user.stripe_subscription_id:
            return None

        info = await stripe_service.get_subscription_info(current_user)
        if not info:
            return None

        return SubscriptionInfo(
            status=info["status"],
            billing_interval=current_user.billing_interval,
            current_period_end=info["current_period_end"],
            trial_end=info["trial_end"],
            cancel_at_period_end=info["cancel_at_period_end"]
        )

    except Exception as e:
        logger.error(f"Error getting subscription info: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get subscription information"
        )

@router.post("/cancel")
async def cancel_subscription(
    current_user: UserInDB = Depends(get_current_user)
):
    """Cancel user's subscription at the end of the current period"""
    try:
        if not current_user.stripe_subscription_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active subscription found"
            )

        success = await stripe_service.cancel_subscription(current_user)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to cancel subscription"
            )

        return {"message": "Subscription will be canceled at the end of the current period"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error canceling subscription: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel subscription"
        )

@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    try:
        payload = await request.body()
        sig_header = request.headers.get('stripe-signature')

        if not sig_header:
            raise HTTPException(status_code=400, detail="Missing stripe-signature header")

        # Verify webhook signature
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.stripe_webhook_secret
            )
        except ValueError:
            logger.error("Invalid payload in webhook")
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.error.SignatureVerificationError:
            logger.error("Invalid signature in webhook")
            raise HTTPException(status_code=400, detail="Invalid signature")

        logger.info(f"Received Stripe webhook event: {event['type']}")

        # Handle different event types
        if event['type'] == 'checkout.session.completed':
            await stripe_service.handle_checkout_completed(event['data']['object'])

        elif event['type'] == 'invoice.paid':
            await stripe_service.handle_invoice_paid(event['data']['object'])

        elif event['type'] == 'customer.subscription.updated':
            await stripe_service.handle_subscription_updated(event['data']['object'])

        elif event['type'] == 'customer.subscription.deleted':
            # Handle subscription cancellation
            await stripe_service.handle_subscription_updated(event['data']['object'])

        else:
            logger.info(f"Unhandled event type: {event['type']}")

        return JSONResponse(content={"status": "success"})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed"
        )

@router.get("/prices", response_model=SubscriptionPricesResponse)
async def get_subscription_prices():
    """Get available subscription prices"""
    try:
        prices = []

        # Get monthly price info
        if settings.stripe_monthly_price_id:
            try:
                monthly_price = stripe.Price.retrieve(settings.stripe_monthly_price_id)
                prices.append({
                    "id": monthly_price.id,
                    "interval": "month",
                    "amount": monthly_price.unit_amount,
                    "currency": monthly_price.currency,
                    "product": monthly_price.product
                })
            except Exception as e:
                logger.warning(f"Could not retrieve monthly price: {e}")

        # Get yearly price info
        if settings.stripe_yearly_price_id:
            try:
                yearly_price = stripe.Price.retrieve(settings.stripe_yearly_price_id)
                prices.append({
                    "id": yearly_price.id,
                    "interval": "year",
                    "amount": yearly_price.unit_amount,
                    "currency": yearly_price.currency,
                    "product": yearly_price.product
                })
            except Exception as e:
                logger.warning(f"Could not retrieve yearly price: {e}")

        return {
            "prices": prices,
            "trial_period_days": settings.stripe_trial_period_days
        }

    except Exception as e:
        logger.error(f"Error getting subscription prices: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get subscription prices"
        )

@router.get("/publishable-key")
async def get_publishable_key():
    """Get Stripe publishable key for frontend"""
    return {"publishable_key": settings.stripe_publishable_key}
