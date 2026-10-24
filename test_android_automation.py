import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from android_automation import (
    AndroidAutomation,
    SCREENSHOT_REFERENCES,
    classify_screen,
    canonical_android_state,
    encode_adb_text,
    node_is_actionable,
    parse_adb_devices,
    parse_bounds,
    screenshot_references_for_state,
)
from pathlib import Path
import re


def ui_node(text="", class_name="android.widget.TextView", **attributes):
    values = {
        "text": text,
        "class": class_name,
        "enabled": "true",
        "clickable": "true",
        "bounds": "[0,0][100,100]",
    }
    values.update(attributes)
    return ET.Element("node", values)


def ui_root(*nodes):
    root = ET.Element("hierarchy")
    for node in nodes:
        root.append(node)
    return root


class AndroidParsingTests(unittest.TestCase):
    def test_parse_bounds_rejects_malformed_or_empty_bounds(self):
        self.assertEqual(parse_bounds("[1,2][101,202]"), (1, 2, 101, 202))
        self.assertIsNone(parse_bounds("[1,2][1,202]"))
        self.assertIsNone(parse_bounds("not-bounds"))

    def test_parse_adb_devices_keeps_authorization_state(self):
        self.assertEqual(
            parse_adb_devices(
                "List of devices attached\n"
                "emulator-5554\tdevice\n"
                "other\tunauthorized\n"
            ),
            [("emulator-5554", "device"), ("other", "unauthorized")],
        )

    def test_actionable_node_requires_enabled_clickable_or_focusable(self):
        self.assertTrue(node_is_actionable(ui_node()))
        self.assertTrue(
            node_is_actionable(
                ui_node(clickable="false", focusable="true", class_name="android.widget.EditText")
            )
        )
        self.assertFalse(node_is_actionable(ui_node(enabled="false")))

    def test_press_key_uses_adb_keyevent_command(self):
        automation = AndroidAutomation()
        automation.last_adb_ok = True

        with patch.object(automation, "run_adb") as run_adb:
            self.assertTrue(automation.press_key(66))

        run_adb.assert_called_once_with(
            ["shell", "input", "keyevent", "66"],
            wait=0.1,
        )

    def test_encode_adb_text_preserves_common_email_and_password_characters(self):
        encoded = encode_adb_text("user+tag@example.com P@ss!<&>()")
        self.assertEqual(
            encoded,
            r"user\+tag\@example.com%sP\@ss\!\<\&\>\(\)",
        )

    def test_type_text_uses_the_central_encoder(self):
        automation = AndroidAutomation()
        automation.last_adb_ok = True
        with patch.object(automation, "run_adb") as run_adb:
            self.assertTrue(automation.type_text("user@example.com"))
        run_adb.assert_called_once_with(
            ["shell", "input", "text", r"user\@example.com"],
            wait=0.1,
        )

    def test_force_stop_requires_non_foreground_and_stopped_process(self):
        automation = AndroidAutomation()
        automation.last_adb_ok = True
        with patch.object(automation, "close_app", return_value=True):
            with patch.object(
                automation,
                "foreground_package",
                side_effect=["com.replit.app", "com.android.launcher"],
            ):
                with patch.object(
                    automation,
                    "package_running",
                    side_effect=[True, False],
                ):
                    self.assertTrue(automation.force_stop_and_verify("com.replit.app"))


class AndroidScrollAndTargetTests(unittest.TestCase):
    def automation(self):
        automation = AndroidAutomation(device_id="FAKE")
        automation.screen_w, automation.screen_h = 1000, 2000
        self.swipes = []
        automation.swipe = lambda *args: self.swipes.append(args) or True
        return automation

    def test_the_swipe_is_drawn_inside_the_biggest_scrollable_container(self):
        root = ui_root(
            ui_node("", scrollable="true", bounds="[0,1000][1000,2000]"),
            ui_node("", scrollable="true", bounds="[0,0][100,100]"),
        )
        self.assertEqual(AndroidAutomation.scroll_area(root), (0, 1000, 1000, 2000))
        automation = self.automation()
        automation.swipe_up(root)
        x, y1, x2, y2, duration = self.swipes[0]
        self.assertEqual((x, x2), (500, 500))
        self.assertTrue(1000 <= y2 < y1 <= 2000, self.swipes)  # finger moves up, inside it

    def test_without_a_scrollable_container_the_whole_screen_is_used(self):
        automation = self.automation()
        automation.swipe_up(ui_root(ui_node("Usage")))
        _x, y1, _x2, y2, _d = self.swipes[0]
        self.assertTrue(0 <= y2 < y1 <= 2000)

    def test_each_retry_uses_a_different_gesture(self):
        automation = self.automation()
        for attempt in range(3):
            automation.swipe_up(None, attempt)
        self.assertEqual(len(set(self.swipes)), 3)

    def test_profile_target_never_resolves_to_the_edit_profile_row(self):
        root = ui_root(
            ui_node("Edit Profile", **{"content-desc": "Edit Profile"}, bounds="[0,500][1000,600]"),
            ui_node("", **{"content-desc": "Profile, MI"}, bounds="[900,100][1000,200]"),
        )
        automation = AndroidAutomation(device_id="FAKE")
        matchers = [{"desc": "Profile", "partial": True}]
        self.assertEqual(
            automation._find_actionable(root, matchers, avoid=("edit profile",)).get("bounds"),
            "[900,100][1000,200]",
        )
        only_edit = ui_root(
            ui_node("Edit Profile", **{"content-desc": "Edit Profile"}, bounds="[0,500][1000,600]")
        )
        self.assertIsNone(
            automation._find_actionable(only_edit, matchers, avoid=("edit profile",))
        )
        self.assertIsNotNone(automation._find_actionable(only_edit, matchers))


class AndroidScreenClassificationTests(unittest.TestCase):
    def test_every_supplied_android_screenshot_has_a_semantic_state_reference(self):
        referenced_files = {
            Path(path).name
            for paths in SCREENSHOT_REFERENCES.values()
            for path in paths
        }
        supplied_files = {
            path.name
            for path in Path("attached_assets").glob("*.jpg")
            if re.match(r"^(7|8|9|1[0-9]|19\.5|2[0-4])_", path.name)
        }
        self.assertEqual(referenced_files, supplied_files)
        self.assertEqual(
            screenshot_references_for_state("email_login"),
            (
                "attached_assets/7_1790062961791.jpg",
                "attached_assets/8_1790062961792.jpg",
            ),
        )

    def test_email_login_matches_keyboard_visible_and_hidden_variants(self):
        for extra in (
            ui_node("Continue with Google"),
            ui_node("Keyboard hidden"),
        ):
            root = ui_root(
                ui_node("Continue With Email"),
                ui_node("Email or Username"),
                ui_node("Continue", class_name="android.widget.Button"),
                extra,
            )
            self.assertEqual(classify_screen(root), "email_login")

    def test_login_states_distinguish_invalid_and_processing(self):
        invalid = ui_root(
            ui_node("Invalid username or password."),
            ui_node("Password"),
            ui_node("Login", class_name="android.widget.Button"),
        )
        processing = ui_root(
            ui_node("Password"),
            ui_node("Login", class_name="android.widget.Button"),
            ui_node("", class_name="android.widget.ProgressBar"),
        )
        self.assertEqual(classify_screen(invalid), "invalid_credentials")
        self.assertEqual(classify_screen(processing), "login_processing")
        self.assertEqual(canonical_android_state("invalid_credentials"), "LOGIN_RETRY")

    def test_security_challenges_are_terminal_manual_states(self):
        root = ui_root(ui_node("Suspicious login detected"))
        self.assertEqual(classify_screen(root), "security_challenge")
        self.assertEqual(canonical_android_state("security_challenge"), "SECURITY_CHALLENGE")

    def test_the_permanent_recaptcha_notice_is_not_a_security_challenge(self):
        notice = (
            "This site is protected by reCAPTCHA Enterprise and the Google "
            "Privacy Policy and Terms of Service apply."
        )
        login = ui_root(
            ui_node("Continue With Email"),
            ui_node("Email or Username"),
            ui_node("Continue", class_name="android.widget.Button"),
            ui_node(notice),
        )
        self.assertEqual(classify_screen(login), "email_login")

        challenge = ui_root(
            ui_node("Continue With Email"),
            ui_node("Please complete the CAPTCHA"),
            ui_node(notice),
        )
        self.assertEqual(classify_screen(challenge), "security_challenge")

    def test_onboarding_and_logout_states_match_reference_screens(self):
        fixtures = {
            "Welcome to Replit! Let's get started...": "welcome_onboarding",
            "What's your name?": "name",
            "Let's create a username Available": "username",
            "How did you hear about Replit? Google search": "source",
            "What best describes you? Developer": "role",
            "Start your building journey Skip": "plan",
            "What are we working on today?": "main",
            "Usage Edit Profile": "profile_menu",
            "Are you sure you want to log out? LOG OUT": "logout_dialog",
        }
        for text, expected in fixtures.items():
            self.assertEqual(classify_screen(ui_root(ui_node(text))), expected)

    def test_the_settings_sheet_wins_over_the_home_screen_behind_it(self):
        # A modern uiautomator dump can contain every window, so the home
        # screen's title is still there while the sheet is open.
        behind = ui_node("What are we working on today?")
        for sheet in (
            (ui_node("Usage"), ui_node("Edit Profile")),
            (ui_node("Manage Account"), ui_node("Log Out")),
            (ui_node("Log Out"),),
        ):
            with self.subTest(sheet=[node.get("text") for node in sheet]):
                self.assertEqual(classify_screen(ui_root(behind, *sheet)), "profile_menu")

    def test_a_scrolled_sheet_is_still_the_sheet_when_the_top_rows_left_the_dump(self):
        behind = ui_node("What are we working on today?")
        scrolled = ui_root(
            behind,
            ui_node("THEME"), ui_node("Theme - Dark"),
            ui_node("Notifications"), ui_node("Help"), ui_node("Docs"),
        )
        self.assertEqual(classify_screen(scrolled), "profile_menu")

    def test_a_single_ordinary_word_does_not_turn_the_home_screen_into_the_sheet(self):
        home = ui_root(ui_node("What are we working on today?"), ui_node("Docs"))
        self.assertEqual(classify_screen(home), "main")
        self.assertEqual(
            classify_screen(ui_root(ui_node("What are we working on today?"))), "main"
        )

    def test_the_logout_dialog_still_beats_the_sheet_behind_it(self):
        dialog = ui_root(
            ui_node("Usage"), ui_node("Edit Profile"),
            ui_node("Are you sure you want to log out?"), ui_node("LOG OUT"),
        )
        self.assertEqual(classify_screen(dialog), "logout_dialog")

    def test_typographic_apostrophes_are_matched_like_plain_ones(self):
        # attached_assets/14, 15 and 16 print "Let" + U+2019 + "s" and "What" +
        # U+2019 + "s". Without the normalisation the welcome screen fell through
        # to the generic "Continue" screen and shared its tap budget with the
        # first one.
        right = "\N{RIGHT SINGLE QUOTATION MARK}"
        left = "\N{LEFT SINGLE QUOTATION MARK}"
        dots = "\N{HORIZONTAL ELLIPSIS}"
        fixtures = {
            f"Welcome to Replit! Let{right}s get started{dots}": "welcome_onboarding",
            f"What{right}s your name?": "name",
            f"Hi Mipok! Let{right}s create a username": "username",
            f"Welcome to Replit! Let{left}s get started": "welcome_onboarding",
        }
        for text, expected in fixtures.items():
            with self.subTest(text=text):
                self.assertEqual(classify_screen(ui_root(ui_node(text))), expected)


if __name__ == "__main__":
    unittest.main()