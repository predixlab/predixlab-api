from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse
import logging
from html import escape

from app.models.user import (
    AccountDeletionRequest,
    Language,
    User,
    UserNoticeAcceptanceRequest,
    UserRole,
    UserUpdate,
    to_public_user,
)
from app.core.dependencies import get_current_user
from app.services.user_service import user_service
from app.services.stripe_service import stripe_service
from app.core.email import email_service
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user", tags=["User"])


def _unsubscribe_page(language: Language, state: str) -> str:
    app_name = escape(settings.app_name)
    if language == Language.CHINESE:
        content = {
            "confirm": (
                "确认停止产品邮件",
                "退订后，验证码、安全通知和账单邮件仍会正常发送。",
                "停止产品邮件",
            ),
            "success": (
                "邮件偏好已更新",
                "我们已停止发送可选的产品邮件。验证码、安全通知和账单邮件仍会正常发送。",
                None,
            ),
            "invalid": (
                "退订链接无效",
                "此链接无效或已损坏，请通过账户页面或客服更新邮件偏好。",
                None,
            ),
        }
    else:
        content = {
            "confirm": (
                "Stop optional product emails",
                "Verification, security, and billing emails will continue.",
                "Stop product emails",
            ),
            "success": (
                "Email preference updated",
                "Optional product emails are now disabled. Verification, security, and billing emails will continue.",
                None,
            ),
            "invalid": (
                "Invalid unsubscribe link",
                "This link is invalid or damaged. Update your preference from your account or contact support.",
                None,
            ),
        }
    title, body, button = content[state]
    form = (
        "<form method='post' style='margin-top:24px'>"
        "<button type='submit' style='border:0;border-radius:6px;background:#2563eb;"
        "color:white;padding:12px 18px;font:inherit;font-weight:700;cursor:pointer'>"
        f"{escape(button)}</button></form>"
        if button
        else ""
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title></head>"
        "<body style='font-family:Arial,sans-serif;max-width:640px;margin:64px auto;"
        "padding:0 24px;color:#0f172a;line-height:1.6'>"
        f"<p style='font-weight:700;color:#2563eb'>{app_name}</p>"
        f"<h1>{escape(title)}</h1><p>{escape(body)}</p>{form}</body></html>"
    )


def _daily_signals_unsubscribe_page(language: Language, state: str) -> str:
    app_name = escape(settings.app_name)
    if language == Language.CHINESE:
        content = {
            "confirm": (
                "停止每日信号邮件",
                "关闭后仍可在 PredixLab 查看每日信号，其他产品邮件不受影响。",
                "停止每日信号邮件",
            ),
            "success": (
                "每日信号邮件已关闭",
                "您可以随时在个人资料页面重新开启每日信号邮件。",
                None,
            ),
            "invalid": (
                "退订链接无效",
                "请通过个人资料页面更新每日信号邮件偏好。",
                None,
            ),
        }
    else:
        content = {
            "confirm": (
                "Stop Daily Signals emails",
                "Daily Signals remain available in PredixLab. Other product emails are unchanged.",
                "Stop Daily Signals emails",
            ),
            "success": (
                "Daily Signals emails disabled",
                "You can enable Daily Signals emails again from your Profile page.",
                None,
            ),
            "invalid": (
                "Invalid unsubscribe link",
                "Update Daily Signals email from your Profile page.",
                None,
            ),
        }
    title, body, button = content[state]
    form = (
        "<form method='post' style='margin-top:24px'>"
        "<button type='submit' style='border:0;border-radius:6px;background:#2563eb;"
        "color:white;padding:12px 18px;font:inherit;font-weight:700;cursor:pointer'>"
        f"{escape(button)}</button></form>"
        if button
        else ""
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title></head>"
        "<body style='font-family:Arial,sans-serif;max-width:640px;margin:64px auto;"
        "padding:0 24px;color:#0f172a;line-height:1.6'>"
        f"<p style='font-weight:700;color:#2563eb'>{app_name}</p>"
        f"<h1>{escape(title)}</h1><p>{escape(body)}</p>{form}</body></html>"
    )


@router.get(
    "/email/unsubscribe",
    response_class=HTMLResponse,
    summary="Unsubscribe from optional product emails",
    operation_id="unsubscribe_marketing_email_get",
)
async def unsubscribe_marketing_email(token: str = Query(..., min_length=1)):
    """Process a signed browser unsubscribe request."""
    try:
        _, language = email_service.parse_unsubscribe_token(token)
    except ValueError:
        return HTMLResponse(
            _unsubscribe_page(Language.ENGLISH, "invalid"),
            status_code=400,
        )

    return HTMLResponse(_unsubscribe_page(language, "confirm"))


@router.post(
    "/email/unsubscribe",
    response_class=HTMLResponse,
    summary="One-click unsubscribe from optional product emails",
    operation_id="unsubscribe_marketing_email_post",
)
async def unsubscribe_marketing_email_one_click(token: str = Query(..., min_length=1)):
    """Process an RFC 8058 one-click unsubscribe request."""
    try:
        user_id, language = email_service.parse_unsubscribe_token(token)
    except ValueError:
        return HTMLResponse(
            _unsubscribe_page(Language.ENGLISH, "invalid"),
            status_code=400,
        )

    await user_service.unsubscribe_marketing_emails(user_id)
    return HTMLResponse(_unsubscribe_page(language, "success"))


@router.get(
    "/email/daily-signals/unsubscribe",
    response_class=HTMLResponse,
    summary="Unsubscribe from Daily Signals emails",
    operation_id="unsubscribe_daily_signals_email_get",
)
async def unsubscribe_daily_signals_email(
    token: str = Query(..., min_length=1),
):
    """Show confirmation for a Daily Signals-only unsubscribe request."""
    try:
        _, language = email_service.parse_daily_signals_unsubscribe_token(token)
    except ValueError:
        return HTMLResponse(
            _daily_signals_unsubscribe_page(Language.ENGLISH, "invalid"),
            status_code=400,
        )

    return HTMLResponse(
        _daily_signals_unsubscribe_page(language, "confirm")
    )


@router.post(
    "/email/daily-signals/unsubscribe",
    response_class=HTMLResponse,
    summary="One-click unsubscribe from Daily Signals emails",
    operation_id="unsubscribe_daily_signals_email_post",
)
async def unsubscribe_daily_signals_email_one_click(
    token: str = Query(..., min_length=1),
):
    """Disable only the Daily Signals email preference."""
    try:
        user_id, language = (
            email_service.parse_daily_signals_unsubscribe_token(token)
        )
    except ValueError:
        return HTMLResponse(
            _daily_signals_unsubscribe_page(Language.ENGLISH, "invalid"),
            status_code=400,
        )

    await user_service.unsubscribe_daily_signals_emails(user_id)
    return HTMLResponse(
        _daily_signals_unsubscribe_page(language, "success")
    )


@router.get("/profile", response_model=User, summary="Get current user profile")
async def get_user_profile(current_user = Depends(get_current_user)):
    """Get current authenticated user profile information"""
    return to_public_user(current_user)


@router.post(
    "/notice/accept",
    response_model=User,
    summary="Accept the current User Notice and Risk Disclosure",
)
async def accept_user_notice(
    acceptance: UserNoticeAcceptanceRequest,
    current_user = Depends(get_current_user),
):
    """Persist acceptance for an existing user when the notice version changes."""
    updated_user = await user_service.accept_user_notice(
        current_user.id,
        acceptance.user_notice_version,
    )
    return to_public_user(updated_user)

@router.patch("/profile", response_model=User, summary="Update user profile")
async def update_user_profile(
    update_data: UserUpdate,
    current_user = Depends(get_current_user)
):
    """Update current user's profile information"""
    try:
        update_values = update_data.model_dump(exclude_unset=True)
        if (
            "daily_signals_email_enabled" in update_values
            and current_user.role not in {UserRole.PREMIUM, UserRole.ADMIN}
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Daily Signals email is available to premium and admin "
                    "users only"
                ),
            )
        updated_user = await user_service.update_user(
            current_user.id,
            update_values,
        )

        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update user profile"
            )

        logger.info(f"User {current_user.email} updated profile")
        return to_public_user(updated_user)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update user profile for {current_user.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user profile"
        )


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT, summary="Delete account")
async def delete_account(
    request: AccountDeletionRequest,
    current_user = Depends(get_current_user),
):
    """End billing and permanently delete the account's user-owned data."""
    if request.confirmation.lower() != current_user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter the account email address to confirm deletion",
        )

    try:
        await stripe_service.delete_customer_account(current_user)
    except Exception as exc:
        logger.error("Failed to end Stripe billing for account deletion: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to end billing; the account was not deleted",
        ) from exc

    deleted = await user_service.delete_account_data(current_user)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete account",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
