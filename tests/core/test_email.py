"""Unit tests for the email service delivery backends."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.email import EmailService


@pytest.fixture
def console_service():
    """An EmailService configured to log instead of deliver."""
    with patch("app.core.email.settings") as mock_settings:
        mock_settings.email_backend = "console"
        mock_settings.acs_connection_string = ""
        mock_settings.acs_sender = ""
        mock_settings.app_name = "PredixLab"
        yield EmailService()


class TestBackendSelection:
    def test_console_backend_skips_acs_client(self, console_service):
        assert console_service.backend == "console"
        assert console_service.acs_email_client is None

    def test_missing_connection_string_does_not_raise(self):
        with patch("app.core.email.settings") as mock_settings:
            mock_settings.email_backend = "acs"
            mock_settings.acs_connection_string = ""
            mock_settings.acs_sender = ""
            service = EmailService()
        assert service.acs_email_client is None

    def test_malformed_connection_string_does_not_raise(self):
        with patch("app.core.email.settings") as mock_settings:
            mock_settings.email_backend = "acs"
            mock_settings.acs_connection_string = "not-a-connection-string"
            mock_settings.acs_sender = "sender@example.com"
            service = EmailService()
        assert service.acs_email_client is None

    def test_backend_defaults_to_acs(self):
        with patch("app.core.email.settings") as mock_settings:
            mock_settings.email_backend = ""
            mock_settings.acs_connection_string = ""
            mock_settings.acs_sender = ""
            service = EmailService()
        assert service.backend == "acs"

    def test_backend_is_case_insensitive(self):
        with patch("app.core.email.settings") as mock_settings:
            mock_settings.email_backend = "CONSOLE"
            mock_settings.acs_connection_string = ""
            mock_settings.acs_sender = ""
            service = EmailService()
        assert service.backend == "console"


class TestConsoleBackend:
    @pytest.mark.asyncio
    async def test_send_email_succeeds(self, console_service):
        assert await console_service.send_email("user@example.com", "Subject", text_body="body") is True

    @pytest.mark.asyncio
    async def test_send_email_never_calls_acs(self, console_service):
        console_service._send_via_acs = AsyncMock(return_value=False)
        assert await console_service.send_email("user@example.com", "Subject", text_body="body") is True
        console_service._send_via_acs.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_body_is_logged(self, console_service, caplog):
        with caplog.at_level("INFO"):
            await console_service.send_email("user@example.com", "Your code", text_body="Code: 123456")
        assert "123456" in caplog.text
        assert "user@example.com" in caplog.text

    @pytest.mark.asyncio
    async def test_falls_back_to_html_body(self, console_service, caplog):
        with caplog.at_level("INFO"):
            await console_service.send_email("user@example.com", "Subject", html_body="<b>654321</b>")
        assert "654321" in caplog.text

    @pytest.mark.asyncio
    async def test_verification_code_is_logged(self, console_service, caplog):
        console_service._load_template = MagicMock(return_value=("<b>042122</b>", "Your code is 042122"))
        with caplog.at_level("INFO"):
            result = await console_service.send_verification_code("user@example.com", "042122")
        assert result is True
        assert "042122" in caplog.text


class TestAcsBackendGuards:
    @pytest.mark.asyncio
    async def test_send_fails_without_configuration(self):
        with patch("app.core.email.settings") as mock_settings:
            mock_settings.email_backend = "acs"
            mock_settings.acs_connection_string = ""
            mock_settings.acs_sender = ""
            service = EmailService()
        assert await service.send_email("user@example.com", "Subject", text_body="body") is False

    @pytest.mark.asyncio
    async def test_send_fails_when_client_missing(self):
        with patch("app.core.email.settings") as mock_settings:
            mock_settings.email_backend = "acs"
            mock_settings.acs_connection_string = "endpoint=https://x.communication.azure.com/;accesskey=dGVzdA=="
            mock_settings.acs_sender = "sender@example.com"
            service = EmailService()
        service.acs_email_client = None
        assert await service.send_email("user@example.com", "Subject", text_body="body") is False
