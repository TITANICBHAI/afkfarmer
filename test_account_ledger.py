"""The list of accounts this tool created, and the page-reading helpers that decide
whether an account may be deleted. Pure functions and a temp file; no browser."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import account_ledger
from pc_flow import is_profile_settings_text, is_workspace_url, parse_account_identity


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.json"
        env = patch.dict(os.environ, {"AFK_ACCOUNT_LEDGER": str(self.path)})
        env.start()
        self.addCleanup(env.stop)

    def test_nothing_is_known_without_a_file(self):
        self.assertEqual(account_ledger.load(), [])
        self.assertFalse(account_ledger.is_known("a@b.test"))

    def test_a_recorded_address_is_known_in_any_case(self):
        self.assertTrue(account_ledger.record("  Made.By@Tool.test "))
        self.assertTrue(account_ledger.is_known("made.by@tool.test"))
        self.assertTrue(account_ledger.is_known("MADE.BY@TOOL.TEST"))
        self.assertFalse(account_ledger.is_known("someone@else.test"))

    def test_recording_twice_keeps_one_entry(self):
        account_ledger.record("a@b.test")
        account_ledger.record("A@B.test")
        self.assertEqual(account_ledger.load(), ["a@b.test"])

    def test_empty_values_are_never_recorded_or_known(self):
        for value in (None, "", "   "):
            self.assertFalse(account_ledger.record(value))
            self.assertFalse(account_ledger.is_known(value))
        self.assertFalse(self.path.exists())

    def test_a_damaged_file_counts_as_empty_and_is_replaced_by_the_next_record(self):
        for broken in ("{not json", "[]", '{"addresses": 5}', ""):
            with self.subTest(broken=broken):
                self.path.write_text(broken, encoding="utf-8")
                self.assertEqual(account_ledger.load(), [])
                self.assertTrue(account_ledger.record("a@b.test"))
                self.assertEqual(account_ledger.load(), ["a@b.test"])

    def test_only_addresses_are_stored(self):
        account_ledger.record("a@b.test")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data, {"addresses": ["a@b.test"]})

    def test_no_temporary_files_are_left_behind(self):
        account_ledger.record("a@b.test")
        self.assertEqual([p.name for p in Path(self.temp.name).iterdir()], ["ledger.json"])

    def test_an_unwritable_place_reports_false_instead_of_raising(self):
        with patch.dict(os.environ, {"AFK_ACCOUNT_LEDGER": "/proc/nope/ledger.json"}):
            self.assertFalse(account_ledger.record("a@b.test"))


class WorkspaceUrlTests(unittest.TestCase):
    def test_the_tilde_address_is_the_workspace(self):
        for url in ("https://replit.com/~", "https://replit.com/~/", "https://www.replit.com/~",
                    "https://replit.com/~?x=1"):
            with self.subTest(url=url):
                self.assertTrue(is_workspace_url(url))

    def test_everything_else_is_not(self):
        for url in ("https://replit.com/-", "https://replit.com/", "https://replit.com/~user",
                    "https://replit.com/settings/profile", "https://example.com/~",
                    "about:blank", "", None):
            with self.subTest(url=url):
                self.assertFalse(is_workspace_url(url))


class AccountIdentityTests(unittest.TestCase):
    PAGE = (
        "Back to app\nProfile\n@oldaccount\nFirst name\nLast name\nLocation\nBio\n"
        "Your email\nold.account@example.test\nEdit\nYour password\nEdit\n"
        "Delete account\nRequest account deletion"
    )

    def test_the_email_is_the_one_under_your_email_and_the_username_is_the_handle(self):
        self.assertEqual(
            parse_account_identity(self.PAGE),
            {"email": "old.account@example.test", "username": "oldaccount"},
        )

    def test_an_address_elsewhere_on_the_page_is_not_taken_for_the_account_email(self):
        text = "Contact support@replit.com\n" + self.PAGE
        self.assertEqual(parse_account_identity(text)["email"], "old.account@example.test")

    def test_missing_pieces_are_none(self):
        self.assertEqual(parse_account_identity(""), {"email": None, "username": None})
        self.assertEqual(parse_account_identity(None), {"email": None, "username": None})
        self.assertIsNone(parse_account_identity("Your email\nEdit")["email"])

    def test_an_email_alone_is_not_a_username(self):
        self.assertIsNone(parse_account_identity("Your email\na@b.test")["username"])

    def test_the_settings_page_is_recognised_and_other_pages_are_not(self):
        self.assertTrue(is_profile_settings_text(self.PAGE))
        self.assertTrue(is_profile_settings_text("Your email\nFirst name"))
        self.assertFalse(is_profile_settings_text("What are we working on today?"))
        self.assertFalse(is_profile_settings_text(""))


if __name__ == "__main__":
    unittest.main()
