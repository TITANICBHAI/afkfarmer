import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from android_automation import (
    AndroidAutomation,
    SCREENSHOT_REFERENCES,
    classify_screen,
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
            if re.match(r"^(7|8|9|1[0-9]|2[0-4])_", path.name)
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


if __name__ == "__main__":
    unittest.main()