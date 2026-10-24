import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "state.json"
        self.original_state_file = main.STATE_FILE
        main.STATE_FILE = str(self.state_path)
        # The list of accounts this tool created must stay inside the test folder.
        self.ledger_path = Path(self.temp_dir.name) / "created_accounts.json"
        env = patch.dict(os.environ, {"AFK_ACCOUNT_LEDGER": str(self.ledger_path)})
        env.start()
        self.addCleanup(env.stop)
        # The failed-stage menu makes a sound; the tests must stay silent.
        beep = patch("operator_alert.beep")
        self.beep = beep.start()
        self.addCleanup(beep.stop)
        # Step detection must not touch a phone or browser of the developer.
        for target, value in (
            ("android_automation.AndroidAutomation.observe_screen", "no_device"),
            ("pc_automation.PCAutomation.attach_existing_only", False),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Nothing in these tests may open a browser window on the developer's machine.
        launch = patch(
            "pc_automation.PCAutomation.setup_browser",
            side_effect=RuntimeError("no browser in unit tests"),
        )
        launch.start()
        self.addCleanup(launch.stop)

    def tearDown(self):
        main.STATE_FILE = self.original_state_file
        self.temp_dir.cleanup()

    def make_orchestrator(self):
        return main.ReplitAutomationOrchestrator()

    def read_state(self):
        with self.state_path.open() as state_file:
            return json.load(state_file)

    def test_atomic_checkpoint_is_redacted_and_leaves_no_temp_file(self):
        orchestrator = self.make_orchestrator()
        orchestrator.state["password"] = "must-not-persist"
        orchestrator.state["temp_email"] = "mail@example.test"
        orchestrator.state["username"] = "mail"
        orchestrator.state["verification_link"] = "https://replit.com/action-code/redacted"
        orchestrator.state["github_repo"] = "https://github.com/example/example"

        orchestrator._save_state("SIGNUP")

        saved = self.read_state()
        self.assertEqual(saved["stage"], "SIGNUP")
        self.assertEqual(saved["password_ref"], main.PASSWORD_REF)
        self.assertNotIn("password", saved)
        self.assertEqual(saved["temp_email"], "mail@example.test")
        self.assertIsNone(saved["email_provider"])
        self.assertEqual(saved["username"], "mail")
        self.assertNotIn("verification_link", saved)
        self.assertEqual(
            saved["github_repo"],
            "https://github.com/example/example",
        )
        self.assertEqual(list(self.state_path.parent.glob(".state.*.tmp")), [])

    def test_legacy_password_is_removed_when_checkpoint_is_rewritten(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "stage": "ANDROID",
                    "password": "legacy-secret",
                    "temp_email": "mail@example.test",
                }
            )
        )

        orchestrator = self.make_orchestrator()
        self.assertNotIn("password", orchestrator.state)
        self.assertEqual(orchestrator.state["password_ref"], main.PASSWORD_REF)

        orchestrator._save_state("ANDROID")
        self.assertNotIn("password", self.read_state())

    def test_success_checkpoint_means_next_stage_and_clears_in_progress(self):
        orchestrator = self.make_orchestrator()

        self.assertTrue(orchestrator.run_stage("EMAIL", lambda: True))

        self.assertEqual(orchestrator.state["stage"], "SIGNUP")
        self.assertIsNone(orchestrator.state["in_progress"])
        self.assertEqual(self.read_state()["stage"], "SIGNUP")

    def test_retry_and_quit_are_distinct_recorded_outcomes(self):
        orchestrator = self.make_orchestrator()

        with patch("builtins.input", side_effect=["r", "q"]):
            self.assertFalse(orchestrator.run_stage("EMAIL", lambda: False))

        self.assertEqual(
            [entry["outcome"] for entry in orchestrator.state["decisions"]],
            ["retry", "quit"],
        )
        self.assertEqual(orchestrator.state["completion_status"], "FAILED")
        self.assertEqual(orchestrator.state["stage"], "EMAIL")
        self.assertEqual(orchestrator.state["in_progress"]["status"], "failed")

    def test_the_failed_stage_menu_makes_a_sound_each_time_before_it_asks(self):
        orchestrator = self.make_orchestrator()
        order = []
        self.beep.side_effect = lambda: order.append("beep")

        def ask(_prompt=""):
            order.append("ask")
            return "r" if order.count("ask") == 1 else "q"

        with patch("builtins.input", side_effect=ask):
            self.assertFalse(orchestrator.run_stage("EMAIL", lambda: False))

        # Two menus (retry, then quit): a sound ahead of each question.
        self.assertEqual(order, ["beep", "ask", "beep", "ask"])

    def test_a_stage_that_succeeds_makes_no_sound(self):
        orchestrator = self.make_orchestrator()

        self.assertTrue(orchestrator.run_stage("EMAIL", lambda: True))

        self.beep.assert_not_called()

    def test_manual_takeover_is_explicit_and_advances_checkpoint(self):
        orchestrator = self.make_orchestrator()

        with patch("builtins.input", side_effect=["m", ""]):
            self.assertTrue(orchestrator.run_stage("EMAIL", lambda: False))

        self.assertEqual(orchestrator.state["decisions"][-1]["outcome"], "manual_takeover")
        self.assertEqual(
            orchestrator.state["completion_status"],
            "MANUAL_COMPLETION",
        )
        self.assertEqual(orchestrator.state["stage"], "SIGNUP")
        self.assertIsNone(orchestrator.state["in_progress"])

    def test_skip_is_explicit_and_advances_checkpoint(self):
        orchestrator = self.make_orchestrator()

        with patch("builtins.input", return_value="s"):
            self.assertTrue(orchestrator.run_stage("EMAIL", lambda: False))

        self.assertEqual(orchestrator.state["decisions"][-1]["outcome"], "skip")
        self.assertEqual(orchestrator.state["completion_status"], "SKIPPED")
        self.assertEqual(orchestrator.state["stage"], "SIGNUP")
        self.assertIsNone(orchestrator.state["in_progress"])

    def test_interruption_before_completion_preserves_every_stage(self):
        for stage in main.STAGES[:-1]:
            with self.subTest(stage=stage):
                orchestrator = self.make_orchestrator()

                def interrupt():
                    raise KeyboardInterrupt()

                with self.assertRaises(KeyboardInterrupt):
                    orchestrator.run_stage(stage, interrupt)

                saved = self.read_state()
                self.assertEqual(saved["stage"], stage)
                self.assertEqual(saved["in_progress"]["stage"], stage)
                self.assertEqual(saved["in_progress"]["status"], "running")

    def test_success_after_every_stage_advances_exactly_once(self):
        for index, stage in enumerate(main.STAGES[:-1]):
            with self.subTest(stage=stage):
                orchestrator = self.make_orchestrator()
                self.assertTrue(orchestrator.run_stage(stage, lambda: True))

                self.assertEqual(
                    orchestrator.state["stage"],
                    main.STAGES[index + 1],
                )
                self.assertIsNone(orchestrator.state["in_progress"])

    def test_resume_from_later_stage_does_not_call_email_stage(self):
        orchestrator = self.make_orchestrator()
        orchestrator.state["stage"] = "ANDROID"
        orchestrator.state["temp_email"] = "mail@example.test"
        orchestrator.state["username"] = "mail"
        orchestrator._save_state("ANDROID")
        called = []

        def fake_run_stage(name, _handler):
            called.append(name)
            return True

        with patch("main.RUN_POST_ANDROID_STAGES", True):
            with patch("builtins.input", return_value="y"):
                with patch.object(orchestrator, "run_stage", side_effect=fake_run_stage):
                    with patch("main.time.sleep"):
                        orchestrator.run()

        self.assertEqual(called, main.STAGES[main.STAGES.index("ANDROID"):-1])
        self.assertNotIn("EMAIL", called)

    def test_shipped_default_ends_the_flow_after_android(self):
        # CORE_FLOW.md: "The process is complete after the Replit app has been
        # closed completely." PC login and GitHub import are opt-in.
        import config

        self.assertIs(config.RUN_POST_ANDROID_STAGES, False)

    def test_post_android_stages_can_be_disabled_explicitly(self):
        orchestrator = self.make_orchestrator()
        orchestrator.state["stage"] = "ANDROID"
        orchestrator.state["temp_email"] = "mail@example.test"
        orchestrator.state["username"] = "mail"
        called = []

        def fake_run_stage(name, _handler):
            called.append(name)
            return True

        with patch("main.RUN_POST_ANDROID_STAGES", False), \
            patch("builtins.input", return_value="y"), \
            patch.object(orchestrator, "run_stage", side_effect=fake_run_stage), \
            patch("main.time.sleep"):
            orchestrator.run()

        self.assertEqual(called, ["ANDROID"])
        self.assertEqual(orchestrator.state["stage"], "DONE")
        self.assertEqual(
            orchestrator.state["completion_status"],
            "AUTOMATED_SUCCESS",
        )


class DetectionTests(CheckpointTests):
    """The run finds its step from the phone / browser instead of only state.json."""

    def ready(self, stage, **extra):
        orchestrator = self.make_orchestrator()
        orchestrator.state.update(
            {"stage": stage, "temp_email": "mail@example.test", "username": "mail", **extra}
        )
        orchestrator._save_state(stage)
        return orchestrator

    def run_with(self, orchestrator, *, phone="no_device", web=None, answers=("y",)):
        called = []

        def fake_run_stage(name, _handler):
            called.append(name)
            orchestrator._save_state(orchestrator._next_stage(name))
            return True

        pc_probe = patch("pc_automation.PCAutomation.attach_existing_only", return_value=web is not None)
        page_probe = patch("pc_automation.PCAutomation.observe_replit_page", return_value=web)
        phone_probe = patch(
            "android_automation.AndroidAutomation.observe_screen", return_value=phone
        )
        with pc_probe, page_probe, phone_probe, \
                patch("builtins.input", side_effect=list(answers)), \
                patch("main.RUN_POST_ANDROID_STAGES", False), \
                patch.object(orchestrator, "run_stage", side_effect=fake_run_stage), \
                patch("main.time.sleep"):
            orchestrator.run()
        return called

    def test_a_phone_past_login_skips_signup_and_verification(self):
        orchestrator = self.ready("SIGNUP")
        called = self.run_with(orchestrator, phone="role")
        self.assertEqual(called, ["ANDROID"])
        outcomes = [d["outcome"] for d in orchestrator.state["decisions"]]
        self.assertIn("autodetected:ANDROID", outcomes)

    def test_the_check_your_email_page_continues_at_verification(self):
        orchestrator = self.ready("SIGNUP")
        called = self.run_with(orchestrator, web="check_email")
        self.assertEqual(called, ["VERIFY", "ANDROID"])

    def test_without_evidence_the_checkpoint_decides(self):
        orchestrator = self.ready("VERIFY")
        called = self.run_with(orchestrator, phone="no_device", web=None)
        self.assertEqual(called, ["VERIFY", "ANDROID"])

    def test_the_run_does_not_go_backwards_on_a_signup_page(self):
        orchestrator = self.ready("ANDROID")
        called = self.run_with(orchestrator, web="signup_form")
        self.assertEqual(called, ["ANDROID"])

    def test_a_checkpoint_without_an_address_starts_over_at_the_email_step(self):
        orchestrator = self.make_orchestrator()
        orchestrator.state["stage"] = "VERIFY"
        orchestrator._save_state("VERIFY")
        called = self.run_with(orchestrator, answers=("y",))
        self.assertEqual(called[0], "EMAIL")

    def test_detection_can_be_switched_off(self):
        orchestrator = self.ready("SIGNUP")
        with patch("main.AUTO_DETECT_STAGE", False):
            called = self.run_with(orchestrator, phone="role")
        self.assertEqual(called, ["SIGNUP", "VERIFY", "ANDROID"])

    def test_detection_looks_but_never_starts_a_browser_or_touches_the_phone(self):
        orchestrator = self.ready("SIGNUP")
        with patch("pc_automation.PCAutomation.setup_browser") as setup, \
                patch.object(orchestrator.android, "complete_onboarding") as onboarding:
            orchestrator.detect_position("SIGNUP")
        setup.assert_not_called()
        onboarding.assert_not_called()

    def test_after_a_manual_takeover_the_run_continues_from_where_the_screens_are(self):
        orchestrator = self.ready("SIGNUP")
        with patch("builtins.input", side_effect=["m", ""]), \
                patch("android_automation.AndroidAutomation.observe_screen", return_value="main"):
            self.assertTrue(orchestrator.run_stage("SIGNUP", lambda: False))
        self.assertEqual(orchestrator.state["stage"], "ANDROID")  # not VERIFY

    def test_after_a_manual_takeover_without_evidence_the_next_stage_follows(self):
        orchestrator = self.ready("SIGNUP")
        with patch("builtins.input", side_effect=["m", ""]):
            self.assertTrue(orchestrator.run_stage("SIGNUP", lambda: False))
        self.assertEqual(orchestrator.state["stage"], "VERIFY")

    def test_verification_that_was_already_completed_is_not_repeated(self):
        orchestrator = self.ready("VERIFY", verification_completed=True)
        orchestrator.pc = None
        with patch.object(orchestrator, "_ensure_browser", side_effect=AssertionError("no browser needed")):
            self.assertTrue(orchestrator.stage_verify())

    def test_a_resumed_signup_is_told_apart_from_a_fresh_one(self):
        # (stage the previous process died in, attempts so far, form submitted in
        # this process, expected). A retry after a failure BEFORE the form was
        # submitted is not a resume: no new account can exist yet.
        for resumed_stage, attempts, submitted, expected in (
            (None, 1, False, False), ("SIGNUP", 1, False, True), ("EMAIL", 1, False, False),
            (None, 2, False, False), (None, 2, True, True),
        ):
            with self.subTest(resumed_stage=resumed_stage, attempts=attempts, submitted=submitted):
                orchestrator = self.ready("SIGNUP")
                orchestrator._resumed_stage = resumed_stage
                orchestrator._stage_attempts = {"SIGNUP": attempts}
                orchestrator.pc = Mock()
                orchestrator.pc.context = object()
                orchestrator.pc.signup_submitted = submitted
                orchestrator.pc.create_account.return_value = True
                orchestrator.stage_signup()
                self.assertEqual(orchestrator.pc.signup_resumed, expected)

    def test_addresses_the_tool_obtained_are_listed_for_later_clean_up(self):
        import account_ledger

        orchestrator = self.ready("SIGNUP", temp_email="Made.By@Tool.test", email_provider="temp-mail.org")
        orchestrator._remember_account()
        self.assertTrue(account_ledger.is_known("made.by@tool.test"))

    def test_an_address_the_operator_brought_is_never_listed(self):
        import account_ledger

        orchestrator = self.ready("SIGNUP", temp_email="me@mine.test", email_provider="custom")
        orchestrator._remember_account()
        self.assertFalse(account_ledger.is_known("me@mine.test"))

    def test_the_previous_runs_address_is_listed_when_a_fresh_run_starts(self):
        import account_ledger

        orchestrator = self.ready("DONE", temp_email="old@tool.test", email_provider="temp-mail.org")
        orchestrator.reset_state()
        self.assertTrue(account_ledger.is_known("old@tool.test"))
        self.assertIsNone(orchestrator.state["temp_email"])

    def finish_run(self, orchestrator, setting, deleted=True):
        orchestrator.pc = Mock()
        orchestrator.pc.context = object()
        orchestrator.pc.keeps_browser_open = True
        orchestrator.pc.discard_mailbox.return_value = deleted
        with patch("main.DELETE_TEMP_MAIL", setting), \
                patch("main.RUN_POST_ANDROID_STAGES", False), \
                patch("builtins.input", return_value="y"), \
                patch.object(orchestrator, "run_stage", return_value=True):
            orchestrator.run()
        return orchestrator.pc.discard_mailbox

    def test_the_temp_mailbox_is_deleted_when_the_android_part_is_done(self):
        orchestrator = self.ready("ANDROID", email_provider="temp-mail.org")
        discard = self.finish_run(orchestrator, "after_android")
        discard.assert_called_once()
        self.assertTrue(self.read_state()["mailbox_deleted"])

    def test_the_mailbox_is_deleted_right_after_verification_when_configured(self):
        orchestrator = self.ready("VERIFY", email_provider="temp-mail.org")
        orchestrator.pc = Mock()
        orchestrator.pc.context = object()
        orchestrator.pc.verify_email.return_value = True
        orchestrator.pc.discard_mailbox.return_value = True
        with patch("main.DELETE_TEMP_MAIL", "after_verify"):
            self.assertTrue(orchestrator.stage_verify())
        orchestrator.pc.discard_mailbox.assert_called_once()
        # ...and not a second time at the end of the run.
        with patch("main.DELETE_TEMP_MAIL", "after_verify"):
            orchestrator._discard_mailbox_if_due("after_android")
        orchestrator.pc.discard_mailbox.assert_called_once()

    def test_the_default_waits_until_after_the_phone_part(self):
        orchestrator = self.ready("VERIFY", email_provider="temp-mail.org")
        orchestrator.pc = Mock()
        orchestrator.pc.context = object()
        orchestrator.pc.verify_email.return_value = True
        self.assertTrue(orchestrator.stage_verify())
        orchestrator.pc.discard_mailbox.assert_not_called()

    def test_never_means_never(self):
        orchestrator = self.ready("ANDROID", email_provider="temp-mail.org")
        self.finish_run(orchestrator, "never").assert_not_called()

    def test_an_address_the_operator_brought_is_not_deleted(self):
        orchestrator = self.ready("ANDROID", email_provider="custom")
        self.finish_run(orchestrator, "after_android").assert_not_called()

    def test_a_mailbox_that_could_not_be_deleted_never_fails_the_run(self):
        orchestrator = self.ready("ANDROID", email_provider="temp-mail.org")
        discard = self.finish_run(orchestrator, "after_android", deleted=False)
        discard.assert_called_once()
        self.assertEqual(orchestrator.state["completion_status"], "AUTOMATED_SUCCESS")
        self.assertFalse(orchestrator.state["mailbox_deleted"])

    def test_a_crash_while_deleting_never_fails_the_run(self):
        orchestrator = self.ready("ANDROID", email_provider="temp-mail.org")
        orchestrator.pc = Mock()
        orchestrator.pc.context = object()
        orchestrator.pc.discard_mailbox.side_effect = RuntimeError("page closed")
        with patch("main.DELETE_TEMP_MAIL", "after_android"):
            orchestrator._discard_mailbox_if_due("after_android")  # must not raise

    def test_a_browser_that_stays_open_is_not_held_for_a_30_second_review(self):
        orchestrator = self.ready("ANDROID")
        orchestrator.pc = Mock()
        orchestrator.pc.keeps_browser_open = True
        with patch("main.RUN_POST_ANDROID_STAGES", False), \
                patch("builtins.input", return_value="y"), \
                patch.object(orchestrator, "run_stage", return_value=True), \
                patch("main.time.sleep") as sleep:
            orchestrator.run()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()