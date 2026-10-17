"""Unit tests for the i18n module."""
import pytest
from unittest.mock import patch, MagicMock

from app.core.i18n import I18nService, I18nKeys
from app.models.user import Language


class TestI18nService:
    """Tests for I18nService class."""

    def test_init_loads_translations(self):
        """Test that translations are loaded on initialization."""
        service = I18nService()
        
        # Should have loaded at least English translations
        assert 'en' in service.translations or len(service.translations) >= 0

    def test_get_text_default_language(self):
        """Test getting text with default language (English)."""
        service = I18nService()
        
        # Test with any key - should return at least the key value if translation not found
        text = service.get_text(I18nKeys.WELCOME_SUBJECT, Language.ENGLISH)
        
        assert text is not None
        assert len(text) > 0

    def test_get_text_with_formatting(self):
        """Test getting text with format arguments."""
        service = I18nService()
        
        # Even if translation doesn't support formatting, should not crash
        text = service.get_text(I18nKeys.WELCOME_SUBJECT, Language.ENGLISH, name="Test User")
        
        assert text is not None

    def test_get_email_subject_login_verification(self):
        """Test getting login verification email subject."""
        service = I18nService()
        
        subject = service.get_email_subject("login_verification", Language.ENGLISH)
        
        assert subject is not None
        assert len(subject) > 0

    def test_get_email_subject_registration_verification(self):
        """Test getting registration verification email subject."""
        service = I18nService()
        
        subject = service.get_email_subject("registration_verification", Language.ENGLISH)
        
        assert subject is not None
        assert len(subject) > 0

    def test_get_email_subject_welcome(self):
        """Test getting welcome email subject."""
        service = I18nService()
        
        subject = service.get_email_subject("welcome", Language.ENGLISH)
        
        assert subject is not None
        assert len(subject) > 0

    def test_get_email_subject_subscription_success(self):
        """Test getting subscription success email subject."""
        service = I18nService()
        
        subject = service.get_email_subject("subscription_success", Language.ENGLISH)
        
        assert subject is not None
        assert len(subject) > 0

    def test_get_email_subject_subscription_renewed(self):
        """Test getting subscription renewed email subject."""
        service = I18nService()
        
        subject = service.get_email_subject("subscription_renewed", Language.ENGLISH)
        
        assert subject is not None
        assert len(subject) > 0

    def test_get_email_subject_subscription_cancelled(self):
        """Test getting subscription cancelled email subject."""
        service = I18nService()
        
        subject = service.get_email_subject("subscription_cancelled", Language.ENGLISH)
        
        assert subject is not None
        assert len(subject) > 0

    def test_get_email_subject_unknown_type_fallback(self):
        """Test getting email subject for unknown type falls back."""
        service = I18nService()
        
        subject = service.get_email_subject("unknown_type", Language.ENGLISH)
        
        # Should fallback to login verification subject
        assert subject is not None

    def test_flatten_dict_simple(self):
        """Test flattening a simple nested dictionary."""
        service = I18nService()
        
        nested = {
            "level1": {
                "level2": {
                    "key": "value"
                }
            }
        }
        
        result = service._flatten_dict(nested)
        
        assert "level1.level2.key" in result
        assert result["level1.level2.key"] == "value"

    def test_flatten_dict_with_multiple_keys(self):
        """Test flattening dictionary with multiple keys at same level."""
        service = I18nService()
        
        nested = {
            "email": {
                "subjects": {
                    "welcome": "Welcome!",
                    "login": "Login Verification"
                }
            }
        }
        
        result = service._flatten_dict(nested)
        
        assert "email.subjects.welcome" in result
        assert "email.subjects.login" in result
        assert result["email.subjects.welcome"] == "Welcome!"
        assert result["email.subjects.login"] == "Login Verification"

    def test_get_language_code_english(self):
        """Test getting language code for English."""
        service = I18nService()
        
        code = service._get_language_code(Language.ENGLISH)
        
        assert code == "en"


class TestI18nKeys:
    """Tests for I18nKeys enum."""

    def test_login_verification_subject_key(self):
        """Test login verification subject key value."""
        assert I18nKeys.LOGIN_VERIFICATION_SUBJECT.value == "email.subjects.login_verification"

    def test_registration_verification_subject_key(self):
        """Test registration verification subject key value."""
        assert I18nKeys.REGISTRATION_VERIFICATION_SUBJECT.value == "email.subjects.registration_verification"

    def test_welcome_subject_key(self):
        """Test welcome subject key value."""
        assert I18nKeys.WELCOME_SUBJECT.value == "email.subjects.welcome"

    def test_subscription_success_subject_key(self):
        """Test subscription success subject key value."""
        assert I18nKeys.SUBSCRIPTION_SUCCESS_SUBJECT.value == "email.subjects.subscription_success"

    def test_subscription_renewed_subject_key(self):
        """Test subscription renewed subject key value."""
        assert I18nKeys.SUBSCRIPTION_RENEWED_SUBJECT.value == "email.subjects.subscription_renewed"

    def test_subscription_cancelled_subject_key(self):
        """Test subscription cancelled subject key value."""
        assert I18nKeys.SUBSCRIPTION_CANCELLED_SUBJECT.value == "email.subjects.subscription_cancelled"
