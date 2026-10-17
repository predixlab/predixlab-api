import json
import os
from typing import Dict, Any
from enum import Enum
from app.models.user import Language

class I18nKeys(str, Enum):
    """I18n keys for translations"""
    # Email subjects
    LOGIN_VERIFICATION_SUBJECT = "email.subjects.login_verification"
    REGISTRATION_VERIFICATION_SUBJECT = "email.subjects.registration_verification"
    WELCOME_SUBJECT = "email.subjects.welcome"
    SUBSCRIPTION_SUCCESS_SUBJECT = "email.subjects.subscription_success"
    SUBSCRIPTION_RENEWED_SUBJECT = "email.subjects.subscription_renewed"
    SUBSCRIPTION_CANCELLED_SUBJECT = "email.subjects.subscription_cancelled"

class I18nService:
    """Internationalization service for handling translations"""

    def __init__(self):
        self.translations: Dict[str, Dict[str, str]] = {}
        self._load_translations()

    def _flatten_dict(self, data: Dict[str, Any], parent_key: str = '', sep: str = '.') -> Dict[str, str]:
        """Flatten nested dictionary into dot notation keys"""
        items = []
        for k, v in data.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, str(v)))
        return dict(items)

    def _load_translations(self):
        """Load translations from JSON files and flatten them"""
        translations_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'translations')

        # Load Chinese translations
        zh_file = os.path.join(translations_dir, 'zh.json')
        if os.path.exists(zh_file):
            with open(zh_file, 'r', encoding='utf-8') as f:
                zh_data = json.load(f)
                self.translations['zh'] = self._flatten_dict(zh_data)

        # Load English translations
        en_file = os.path.join(translations_dir, 'en.json')
        if os.path.exists(en_file):
            with open(en_file, 'r', encoding='utf-8') as f:
                en_data = json.load(f)
                self.translations['en'] = self._flatten_dict(en_data)

    def _get_language_code(self, language: Language) -> str:
        """Get language code from Language enum"""
        if language == Language.ENGLISH:
            return "en"
        else:
            return "en"  # Default to English

    def get_text(self, key: I18nKeys, language: Language = Language.ENGLISH, **kwargs) -> str:
        """Get translated text for a given key and language"""
        lang_code = self._get_language_code(language)

        # Get translations for the language, fallback to English if not found
        language_dict = self.translations.get(lang_code, self.translations.get('en', {}))

        # Get the text directly from flattened dictionary
        text = language_dict.get(key.value, key.value)

        # Format with provided kwargs
        if kwargs and isinstance(text, str):
            try:
                text = text.format(**kwargs)
            except (KeyError, ValueError):
                # If formatting fails, return the unformatted text
                pass

        return text

    def get_email_subject(self, email_type: str, language: Language = Language.ENGLISH, **kwargs) -> str:
        """Get email subject for different email types"""
        subject_mapping = {
            "login_verification": I18nKeys.LOGIN_VERIFICATION_SUBJECT,
            "registration_verification": I18nKeys.REGISTRATION_VERIFICATION_SUBJECT,
            "welcome": I18nKeys.WELCOME_SUBJECT,
            "subscription_success": I18nKeys.SUBSCRIPTION_SUCCESS_SUBJECT,
            "subscription_renewed": I18nKeys.SUBSCRIPTION_RENEWED_SUBJECT,
            "subscription_cancelled": I18nKeys.SUBSCRIPTION_CANCELLED_SUBJECT
        }

        subject_key = subject_mapping.get(email_type, I18nKeys.LOGIN_VERIFICATION_SUBJECT)
        return self.get_text(subject_key, language, **kwargs)

# Create global i18n service instance
i18n_service = I18nService()
