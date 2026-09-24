import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "state.json"
        self.original_state_file = main.STATE_FILE
        main.STATE_FILE = str(self.state_path)

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

        with patch("builtins.input", return_value="y"):
            with patch.object(orchestrator, "run_stage", side_effect=fake_run_stage):
                with patch("main.time.sleep"):
                    orchestrator.run()

        self.assertEqual(called, main.STAGES[main.STAGES.index("ANDROID"):-1])
        self.assertNotIn("EMAIL", called)


if __name__ == "__main__":
    unittest.main()