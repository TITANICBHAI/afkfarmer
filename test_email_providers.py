import unittest
from unittest.mock import Mock

from email_provider import EmailProvider, EmailProviderError
from email_providers import (
    FallbackEmailProvider,
    ManualEmailProvider,
    OneSecMailProvider,
    create_email_provider,
)
from temp_mail import TempMailOrgProvider


class DummyProvider(EmailProvider):
    def __init__(self, address=None, error=None):
        self.address = address
        self.error = error
        self.opened = False
        self.verified = False

    def obtain_address(self):
        if self.error:
            raise self.error
        return self.address

    def open_verification_message(self):
        self.opened = True
        return {"id": "message"}

    def verify_email(self):
        self.verified = True
        return True


class EmailProviderTests(unittest.TestCase):
    def test_custom_email_has_priority_and_never_needs_a_browser_context(self):
        provider = create_email_provider(
            context=None,
            strategy="hybrid",
            primary_api="1secmail",
            custom_email=" Owner@Example.com ",
            timeout_seconds=60,
            temp_mail_url="https://temp-mail.org/",
        )
        self.assertIsInstance(provider, ManualEmailProvider)
        self.assertEqual(provider.obtain_address(), "Owner@Example.com")

    def test_temp_mail_strategy_selects_browser_provider(self):
        context = Mock()
        provider = create_email_provider(
            context=context,
            strategy="temp-mail.org",
            primary_api="1secmail",
            custom_email="",
            timeout_seconds=60,
            temp_mail_url="https://temp-mail.org/",
        )
        self.assertIsInstance(provider, TempMailOrgProvider)

    def test_hybrid_fallback_is_used_only_when_address_creation_fails(self):
        primary = DummyProvider(error=EmailProviderError("API unavailable"))
        fallback = DummyProvider(address="fallback@example.test")
        provider = FallbackEmailProvider(primary, lambda: fallback)

        self.assertEqual(provider.obtain_address(), "fallback@example.test")
        self.assertIs(provider.active, fallback)
        provider.open_verification_message()
        self.assertTrue(fallback.opened)

    def test_api_provider_rejects_malformed_mailbox_payload(self):
        response = Mock(status_code=200)
        response.json.return_value = {"mailbox": "not-a-list"}
        session = Mock()
        session.get.return_value = response
        provider = OneSecMailProvider(session=session)
        with self.assertRaises(EmailProviderError):
            provider.obtain_address()


if __name__ == "__main__":
    unittest.main()