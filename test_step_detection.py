"""Step and sub-step detection: the run looks at the page / phone before acting.

Offline: scripted Replit pages on a virtual clock, and a fake phone. These tests
prove the decision logic only; what the live sites show was not observed.
"""

import contextlib
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import stage_detect
from fake_browser import FakeReplitContext, FakeReplitSignup, VirtualClock
from pc_automation import PCAutomation
from pc_flow import classify_replit_page


class ClassifyReplitPageTests(unittest.TestCase):
    def test_each_point_of_the_flow_is_named(self):
        cases = [
            (dict(url="about:blank", text=""), "other"),
            (dict(url="https://replit.com/", text="Log in\nCreate account", create_button=True), "landing"),
            (dict(url="https://replit.com/", text="Continue with Email", chooser=True), "signup_chooser"),
            (dict(url="https://replit.com/", text="", fields=True), "signup_form"),
            (dict(url="https://replit.com/login", text="", fields=True), "login"),
            (dict(url="https://replit.com/login", text=""), "login"),
            (dict(url="https://replit.com/", text="Verify your email to start building"), "check_email"),
            (dict(url="https://replit.com/", text="What are we working on today?"), "authenticated"),
            (dict(url="https://replit.com/", text="x", challenge=True), "challenge"),
            (dict(url="https://replit.com/", text="Please complete the CAPTCHA"), "challenge"),
        ]
        for case, expected in cases:
            with self.subTest(expected=expected):
                url, text = case.pop("url"), case.pop("text")
                self.assertEqual(classify_replit_page(url, text, **case), expected)

    def test_the_permanent_recaptcha_notice_is_not_a_challenge(self):
        notice = (
            "This site is protected by reCAPTCHA Enterprise and the Google "
            "Privacy Policy and Terms of Service apply."
        )
        self.assertEqual(
            classify_replit_page("https://replit.com/", notice, fields=True), "signup_form"
        )


class InferPositionTests(unittest.TestCase):
    STATE = {"temp_email": "a@b.test", "username": "a"}

    def test_the_checkpoint_stands_without_evidence(self):
        position = stage_detect.infer_position("VERIFY", self.STATE)
        self.assertEqual((position.stage, position.moved), ("VERIFY", False))

    def test_a_phone_past_login_means_the_account_exists(self):
        for screen in ("welcome_onboarding", "name", "plan", "main", "profile_menu", "logout_dialog"):
            with self.subTest(screen=screen):
                position = stage_detect.infer_position(
                    "SIGNUP", self.STATE, phone_state=screen
                )
                self.assertEqual(position.stage, "ANDROID")
                self.assertTrue(position.moved)
                self.assertIn(screen, position.substage)

    def test_a_phone_still_at_login_proves_nothing(self):
        for screen in ("initial_login", "email_login", "password_login", "not_replit", "no_device"):
            with self.subTest(screen=screen):
                position = stage_detect.infer_position("SIGNUP", self.STATE, phone_state=screen)
                self.assertEqual(position.stage, "SIGNUP")

    def test_check_your_email_page_moves_signup_to_verify(self):
        position = stage_detect.infer_position("SIGNUP", self.STATE, web_kind="check_email")
        self.assertEqual((position.stage, position.moved), ("VERIFY", True))

    def test_evidence_never_moves_the_run_backwards(self):
        # The checkpoint says the phone step; a signup form on the web proves nothing about it.
        position = stage_detect.infer_position("ANDROID", self.STATE, web_kind="signup_form")
        self.assertEqual(position.stage, "ANDROID")
        position = stage_detect.infer_position("ANDROID", self.STATE, phone_state="main")
        self.assertEqual((position.stage, position.moved), ("ANDROID", False))
        position = stage_detect.infer_position("ANDROID", self.STATE, web_kind="check_email")
        self.assertEqual(position.stage, "ANDROID")

    def test_nothing_is_skipped_without_the_saved_address(self):
        position = stage_detect.infer_position("SIGNUP", {}, phone_state="main")
        self.assertEqual(position.stage, "EMAIL")
        position = stage_detect.infer_position("VERIFY", {"temp_email": None}, web_kind="check_email")
        self.assertEqual(position.stage, "EMAIL")

    def test_a_finished_run_stays_finished(self):
        position = stage_detect.infer_position("DONE", self.STATE, phone_state="main")
        self.assertEqual(position.stage, "DONE")

    def test_unknown_checkpoint_names_start_over(self):
        self.assertEqual(stage_detect.infer_position("???", self.STATE).stage, "EMAIL")

    def test_substage_reports_the_point_inside_the_step(self):
        self.assertEqual(
            stage_detect.infer_position("SIGNUP", self.STATE, web_kind="signup_form").substage,
            "signup_form",
        )
        self.assertIn(
            "login",
            stage_detect.infer_position("ANDROID", self.STATE, phone_state="password_login").substage,
        )
        self.assertIn(
            "onboarding",
            stage_detect.infer_position("ANDROID", self.STATE, phone_state="role").substage,
        )


class SignupPositionTests(unittest.TestCase):
    """create_account() looks at the open Replit tab before it does anything."""

    EMAIL = "mail@example.test"

    def run_signup(self, *, answers=(), own_profile=False, resumed=False,
                   clearing_works=True, env=None, on_input=None, **page_options):
        clock = VirtualClock()
        page = FakeReplitSignup(clock, **page_options)
        automation = PCAutomation(self.EMAIL, "S3cret-pass")
        automation.page = page
        automation.context = FakeReplitContext(page, clearing_works=clearing_works)
        automation._persistent = own_profile
        automation.signup_resumed = resumed
        automation.log = lambda message, color=None: self.logs.append(str(message))
        automation._save_failure = Mock()
        prompts = []
        queue = list(answers)

        def ask(prompt=""):
            prompts.append(prompt)
            if on_input:
                on_input(page)
            return queue.pop(0) if queue else ""

        self.logs = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("pc_automation.time", clock))
            stack.enter_context(patch("builtins.input", side_effect=ask))
            stack.enter_context(patch("operator_alert.beep"))
            stack.enter_context(patch("operator_alert.enter_pressed", return_value=False))
            stack.enter_context(patch("operator_alert.discard_pending_input"))
            stack.enter_context(patch.dict(os.environ, env or {}))
            ok = automation.create_account()
        return ok, page, automation, prompts

    def test_the_check_your_email_page_means_signup_is_already_done(self):
        ok, page, _pc, prompts = self.run_signup(start_state="result")
        self.assertTrue(ok)
        self.assertEqual(page.goto_urls, [])
        self.assertEqual(page.submit.clicks, 0)
        self.assertEqual(prompts, [])

    def test_an_open_form_is_continued_in_place(self):
        ok, page, _pc, _prompts = self.run_signup(start_state="form")
        self.assertTrue(ok)
        self.assertEqual(page.goto_urls, [])          # the page was not reloaded
        self.assertEqual(page.create_account.clicks, 0)
        self.assertEqual(page.email.value, self.EMAIL)
        self.assertEqual(page.submit.clicks, 1)

    def test_an_open_chooser_is_continued_in_place(self):
        ok, page, _pc, _prompts = self.run_signup(start_state="chooser")
        self.assertTrue(ok)
        self.assertEqual(page.goto_urls, [])
        self.assertEqual(page.create_account.clicks, 0)
        self.assertEqual(page.email_option.clicks, 1)

    def test_a_landing_page_is_walked_from_the_start(self):
        ok, page, _pc, _prompts = self.run_signup(start_state="landing", hydrate_s=0.0)
        self.assertTrue(ok)
        self.assertEqual(page.create_account.clicks, 1)

    def test_a_blank_tab_navigates_to_replit_as_before(self):
        ok, page, _pc, _prompts = self.run_signup()
        self.assertTrue(ok)
        self.assertEqual(page.goto_urls[0], "https://replit.com/")

    def test_an_old_login_in_the_own_profile_is_signed_out_without_asking(self):
        ok, page, pc, prompts = self.run_signup(start_state="authenticated", own_profile=True)
        self.assertTrue(ok)
        self.assertEqual(prompts, [])
        self.assertEqual(len(pc.context.cleared), 1)
        self.assertEqual(page.email.value, self.EMAIL)  # the new account was then created

    def test_only_replit_cookies_are_cleared(self):
        _ok, _page, pc, _prompts = self.run_signup(start_state="authenticated", own_profile=True)
        domain = pc.context.cleared[0]["domain"]
        self.assertTrue(domain.search("replit.com"))
        self.assertFalse(domain.search("temp-mail.org"))

    def test_a_login_that_cannot_be_ended_fails_instead_of_signing_up_on_top_of_it(self):
        ok, page, pc, _prompts = self.run_signup(
            start_state="authenticated", own_profile=True, clearing_works=False
        )
        self.assertFalse(ok)
        self.assertEqual(page.email.value, "")
        pc._save_failure.assert_called_with("signup_signout_failed")

    def test_a_resumed_signup_asks_before_touching_the_login(self):
        ok, page, pc, prompts = self.run_signup(
            start_state="authenticated", own_profile=True, resumed=True, answers=["k"]
        )
        self.assertTrue(ok)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(pc.context.cleared, [])
        self.assertEqual(page.goto_urls, [])

    def test_quitting_at_the_question_stops_without_changes(self):
        ok, page, pc, _prompts = self.run_signup(
            start_state="authenticated", resumed=True, answers=["q"]
        )
        self.assertFalse(ok)
        self.assertEqual(pc.context.cleared, [])

    def test_a_browser_the_operator_attached_is_never_signed_out_by_the_script(self):
        # Not our profile: the login may be their own account.
        ok, page, pc, prompts = self.run_signup(
            start_state="authenticated", own_profile=False, answers=["s", ""],
            on_input=lambda page: setattr(page, "logged_in", False) or setattr(page, "state", "landing"),
        )
        self.assertEqual(pc.context.cleared, [])
        self.assertEqual(len(prompts), 2)  # the question, then "press ENTER when signed out"
        self.assertTrue(ok)

    def test_an_attached_browser_that_is_still_logged_in_after_the_wait_fails(self):
        ok, _page, pc, _prompts = self.run_signup(
            start_state="authenticated", own_profile=False, answers=["s", ""]
        )
        self.assertFalse(ok)
        self.assertEqual(pc.context.cleared, [])

    def test_the_keep_setting_skips_the_question(self):
        ok, _page, _pc, prompts = self.run_signup(
            start_state="authenticated", env={"AFK_EXISTING_SESSION_ACTION": "keep"}
        )
        self.assertTrue(ok)
        self.assertEqual(prompts, [])

    def test_a_challenge_on_the_open_page_is_handled_in_place(self):
        ok, page, _pc, _prompts = self.run_signup(
            start_state="form", challenge="frame", challenge_when="fill", human_s=2.0
        )
        self.assertTrue(ok)
        self.assertEqual(page.goto_urls, [])

    def test_observation_does_not_navigate_or_click(self):
        clock = VirtualClock()
        page = FakeReplitSignup(clock, start_state="landing", hydrate_s=0.0)
        pc = PCAutomation(self.EMAIL, "pw")
        pc.page = page
        self.assertEqual(pc.observe_replit_page(), "landing")
        self.assertEqual(page.goto_urls, [])
        self.assertEqual(page.create_account.clicks, 0)


if __name__ == "__main__":
    unittest.main()
