import os
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from pc_automation import PCAutomation


class FakeLocator:
    def __init__(self, page, visible=True, text=""):
        self.page = page
        self.visible = visible
        self.text = text
        self.value = None
        self.pressed = None

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.visible else 0

    def nth(self, _index):
        return self

    def is_visible(self, timeout=0):
        if hasattr(self.page, "visibility_timeouts"):
            self.page.visibility_timeouts.append(timeout)
        return self.visible

    def wait_for(self, state="visible", timeout=0):
        if state == "visible" and not self.visible:
            raise TimeoutError("locator is hidden")

    def click(self):
        return None

    def fill(self, value):
        self.value = value

    def press(self, key):
        self.pressed = key
        if key == "Enter":
            self.page.url = "https://replit.com/~/imported"

    def inner_text(self):
        return self.text


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


class SessionSyncPage:
    def __init__(self, states):
        self.states = list(states)
        self.url = "https://replit.com/"
        self.body_text = ""
        self.reload_count = 0
        self.waits = []
        self.screenshot_paths = []

    def reload(self, wait_until=None, timeout=0):
        self.reload_count += 1
        self.url, self.body_text = self.states.pop(0)

    def wait_for_load_state(self, state, timeout=0):
        self.waits.append((state, timeout))

    def wait_for_timeout(self, timeout):
        self.waits.append(("poll", timeout))

    def locator(self, selector):
        if selector == "body":
            body = FakeLocator(self)
            body.inner_text = lambda: self.body_text
            return body
        return FakeLocator(self, visible=False)

    def screenshot(self, path):
        self.screenshot_paths.append(path)


class SignupPage(FakePage):
    def __init__(
        self,
        body_text="Check your inbox for the verification email",
        dialog_text="Create an account\nPassword is valid",
    ):
        super().__init__(chat_visible=False)
        self.url = "https://replit.com/signup"
        self.body_text = body_text
        self.dialog_text = dialog_text
        self.visibility_timeouts = []
        self.function_timeouts = []
        self.email_field = FakeLocator(self)
        self.password_field = FakeLocator(self)
        self.control = FakeLocator(self, visible=True)
        self.error_marker = FakeLocator(
            self,
            visible=True,
            text="Password is valid",
        )

    def goto(self, url, wait_until=None):
        self.url = url

    def wait_for_load_state(self, state, timeout=0):
        return None

    def wait_for_function(self, _script, timeout=0, **_kwargs):
        self.function_timeouts.append(timeout)
        return None

    def locator(self, selector):
        if selector == "body":
            body = FakeLocator(self)
            body.inner_text = lambda: self.body_text
            return body
        if selector in {"[role='dialog']", "dialog", "form"}:
            return FakeLocator(self, visible=True, text=self.dialog_text)
        if selector in {
            "[role='alert']",
            "[aria-invalid='true']",
            "[data-testid*='error' i]",
        }:
            return self.error_marker
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

    def test_explicit_cdp_endpoint_skips_local_port_probe(self):
        automation = PCAutomation("mail@example.test", "password")

        with patch.dict(
            "os.environ",
            {"PLAYWRIGHT_CDP_URL": "http://127.0.0.1:9876"},
            clear=False,
        ):
            with patch("pc_automation.urlopen") as urlopen:
                self.assertEqual(
                    automation._candidate_cdp_urls(),
                    ["http://127.0.0.1:9876"],
                )

        urlopen.assert_not_called()

    def test_edge_profile_directory_uses_selected_user_data_root(self):
        automation = PCAutomation("mail@example.test", "password")

        with patch("pc_automation.os.name", "nt"):
            with patch.dict(
                "os.environ",
                {
                    "LOCALAPPDATA": r"C:\Users\DELL\AppData\Local",
                    "PLAYWRIGHT_PROFILE_DIRECTORY": "Profile 2",
                },
                clear=False,
            ):
                self.assertEqual(
                    automation._profile_directory(),
                    "Profile 2",
                )
                self.assertEqual(
                    automation._profile_user_data_dir(
                        executable_path=(
                            r"C:\Program Files (x86)\Microsoft\Edge"
                            r"\Application\msedge.exe"
                        )
                    ),
                    os.path.join(
                        r"C:\Users\DELL\AppData\Local",
                        "Microsoft",
                        "Edge",
                        "User Data",
                    ),
                )

    def test_edge_profiles_resolve_friendly_name_and_last_used_profile(self):
        automation = PCAutomation("mail@example.test", "password")

        with tempfile.TemporaryDirectory() as profile_root:
            with open(
                os.path.join(profile_root, "Local State"),
                "w",
                encoding="utf-8",
            ) as state_file:
                json.dump(
                    {
                        "profile": {
                            "last_used": "Profile 2",
                            "info_cache": {
                                "Default": {"name": "Personal"},
                                "Profile 2": {"name": "Work"},
                            },
                        }
                    },
                    state_file,
                )

            profiles = automation.discover_browser_profiles(profile_root)
            self.assertEqual(profiles[0]["directory"], "Profile 2")
            self.assertEqual(profiles[0]["name"], "Work")

            with patch.dict(
                "os.environ",
                {
                    "PLAYWRIGHT_PROFILE_DIRECTORY": "",
                    "PLAYWRIGHT_PROFILE_NAME": "Work",
                },
                clear=False,
            ):
                self.assertEqual(
                    automation._select_profile(profile_root),
                    ("Profile 2", "Work"),
                )

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
        self.assertEqual(page.url, PCAutomation.REPLIT_URL)
        self.assertIn(PCAutomation.SIGNUP_RESULT_WAIT_MS, page.function_timeouts)
        self.assertEqual(page.email_field.value, "mail@example.test")
        self.assertEqual(page.password_field.value, "password")

    def test_signup_mock_rejects_visible_validation_error(self):
        page = SignupPage(dialog_text="Create an account\nEmail is required")
        automation = self.make_automation(page)

        with patch.object(automation, "_save_failure") as save_failure:
            self.assertFalse(automation.create_account())

        save_failure.assert_called_once_with("signup_validation_before_submit")

    def test_signup_ignores_non_error_validation_marker_in_valid_modal(self):
        page = SignupPage()
        automation = self.make_automation(page)

        self.assertTrue(automation.create_account())
        self.assertEqual(page.error_marker.inner_text(), "Password is valid")

    def test_session_sync_reloads_until_authenticated_state_is_observed(self):
        page = SessionSyncPage(
            [
                ("https://replit.com/", "Loading workspace"),
                ("https://replit.com/~/home", "Personal workspace"),
            ]
        )
        automation = self.make_automation(page)

        self.assertEqual(
            automation.wait_for_session_sync(timeout=5_000, poll_interval=1),
            "authenticated",
        )
        self.assertEqual(page.reload_count, 2)
        self.assertIn("poll", [kind for kind, _timeout in page.waits])

    def test_session_sync_timeout_remains_unknown_and_saves_evidence(self):
        page = SessionSyncPage(
            [("https://replit.com/", "Still loading")]
        )
        automation = self.make_automation(page)

        with patch.object(automation, "_save_failure") as save_failure:
            self.assertEqual(
                automation.wait_for_session_sync(timeout=0),
                "unknown",
            )

        save_failure.assert_called_once_with("session_sync_timeout")

    def test_close_browser_does_not_raise_when_managed_browser_is_already_gone(self):
        automation = PCAutomation("mail@example.test", "password")
        browser = Mock()
        browser.is_connected.return_value = True
        browser.close.side_effect = RuntimeError("Target page already closed")
        playwright = Mock()
        automation.browser = browser
        automation.playwright = playwright
        automation._owns_browser = True

        with patch.object(automation, "save_state"):
            automation.close_browser()

        browser.close.assert_called_once()
        playwright.stop.assert_called_once()
        self.assertIsNone(automation.browser)
        self.assertIsNone(automation.playwright)

    def test_close_browser_never_closes_an_attached_browser(self):
        automation = PCAutomation("mail@example.test", "password")
        browser = Mock()
        playwright = Mock()
        automation.browser = browser
        automation.playwright = playwright
        automation._attached_to_browser = True
        automation._owns_browser = False

        with patch.object(automation, "save_state"):
            automation.close_browser()

        browser.close.assert_not_called()
        playwright.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()