import unittest
from unittest.mock import Mock, patch

from pc_automation import PCAutomation


class FakeLocator:
    def __init__(self, page, visible=True):
        self.page = page
        self.visible = visible
        self.value = None
        self.pressed = None

    @property
    def first(self):
        return self

    def is_visible(self, timeout=0):
        return self.visible

    def fill(self, value):
        self.value = value

    def press(self, key):
        self.pressed = key
        if key == "Enter":
            self.page.url = "https://replit.com/~/imported"


class FakePage:
    def __init__(self, *, chat_visible=True, wait_succeeds=True):
        self.url = "https://replit.com/"
        self.chat = FakeLocator(self, visible=chat_visible)
        self.wait_succeeds = wait_succeeds
        self.screenshot_paths = []

    def locator(self, selector):
        if selector.startswith("textarea") or selector.startswith("input[placeholder"):
            return self.chat
        return FakeLocator(self, visible=False)

    def wait_for_function(self, _script, timeout=0, **_kwargs):
        if not self.wait_succeeds:
            raise TimeoutError("import state not observed")

    def screenshot(self, path):
        self.screenshot_paths.append(path)


class SignupPage(FakePage):
    def __init__(self, body_text="Check your inbox for the verification email"):
        super().__init__(chat_visible=False)
        self.url = "https://replit.com/signup"
        self.body_text = body_text
        self.email_field = FakeLocator(self)
        self.password_field = FakeLocator(self)
        self.control = FakeLocator(self, visible=True)

    def goto(self, url, wait_until=None):
        self.url = url

    def wait_for_load_state(self, state, timeout=0):
        return None

    def wait_for_function(self, _script, timeout=0, **_kwargs):
        return None

    def locator(self, selector):
        if selector == "body":
            body = FakeLocator(self)
            body.inner_text = lambda: self.body_text
            return body
        if "input[type='email']" in selector or "input[name='email']" in selector:
            return self.email_field
        if "input[type='password']" in selector or "input[name='password']" in selector:
            return self.password_field
        if "button:has-text" in selector or "button[type='submit']" in selector:
            return self.control
        return FakeLocator(self, visible=False)


class PcAutomationMockTests(unittest.TestCase):
    def make_automation(self, page):
        automation = PCAutomation("mail@example.test", "password")
        automation.page = page
        return automation

    def test_import_requires_visible_control_and_observed_project_url(self):
        page = FakePage(chat_visible=True)
        automation = self.make_automation(page)

        with patch("pc_automation.PlaywrightTimeoutError", TimeoutError):
            self.assertTrue(
                automation.import_github_repo(
                    "https://github.com/example/project"
                )
            )

        self.assertEqual(page.chat.value, "Import this GitHub repository: https://github.com/example/project")
        self.assertEqual(page.chat.pressed, "Enter")
        self.assertIn("/~/", page.url)

    def test_import_control_missing_is_failure_and_saves_evidence(self):
        page = FakePage(chat_visible=False)
        automation = self.make_automation(page)

        with patch.object(automation, "_save_failure") as save_failure:
            self.assertFalse(
                automation.import_github_repo(
                    "https://github.com/example/project"
                )
            )

        save_failure.assert_called_once_with("github_import_control_not_found")

    def test_import_timeout_is_failure_and_saves_evidence(self):
        page = FakePage(chat_visible=True, wait_succeeds=False)
        automation = self.make_automation(page)

        with patch("pc_automation.PlaywrightTimeoutError", TimeoutError):
            with patch.object(automation, "_save_failure") as save_failure:
                self.assertFalse(
                    automation.import_github_repo(
                        "https://github.com/example/project"
                    )
                )

        save_failure.assert_called_once_with("github_import_result_timeout")

    def test_signup_mock_requires_observed_result_and_fills_credentials(self):
        page = SignupPage()
        automation = self.make_automation(page)

        self.assertTrue(automation.create_account())
        self.assertEqual(page.email_field.value, "mail@example.test")
        self.assertEqual(page.password_field.value, "password")

    def test_signup_mock_rejects_visible_validation_error(self):
        page = SignupPage(body_text="Email is required")
        automation = self.make_automation(page)

        with patch.object(automation, "_save_failure") as save_failure:
            self.assertFalse(automation.create_account())

        save_failure.assert_called_once_with("signup_validation_before_submit")


if __name__ == "__main__":
    unittest.main()