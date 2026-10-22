import logging
from jinja2 import Environment, FileSystemLoader
import os
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from urllib.parse import quote

from itsdangerous import BadData, SignatureExpired, URLSafeSerializer, URLSafeTimedSerializer
from app.config import settings
from app.models.user import Language
from app.core.i18n import i18n_service
# Import inside function to avoid hard dependency if not installed
from azure.communication.email import EmailClient

logger = logging.getLogger(__name__)

UNSUBSCRIBE_MAX_AGE = 365 * 24 * 3600
MARKETING_UNSUBSCRIBE_SALT = "predixlab-marketing-email-unsubscribe"
DAILY_SIGNALS_UNSUBSCRIBE_SALT = "predixlab-daily-signals-email-unsubscribe"


def _load_unsubscribe_token(token: str, salt: str) -> dict:
    cutoff = settings.unsubscribe_legacy_cutoff
    allow_legacy = settings.unsubscribe_allow_legacy_tokens and (
        cutoff is None or datetime.now(timezone.utc) < cutoff
    )
    keys = list(dict.fromkeys(
        key for key in (
            settings.secret_key if allow_legacy else None,
            settings.unsubscribe_secret,
        ) if key
    ))
    if not keys:
        raise BadData("Legacy unsubscribe tokens are disabled")
    try:
        return URLSafeTimedSerializer(keys, salt=salt).loads(
            token, max_age=UNSUBSCRIBE_MAX_AGE
        )
    except SignatureExpired:
        raise
    except BadData:
        if not allow_legacy:
            raise
        # Temporary compatibility for untimed links already sent using SECRET_KEY.
        return URLSafeSerializer(settings.secret_key, salt=salt).loads(token)


class EmailService:
    def __init__(self):
        # ACS config (expected from environment via settings)
        self.acs_connection_string = getattr(settings, "acs_connection_string", "")
        self.acs_sender = getattr(settings, "acs_sender", "")
        self.backend = (getattr(settings, "email_backend", "acs") or "acs").lower()
        self.acs_email_client = self._build_acs_client()
        self.unsubscribe_serializer = URLSafeTimedSerializer(
            settings.unsubscribe_secret or settings.secret_key,
            salt=MARKETING_UNSUBSCRIBE_SALT,
        )
        self.daily_signals_unsubscribe_serializer = URLSafeTimedSerializer(
            settings.unsubscribe_secret or settings.secret_key,
            salt=DAILY_SIGNALS_UNSUBSCRIBE_SALT,
        )

        # Setup Jinja2 template environment for different languages
        template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates', 'email')
        self.template_env = Environment(loader=FileSystemLoader(template_dir))

    def _build_acs_client(self):
        """Build the ACS client, tolerating a missing or malformed connection string.

        A misconfigured connection string must not prevent the application from
        starting; sending will simply fail (or use the console backend) instead.
        """
        if self.backend == "console" or not self.acs_connection_string:
            return None
        try:
            return EmailClient.from_connection_string(self.acs_connection_string)
        except Exception as e:
            logger.error(f"Failed to initialize ACS email client: {e}")
            return None

    def _get_language_dir(self, language: Language) -> str:
        """Get language directory name based on language enum"""
        if language == Language.ENGLISH:
            return "en"
        else:
            return "cn"  # For Language.CHINESE and any fallbacks

    def _load_template(self, template_name: str, language: Language = Language.ENGLISH, **kwargs) -> Tuple[str, str]:
        """Load HTML and text templates and render with given variables"""
        try:
            lang_dir = self._get_language_dir(language)

            html_template = self.template_env.get_template(f"{lang_dir}/{template_name}.html")
            html_content = html_template.render(**kwargs)

            text_template = self.template_env.get_template(f"{lang_dir}/{template_name}.txt")
            text_content = text_template.render(**kwargs)

            return html_content, text_content
        except Exception as e:
            logger.error(f"Failed to load template {template_name} for language {language}: {e}")
            # Fallback to Chinese templates if English templates fail
            if language == Language.ENGLISH:
                try:
                    html_template = self.template_env.get_template(f"cn/{template_name}.html")
                    html_content = html_template.render(**kwargs)

                    text_template = self.template_env.get_template(f"cn/{template_name}.txt")
                    text_content = text_template.render(**kwargs)

                    return html_content, text_content
                except Exception as fallback_e:
                    logger.error(f"Fallback template loading also failed: {fallback_e}")
            raise

    def create_unsubscribe_url(self, user_id: str, language: Language) -> str:
        """Create a signed link that changes only optional product-email preferences."""
        token = self.unsubscribe_serializer.dumps(
            {"user_id": user_id, "language": language.value}
        )
        return (
            f"{settings.public_api_url.rstrip('/')}/api/v1/user/email/unsubscribe"
            f"?token={quote(token)}"
        )

    def parse_unsubscribe_token(self, token: str) -> tuple[str, Language]:
        """Validate an unsubscribe token without exposing account data in the URL."""
        try:
            payload = _load_unsubscribe_token(token, MARKETING_UNSUBSCRIBE_SALT)
            user_id = payload["user_id"]
            language = Language(payload.get("language", Language.ENGLISH.value))
        except (BadData, KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid unsubscribe link") from exc
        return user_id, language

    def parse_daily_signals_unsubscribe_token(
        self,
        token: str,
    ) -> tuple[str, Language]:
        """Validate a token scoped only to Daily Signals email preferences."""
        try:
            payload = _load_unsubscribe_token(token, DAILY_SIGNALS_UNSUBSCRIBE_SALT)
            user_id = payload["user_id"]
            language = Language(
                payload.get("language", Language.ENGLISH.value)
            )
        except (BadData, KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Invalid Daily Signals unsubscribe link"
            ) from exc
        return str(user_id), language

    @staticmethod
    def _unsubscribe_headers(unsubscribe_url: str) -> Dict[str, str]:
        return {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }

    async def _send_via_acs(
        self,
        to_email: str,
        subject: str,
        html_body: str = None,
        text_body: str = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Send email using Azure Communication Services (executes SDK call in thread)."""
        if not (self.acs_connection_string and self.acs_sender):
            logger.error("ACS configuration missing: acs_connection_string or acs_sender not set")
            return False

        if self.acs_email_client is None:
            logger.error("ACS email client is not initialized; cannot send email")
            return False

        try:
            message = {
                "senderAddress": self.acs_sender,
                "recipients": {
                    "to": [{"address": to_email }]
                },
                "content": {
                    "subject": subject,
                    "plainText": text_body or "Hello world via email.",
                    "html": html_body or ""
                }
            }
            if headers:
                message["headers"] = headers

            # Run the synchronous SDK call in a thread to keep async API
            def send_sync():
                poller = self.acs_email_client.begin_send(message)
                return poller.result()

            resp = await asyncio.to_thread(send_sync)
            # Try to extract message id / status for logging if available
            try:
                message_id = getattr(resp, "message_id", None) or resp.message_id
            except Exception:
                message_id = None

            logger.info(f"ACS email send initiated to {to_email}, message_id={message_id}")
            return True
        except Exception as e:
            logger.exception(f"Failed to send email via ACS to {to_email}: {e}")
            return False

    async def _send_via_console(
        self,
        to_email: str,
        subject: str,
        text_body: str = None,
        html_body: str = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Write the message to the log instead of delivering it.

        Intended for local development, where no mail provider is configured but
        the contents (such as a verification code) still need to be readable.
        """
        body = text_body or html_body or ""
        logger.info(
            "[console email backend] no message was delivered\n"
            f"  to      : {to_email}\n"
            f"  subject : {subject}\n"
            f"  headers : {headers or {}}\n"
            f"  body    : {body.strip()}"
        )
        return True

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str = None,
        text_body: str = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Send an email using the configured backend.

        Returns False if the backend is unavailable or sending fails.
        """
        if self.backend == "console":
            return await self._send_via_console(
                to_email,
                subject,
                text_body=text_body,
                html_body=html_body,
                headers=headers,
            )

        success = await self._send_via_acs(
            to_email,
            subject,
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        if not success:
            logger.error(f"Failed to send email to {to_email} via ACS")
        return success

    async def send_verification_code(self, to_email: str, verification_code: str, language: Language = Language.ENGLISH):
        """Send login verification code email"""
        subject = i18n_service.get_email_subject("login_verification", language, app_name=settings.app_name)

        # Load templates and render with variables
        html_body, text_body = self._load_template(
            "verification_code",
            language=language,
            app_name=settings.app_name,
            verification_code=verification_code
        )

        return await self.send_email(to_email, subject, html_body=html_body, text_body=text_body)

    async def send_registration_verification_code(self, to_email: str, verification_code: str, language: Language = Language.ENGLISH):
        """Send registration verification code email"""
        subject = i18n_service.get_email_subject("registration_verification", language, app_name=settings.app_name)

        # Load templates and render with variables
        html_body, text_body = self._load_template(
            "registration_verification_code",
            language=language,
            app_name=settings.app_name,
            verification_code=verification_code
        )

        return await self.send_email(to_email, subject, html_body=html_body, text_body=text_body)

    async def send_welcome_email(
        self,
        to_email: str,
        user_id: str,
        user_name: str = None,
        language: Language = Language.ENGLISH,
    ):
        """Send welcome email to new users"""
        subject = i18n_service.get_email_subject("welcome", language, app_name=settings.app_name)

        display_name = user_name if user_name else to_email.split('@')[0]
        unsubscribe_url = self.create_unsubscribe_url(user_id, language)

        # Load templates and render with variables
        html_body, text_body = self._load_template(
            "welcome",
            language=language,
            app_name=settings.app_name,
            user_name=display_name,
            unsubscribe_url=unsubscribe_url,
        )

        return await self.send_email(
            to_email,
            subject,
            html_body=html_body,
            text_body=text_body,
            headers=self._unsubscribe_headers(unsubscribe_url),
        )

    async def send_subscription_success_email(
        self,
        to_email: str,
        user_id: str,
        user_name: str,
        billing_interval: str,
        trial_end: str = None,
        next_billing_date: str = None,
        language: Language = Language.ENGLISH
    ):
        """Send subscription success email when user successfully subscribes"""
        subject = i18n_service.get_email_subject("subscription_success", language, app_name=settings.app_name)

        display_name = user_name if user_name else to_email.split('@')[0]
        unsubscribe_url = self.create_unsubscribe_url(user_id, language)

        # Load templates and render with variables
        html_body, text_body = self._load_template(
            "subscription_success",
            language=language,
            app_name=settings.app_name,
            user_name=display_name,
            billing_interval=billing_interval,
            trial_end=trial_end,
            next_billing_date=next_billing_date,
            unsubscribe_url=unsubscribe_url,
        )

        return await self.send_email(to_email, subject, html_body=html_body, text_body=text_body)

    async def send_subscription_renewed_email(
        self,
        to_email: str,
        user_id: str,
        user_name: str,
        billing_interval: str,
        amount: str,
        next_billing_date: str,
        premium_expires_at: str,
        language: Language = Language.ENGLISH
    ):
        """Send subscription renewed email when subscription is successfully renewed"""
        subject = i18n_service.get_email_subject("subscription_renewed", language, app_name=settings.app_name)

        display_name = user_name if user_name else to_email.split('@')[0]
        unsubscribe_url = self.create_unsubscribe_url(user_id, language)

        # Load templates and render with variables
        html_body, text_body = self._load_template(
            "subscription_renewed",
            language=language,
            app_name=settings.app_name,
            user_name=display_name,
            billing_interval=billing_interval,
            amount=amount,
            next_billing_date=next_billing_date,
            premium_expires_at=premium_expires_at,
            unsubscribe_url=unsubscribe_url,
        )

        return await self.send_email(to_email, subject, html_body=html_body, text_body=text_body)

    async def send_subscription_cancelled_email(
        self,
        to_email: str,
        user_id: str,
        user_name: str,
        cancellation_date: str,
        premium_expires_at: str,
        language: Language = Language.ENGLISH
    ):
        """Send subscription cancelled email when user cancels their subscription"""
        subject = i18n_service.get_email_subject("subscription_cancelled", language, app_name=settings.app_name)

        display_name = user_name if user_name else to_email.split('@')[0]
        unsubscribe_url = self.create_unsubscribe_url(user_id, language)

        # Load templates and render with variables
        html_body, text_body = self._load_template(
            "subscription_cancelled",
            language=language,
            app_name=settings.app_name,
            user_name=display_name,
            cancellation_date=cancellation_date,
            premium_expires_at=premium_expires_at,
            unsubscribe_url=unsubscribe_url,
        )

        return await self.send_email(to_email, subject, html_body=html_body, text_body=text_body)

# Create global email service instance
email_service = EmailService()
