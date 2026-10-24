"""Deleting the old Replit account at the very start of a run (pass 4).

When Replit opens already signed in at https://replit.com/~, the run goes to
Settings > Profile, scrolls to "Request account deletion", confirms with "Yes,
Delete my Account", waits for the logged-out home page and only then signs up.
It must never do this after the NEW account exists.

Offline: the pages are scripted (fake_browser.FakeReplitSignup) on a virtual
clock. They prove the decision logic and the safety guards only. What the live
settings page looks like and how long Replit takes was NOT observed.
"""

import contextlib
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import account_ledger
from fake_browser import FakeReplitContext, FakeReplitSignup, VirtualClock
from pc_automation import PCAutomation

PROJECT = Path(__file__).resolve().parent


class StaleAccountTestCase(unittest.TestCase):
    EMAIL = "mail@example.test"          # the address of THIS run
    OLD = "old.account@example.test"     # the account signed in at the start
    DELETE_ENV = {"AFK_EXISTING_SESSION_ACTION": "delete"}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = str(Path(self.temp.name) / "created_accounts.json")
        env = patch.dict(os.environ, {"AFK_ACCOUNT_LEDGER": self.ledger})
        env.start()
        self.addCleanup(env.stop)

    def run_signup(self, *, known=(), answers=(), own_profile=True, resumed=False,
                   before=None, **page_options):
        for address in known:
            account_ledger.record(address)
        clock = VirtualClock()
        page_options.setdefault("start_state", "authenticated")
        page = FakeReplitSignup(clock, **page_options)
        automation = PCAutomation(self.EMAIL, "S3cret-pass")
        automation.page = page
        automation.context = FakeReplitContext(page)
        automation._persistent = own_profile
        automation.signup_resumed = resumed
        automation.log = lambda message, color=None: self.logs.append(str(message))
        automation._save_failure = Mock()
        if before:
            before(page)
        prompts, queue = [], list(answers)

        def ask(prompt=""):
            prompts.append(prompt)
            return queue.pop(0) if queue else ""

        self.logs = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("pc_automation.time", clock))
            stack.enter_context(patch("builtins.input", side_effect=ask))
            stack.enter_context(patch("operator_alert.beep"))
            stack.enter_context(patch("operator_alert.enter_pressed", return_value=False))
            stack.enter_context(patch("operator_alert.discard_pending_input"))
            stack.enter_context(patch.dict(os.environ, self.DELETE_ENV))
            ok = automation.create_account()
        return ok, page, automation, prompts


class DeleteAtTheStartTests(StaleAccountTestCase):
    def test_an_account_this_tool_created_is_deleted_without_asking_then_signup_runs(self):
        ok, page, pc, prompts = self.run_signup(known=[self.OLD])
        self.assertTrue(ok)
        self.assertEqual(prompts, [])
        self.assertTrue(page.deleted)
        self.assertEqual(page.delete_request.clicks, 1)
        self.assertEqual(page.delete_confirm.clicks, 1)
        self.assertEqual(pc.context.cleared, [])  # nothing was signed out by cookie
        self.assertEqual(page.email.value, self.EMAIL)
        self.assertEqual(page.submit.clicks, 1)

    def test_the_settings_page_is_opened_directly_and_deletion_comes_before_signup(self):
        _ok, page, _pc, _prompts = self.run_signup(known=[self.OLD])
        self.assertIn("https://replit.com/settings/profile", page.goto_urls)
        labels = page.event_labels()
        order = [labels.index(step) for step in (
            "deletion dialog opened", "deletion confirmed",
            "logged out after the deletion", "submit",
        )]
        self.assertEqual(order, sorted(order))

    def test_a_blank_tab_that_ends_up_in_the_workspace_is_not_sent_to_sign_in_directly(self):
        # The reported fault: Replit opens at /~ a moment after the page loads, and
        # the old code had already jumped to the sign-up link by then.
        ok, page, _pc, _prompts = self.run_signup(
            known=[self.OLD], start_state="blank", logged_in=True, home_redirect_s=2.5
        )
        self.assertTrue(ok)
        self.assertTrue(page.deleted)
        settings_at = page.goto_urls.index("https://replit.com/settings/profile")
        self.assertFalse(
            [url for url in page.goto_urls[:settings_at] if url.startswith(page.DEEP_LINK)],
            page.goto_urls,
        )

    def test_the_late_redirect_to_the_workspace_is_waited_for_after_opening_the_home_page(self):
        clock = VirtualClock()
        page = FakeReplitSignup(clock, start_state="blank", logged_in=True, home_redirect_s=4.0)
        pc = PCAutomation(self.EMAIL, "pw")
        pc.page = page
        with patch("pc_automation.time", clock):
            page.goto("https://replit.com/")
            # One look straight after goto is too early: an empty replit.com/ page.
            self.assertNotEqual(pc.observe_replit_page(), "authenticated")
            self.assertEqual(pc._settle_replit_home(), "authenticated")
        self.assertEqual(page.url, "https://replit.com/~")
        self.assertEqual(page.goto_urls, ["https://replit.com/"])

    def test_a_logged_out_home_page_skips_all_of_it(self):
        ok, page, _pc, prompts = self.run_signup(start_state="landing", hydrate_s=0.0)
        self.assertTrue(ok)
        self.assertEqual(prompts, [])
        self.assertNotIn("https://replit.com/settings/profile", page.goto_urls)
        self.assertEqual(page.delete_request.clicks, 0)
        self.assertEqual(page.create_account.clicks, 1)

    def test_a_blank_logged_out_tab_skips_all_of_it(self):
        ok, page, _pc, _prompts = self.run_signup(start_state="blank")
        self.assertTrue(ok)
        self.assertFalse([url for url in page.goto_urls if "settings" in url])

    def test_the_address_is_matched_without_regard_to_case(self):
        ok, page, _pc, prompts = self.run_signup(known=["OLD.Account@Example.TEST"])
        self.assertTrue(ok)
        self.assertEqual(prompts, [])
        self.assertTrue(page.deleted)

    def test_a_settings_page_that_redirects_to_login_means_there_was_nothing_to_delete(self):
        def lose_the_session(page):
            original = page.goto

            def goto(url, wait_until=None):
                if "settings" in url:
                    page.logged_in = False
                original(url, wait_until)

            page.goto = goto

        ok, page, _pc, prompts = self.run_signup(known=[self.OLD], before=lose_the_session)
        self.assertTrue(ok)
        self.assertEqual(prompts, [])
        self.assertFalse(page.deleted)
        self.assertEqual(page.delete_request.clicks, 0)
        self.assertEqual(page.email.value, self.EMAIL)


class DeletionGuardTests(StaleAccountTestCase):
    def test_an_account_this_tool_did_not_create_needs_its_email_typed(self):
        ok, page, _pc, prompts = self.run_signup(answers=[self.OLD])
        self.assertTrue(ok)
        self.assertEqual(len(prompts), 1)
        self.assertIn(self.OLD, prompts[0])
        self.assertTrue(page.deleted)

    def test_the_typed_email_is_compared_without_regard_to_case(self):
        ok, page, _pc, _prompts = self.run_signup(answers=[self.OLD.upper()])
        self.assertTrue(ok)
        self.assertTrue(page.deleted)

    def test_a_wrong_or_empty_answer_keeps_the_account_and_stops_the_run(self):
        for answer in ("", "no", "yes", "someone.else@example.test"):
            with self.subTest(answer=answer):
                ok, page, pc, prompts = self.run_signup(answers=[answer])
                self.assertFalse(ok)
                self.assertEqual(len(prompts), 1)
                self.assertFalse(page.deleted)
                self.assertEqual(page.delete_request.clicks, 0)
                self.assertEqual(page.email.value, "")

    def test_an_unreadable_address_falls_back_to_the_username_as_the_confirmation(self):
        ok, page, _pc, prompts = self.run_signup(settings_email="", answers=["@oldaccount"])
        self.assertTrue(ok)
        self.assertIn("@oldaccount", prompts[0])
        self.assertTrue(page.deleted)
        ok, page, _pc, _prompts = self.run_signup(settings_email="", answers=["oldaccount-typo"])
        self.assertFalse(ok)
        self.assertFalse(page.deleted)

    def test_the_account_of_this_run_is_never_deleted(self):
        # Even when the ledger knows the address: it is the account being created.
        ok, page, _pc, prompts = self.run_signup(known=[self.EMAIL], settings_email=self.EMAIL)
        self.assertTrue(ok)  # kept: it is the sign-up that already happened
        self.assertEqual(prompts, [])
        self.assertFalse(page.deleted)
        self.assertEqual(page.delete_request.clicks, 0)
        self.assertEqual(page.delete_confirm.clicks, 0)

    def test_the_own_account_check_ignores_case(self):
        ok, page, _pc, _prompts = self.run_signup(settings_email=self.EMAIL.upper())
        self.assertTrue(ok)
        self.assertFalse(page.deleted)

    def test_a_resumed_signup_never_deletes_by_itself(self):
        ok, page, pc, prompts = self.run_signup(known=[self.OLD], resumed=True, answers=["k"])
        self.assertTrue(ok)
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("[d]elete", prompts[0])
        self.assertFalse(page.deleted)

    def test_a_resumed_signup_ignores_a_typed_d(self):
        ok, page, _pc, _prompts = self.run_signup(known=[self.OLD], resumed=True, answers=["d"])
        self.assertFalse(ok)  # "d" is not offered, so it counts as quit
        self.assertFalse(page.deleted)

    def test_the_keep_and_signout_settings_do_not_delete(self):
        for mode in ("keep", "signout"):
            with self.subTest(mode=mode), patch.dict(os.environ, {}):
                self.DELETE_ENV = {"AFK_EXISTING_SESSION_ACTION": mode}
                _ok, page, _pc, _prompts = self.run_signup(known=[self.OLD])
                self.assertFalse(page.deleted)
                self.assertEqual(page.delete_request.clicks, 0)
        self.DELETE_ENV = StaleAccountTestCase.DELETE_ENV

    def test_a_misspelt_setting_asks_instead_of_deleting(self):
        self.DELETE_ENV = {"AFK_EXISTING_SESSION_ACTION": "delte"}
        ok, page, _pc, prompts = self.run_signup(known=[self.OLD], answers=["q"])
        self.assertFalse(ok)
        self.assertEqual(len(prompts), 1)
        self.assertFalse(page.deleted)


class DeletionFailureTests(StaleAccountTestCase):
    def test_a_settings_page_without_the_delete_button_fails_with_evidence(self):
        ok, page, pc, _prompts = self.run_signup(known=[self.OLD], delete_button=False)
        self.assertFalse(ok)
        pc._save_failure.assert_any_call("stale_delete_button_not_found")
        self.assertFalse(page.deleted)
        self.assertEqual(page.email.value, "")

    def test_a_login_that_lingers_after_the_deletion_is_cleared_in_the_own_profile(self):
        ok, page, pc, _prompts = self.run_signup(known=[self.OLD], logout_lingers=True)
        self.assertTrue(ok)
        self.assertEqual(len(pc.context.cleared), 1)
        self.assertEqual(page.email.value, self.EMAIL)

    def test_a_login_that_lingers_in_an_attached_browser_fails(self):
        ok, page, pc, _prompts = self.run_signup(
            known=[self.OLD], logout_lingers=True, own_profile=False
        )
        self.assertFalse(ok)
        self.assertEqual(pc.context.cleared, [])
        pc._save_failure.assert_any_call("stale_delete_not_logged_out")
        self.assertEqual(page.email.value, "")

    def test_a_slow_logout_is_waited_for(self):
        ok, page, _pc, _prompts = self.run_signup(known=[self.OLD], logout_delay_s=60.0)
        self.assertTrue(ok)
        self.assertTrue(page.deleted)


class NeverAfterVerificationTests(StaleAccountTestCase):
    def test_a_new_account_that_is_signed_in_straight_after_submit_is_left_alone(self):
        ok, page, _pc, prompts = self.run_signup(start_state="landing", hydrate_s=0.0, outcome="workspace")
        self.assertEqual(prompts, [])
        self.assertTrue(page.logged_in)  # the new account is signed in at /~
        self.assertFalse(page.deleted)
        self.assertEqual(page.delete_request.clicks, 0)
        self.assertEqual(page.delete_confirm.clicks, 0)
        self.assertNotIn("https://replit.com/settings/profile", page.goto_urls)
        self.assertIn("submit", page.event_labels())
        self.assertTrue(ok or not ok)  # the verdict of sign-up itself is not what is tested here

    def test_the_deletion_code_is_only_reachable_from_the_start_of_create_account(self):
        sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in PROJECT.glob("*.py")
            if not path.name.startswith("test_") and path.name != "fake_browser.py"
        }
        calls = {
            name: len(re.findall(r"_delete_stale_account\(", text)) for name, text in sources.items()
        }
        # The definition and one call; nothing outside pc_automation.py.
        self.assertEqual({k: v for k, v in calls.items() if v}, {"pc_automation.py": 2})
        text = sources["pc_automation.py"]
        callers = [
            m.group(1)
            for m in re.finditer(r"def (\w+)\(.*?(?=\n    def |\Z)", text, re.S)
            if "self._delete_stale_account(" in m.group(0) and m.group(1) != "_delete_stale_account"
        ]
        self.assertEqual(callers, ["_handle_existing_session"])
        users = [
            m.group(1)
            for m in re.finditer(r"def (\w+)\(.*?(?=\n    def |\Z)", text, re.S)
            if "self._handle_existing_session(" in m.group(0)
            and m.group(1) != "_handle_existing_session"
        ]
        self.assertEqual(users, ["create_account"])
        for name, source in sources.items():
            if name != "pc_automation.py":
                self.assertNotIn("_handle_existing_session", source, name)

    def test_every_session_check_in_create_account_comes_before_the_submit_click(self):
        text = (PROJECT / "pc_automation.py").read_text(encoding="utf-8")
        body = text[text.index("    def create_account("):]
        body = body[: body.index("\n    def ")]
        submit = body.index("submit_btn.click()")
        for match in re.finditer(r"self\._handle_existing_session\(", body):
            self.assertLess(match.start(), submit)


if __name__ == "__main__":
    unittest.main()
