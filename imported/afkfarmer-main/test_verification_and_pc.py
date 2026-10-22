"""Regression tests for verification observation and PC browser safety.

All offline: pages and contexts are fakes. They prove the decision logic, not
that the live Replit or mailbox pages behave the same way.
"""

import unittest
from unittest.mock import Mock, patch

from browser_verification import verify_action_code_in_browser
from email_provider import EmailProviderError, verification_success_observed
from email_providers import OneSecMailProvider, create_email_provider
from pc_automation import PCAutomation
from temp_mail import TempMailOrgProvider

URL = "https://replit.com/action-code?token=abc"


class FakeBody:
    """Returns each text once, repeats the last; a None entry means the page closed."""

    def __init__(self, texts):
        self.texts = list(texts)

    def inner_text(self):
        value = self.texts[0]
        if len(self.texts) > 1:
            self.texts.pop(0)
        if value is None:
            raise RuntimeError("Target page, context or browser has been closed")
        return value


class FakeVerificationPage:
    def __init__(self, texts):
        self.body = FakeBody(texts)
        self.goto = Mock()
        self.close = Mock()

    def locator(self, selector):
        assert selector == "body"
        return self.body


def context_with(page):
    context = Mock()
    context.new_page.return_value = page
    return context


class VerificationPhraseTests(unittest.TestCase):
    def test_the_documented_success_text_is_recognised(self):
        self.assertTrue(verification_success_observed("Verifying email success."))
        self.assertTrue(verification_success_observed("Email verified"))

    def test_the_intermediate_state_is_not_success(self):
        self.assertFalse(verification_success_observed("Verifying email"))


@patch("browser_verification.time.sleep")
class BrowserVerifierTests(unittest.TestCase):
    def test_success_is_observed_in_the_rendered_page(self, _sleep):
        page = FakeVerificationPage(["Verifying email", "Verifying email success."])
        self.assertTrue(verify_action_code_in_browser(context_with(page), URL, 5))
        page.goto.assert_called_once()
        page.close.assert_called_once()

    def test_a_window_that_closes_after_verifying_counts_as_success(self, _sleep):
        page = FakeVerificationPage(["Verifying email", None])
        self.assertTrue(verify_action_code_in_browser(context_with(page), URL, 5))

    def test_a_window_that_closes_before_verifying_is_not_success(self, _sleep):
        page = FakeVerificationPage([None])
        with self.assertRaises(EmailProviderError):
            verify_action_code_in_browser(context_with(page), URL, 5)

    def test_an_unchanged_page_times_out_instead_of_passing(self, _sleep):
        page = FakeVerificationPage(["Welcome to Replit"])
        with patch("browser_verification.time.monotonic", side_effect=[0, 0, 0.5, 100]):
            with self.assertRaises(EmailProviderError):
                verify_action_code_in_browser(context_with(page), URL, 5)
        page.close.assert_called_once()

    def test_unexpected_destinations_are_rejected_before_any_navigation(self, _sleep):
        for bad in ("http://replit.com/action-code", "https://evil.example/action-code",
                    "https://replit.com/other", ""):
            with self.subTest(url=bad):
                context = Mock()
                with self.assertRaises(EmailProviderError):
                    verify_action_code_in_browser(context, bad, 5)
                context.new_page.assert_not_called()


class OneSecMailVerificationTests(unittest.TestCase):
    def provider(self, **kwargs):
        session = Mock()
        provider = OneSecMailProvider(session=session, **kwargs)
        provider.verification_url = URL
        return provider, session

    def test_http_alone_is_never_used_to_claim_verification(self):
        provider, session = self.provider()
        with self.assertRaises(EmailProviderError):
            provider.verify_email()
        session.get.assert_not_called()

    def test_verification_is_delegated_to_the_browser_verifier(self):
        verifier = Mock(return_value=True)
        provider, session = self.provider(verifier=verifier)
        self.assertTrue(provider.verify_email())
        verifier.assert_called_once_with(URL)
        session.get.assert_not_called()

    def test_a_negative_verifier_result_is_not_success(self):
        provider, _session = self.provider(verifier=Mock(return_value=False))
        self.assertFalse(provider.verify_email())

    def test_the_factory_supplies_a_browser_verifier_only_with_a_context(self):
        common = dict(strategy="api", primary_api="1secmail", custom_email="",
                      timeout_seconds=45, temp_mail_url="https://temp-mail.org/")
        with_context = create_email_provider(context=Mock(), **common)
        self.assertIsNotNone(with_context.verifier)
        without_context = create_email_provider(context=None, **common)
        self.assertIsNone(without_context.verifier)

    def test_the_mailbox_timeout_setting_reaches_the_browser_provider(self):
        provider = create_email_provider(
            context=Mock(), strategy="temp-mail.org", primary_api="1secmail",
            custom_email="", timeout_seconds=75, temp_mail_url="https://temp-mail.org/")
        self.assertIsInstance(provider, TempMailOrgProvider)
        self.assertEqual(provider.timeout_ms, 75_000)


class FakeButton:
    def __init__(self, label, visible=True):
        self.label = label
        self.visible = visible

    def is_visible(self, timeout=None):
        return self.visible

    def inner_text(self):
        return self.label


class FakeButtons:
    def __init__(self, buttons):
        self.buttons = buttons

    def count(self):
        return len(self.buttons)

    def nth(self, index):
        return self.buttons[index]


class PcSafetyTests(unittest.TestCase):
    def automation(self, *, owns_browser):
        automation = PCAutomation("a@b.test", "pw")
        automation.playwright = Mock()
        automation.browser = Mock()
        automation.browser.is_connected.return_value = True
        automation.context = Mock()
        automation._owns_browser = owns_browser
        return automation

    def test_an_attached_browsers_cookies_are_never_exported(self):
        automation = self.automation(owns_browser=False)
        context = automation.context
        automation.close_browser()
        context.storage_state.assert_not_called()

    def test_a_managed_browser_still_saves_its_own_session(self):
        automation = self.automation(owns_browser=True)
        context = automation.context
        automation.close_browser()
        context.storage_state.assert_called_once_with(path="auth_state.json")

    def test_the_popup_close_helper_never_picks_a_labeled_button(self):
        automation = PCAutomation("a@b.test", "pw")
        icon = FakeButton("")
        automation.page = Mock()
        automation.page.locator.return_value = FakeButtons(
            [FakeButton("Continue with Google"), FakeButton("Continue with Email"), icon]
        )
        self.assertIs(automation._icon_only_dialog_button(), icon)

    def test_the_popup_close_helper_returns_none_without_an_icon_button(self):
        automation = PCAutomation("a@b.test", "pw")
        automation.page = Mock()
        automation.page.locator.return_value = FakeButtons(
            [FakeButton("Continue with Google"), FakeButton("", visible=False)]
        )
        self.assertIsNone(automation._icon_only_dialog_button())


if __name__ == "__main__":
    unittest.main()
