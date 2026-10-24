"""Android changes of pass 4, against the simulated phone.

* attached_assets/19.5: the optional Notifications sheet ("Not Now" only).
* attached_assets/15 and 16: the name and username are left exactly as Replit
  filled them in; only Continue is tapped.
* After the final "Log Out" tap the app may crash to the Android home screen or
  leave Replit's profile page stuck on screen; the run still has to finish.

Like the other Android tests these prove the state logic against a simulation.
They are not evidence that the live app behaves this way.
"""

import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import android_automation as aa
from android_automation import classify_screen, first_name_from_username
from test_android_onboarding import (
    EMAIL,
    EXPECTED_FULL_FLOW,
    PASSWORD,
    USERNAME,
    FakeReplitDevice,
    IsolatedCwdTestCase,
    actions,
    run_flow,
)


def sheet_only(*labels):
    """A bare UI dump holding just these texts (what a sparse dump looks like)."""

    root = ET.Element("hierarchy")
    for index, label in enumerate(labels):
        ET.SubElement(root, "node", {
            "text": label, "class": "android.widget.TextView",
            "bounds": f"[0,{index * 100}][1080,{index * 100 + 90}]",
            "enabled": "true", "clickable": "false", "focusable": "false",
        })
    return root


class NotificationsSheetTests(IsolatedCwdTestCase):
    REAL_TEXT = (
        "Notifications",
        "Receive important updates about your Projects and your account. "
        "Notifications can be turned off from Account settings at any time.",
        "Turn On Notifications",
        "Not Now",
    )

    def test_the_real_sheet_text_is_recognised(self):
        self.assertEqual(classify_screen(sheet_only(*self.REAL_TEXT)), "notifications_prompt")

    def test_the_sheet_is_recognised_with_the_home_screen_behind_it(self):
        # A modern uiautomator dump keeps the screen under the sheet; that screen
        # says "What are we working on today?" and must not win.
        for multi_window in (True, False):
            with self.subTest(multi_window=multi_window):
                device = FakeReplitDevice(start_screen="notifications", multi_window=multi_window)
                self.assertEqual(classify_screen(device.dump_ui()), "notifications_prompt")

    def test_the_home_screen_alone_is_not_the_sheet(self):
        device = FakeReplitDevice(start_screen="main")
        self.assertEqual(classify_screen(device.dump_ui()), "main")

    def test_the_settings_page_entry_called_notifications_is_not_the_sheet(self):
        # The settings sheet has a "Notifications" row (attached_assets/21-22).
        device = FakeReplitDevice(start_screen="profile_menu")
        self.assertEqual(classify_screen(device.dump_ui()), "profile_menu")

    def test_not_now_is_tapped_between_skip_and_the_profile_when_the_sheet_shows(self):
        device = FakeReplitDevice(notifications_prompt=True)
        self.assertTrue(run_flow(device))
        expected = list(EXPECTED_FULL_FLOW)
        expected.insert(expected.index("open_profile"), "notifications_not_now")
        self.assertEqual(actions(device), expected)
        self.assertEqual(device.evidence, [])

    def test_turn_on_notifications_is_never_tapped(self):
        device = FakeReplitDevice(notifications_prompt=True)
        self.assertTrue(run_flow(device))
        self.assertNotIn("notifications_on", device.taps)
        self.assertNotIn("scrim", device.taps)

    def test_nothing_is_waited_for_or_tapped_when_the_sheet_does_not_show(self):
        device = FakeReplitDevice(notifications_prompt=False)
        self.assertTrue(run_flow(device))
        self.assertEqual(actions(device), EXPECTED_FULL_FLOW)
        self.assertNotIn("notifications_not_now", device.taps)

    def test_a_run_resumed_on_the_sheet_dismisses_it_and_finishes(self):
        device = FakeReplitDevice(start_screen="notifications")
        self.assertTrue(run_flow(device))
        self.assertEqual(
            actions(device),
            ["notifications_not_now", "open_profile", "logout_menu", "confirm_logout"],
        )

    def test_a_sheet_that_ignores_not_now_fails_instead_of_looping(self):
        device = FakeReplitDevice(start_screen="notifications")
        device._on_action = lambda action: None  # the button does nothing
        self.assertFalse(run_flow(device))
        self.assertEqual(device.evidence, ["onboarding_stuck_notifications_not_now"])
        self.assertNotIn("notifications_on", device.taps)


class NameAndUsernameAreLeftAloneTests(IsolatedCwdTestCase):
    def test_the_name_replit_filled_in_is_not_typed_over(self):
        device = FakeReplitDevice()
        self.assertTrue(run_flow(device))
        self.assertNotIn("focus:first_name", device.taps)
        self.assertEqual(device.fields["first_name"], "Mipok")
        self.assertEqual(device.taps.count("name_continue"), 1)

    def test_a_name_that_differs_from_the_username_is_kept_and_the_profile_still_opens(self):
        device = FakeReplitDevice(display_name="Hehijej")
        self.assertTrue(run_flow(device))
        self.assertNotIn("focus:first_name", device.taps)
        self.assertEqual(device.fields["first_name"], "Hehijej")
        self.assertEqual(device._avatar(), "HE")
        # The avatar was found by the initials Replit actually shows.
        self.assertIn("open_profile", device.taps)
        self.assertEqual(device.evidence, [])

    def test_the_username_screen_only_gets_continue(self):
        device = FakeReplitDevice(start_screen="username")
        self.assertTrue(run_flow(device))
        self.assertEqual(device.taps[0], "username_continue")
        self.assertFalse([tap for tap in device.taps if tap.startswith("focus:")])

    def test_an_empty_name_field_is_filled_once_as_a_fallback(self):
        device = FakeReplitDevice(start_screen="name", prefill_name=False)
        self.assertTrue(run_flow(device))
        self.assertEqual(device.taps.count("focus:first_name"), 1)
        self.assertEqual(device.fields["first_name"], first_name_from_username(USERNAME))
        self.assertEqual(device.evidence, [])


class AfterLogoutTests(IsolatedCwdTestCase):
    """The logout tap worked, or seems to have; the app misbehaves afterwards."""

    def run_logout(self, **options):
        device = FakeReplitDevice(start_screen="logout_dialog", **options)
        return device, run_flow(device)

    def test_a_normal_logout_needs_no_relaunch(self):
        device, ok = self.run_logout()
        self.assertTrue(ok)
        self.assertEqual(device.opened, 1)
        self.assertEqual(device.evidence, [])

    def test_a_crash_to_the_home_screen_is_followed_by_a_relaunch_and_still_succeeds(self):
        device, ok = self.run_logout(after_logout="crash")
        self.assertTrue(ok)
        self.assertEqual(device.opened, 2)  # the start of the run plus one relaunch
        self.assertEqual(device.taps.count("confirm_logout"), 1)
        self.assertEqual(device.evidence, [])
        self.assertFalse(device.app_running)

    def test_a_slow_relaunch_is_waited_for(self):
        device, ok = self.run_logout(after_logout="crash", relaunch_ticks=25)
        self.assertTrue(ok)
        self.assertEqual(device.opened, 2)
        self.assertEqual(device.evidence, [])

    def test_a_crash_that_left_the_account_logged_in_logs_out_again(self):
        device, ok = self.run_logout(after_logout="crash_logged_in", relaunch_ticks=3)
        self.assertTrue(ok)
        self.assertEqual(device.taps.count("confirm_logout"), 2)
        self.assertIn("open_profile", device.taps)
        self.assertEqual(device.evidence, [])

    def test_a_profile_page_that_stays_on_screen_is_scrolled_up(self):
        device, ok = self.run_logout(after_logout="stuck_profile")
        self.assertTrue(ok)
        downward = [swipe for swipe in device.swipes if swipe[3] > swipe[1]]
        self.assertTrue(downward, device.swipes)
        self.assertEqual(device.taps.count("confirm_logout"), 1)
        self.assertEqual(device.evidence, [])

    def test_a_screen_nothing_recognises_is_trusted_on_the_tap_and_leaves_evidence(self):
        device, ok = self.run_logout(after_logout="never")
        self.assertTrue(ok)
        self.assertEqual(device.evidence, ["logout_unconfirmed"])
        self.assertEqual(device.taps.count("confirm_logout"), 1)

    def test_an_app_that_never_comes_back_is_opened_a_bounded_number_of_times(self):
        device, ok = self.run_logout(after_logout="crash", relaunch_works=False)
        self.assertTrue(ok)  # the logout tap itself was accepted
        self.assertEqual(device.opened, 1 + aa.POST_LOGOUT_RELAUNCH_LIMIT)
        self.assertEqual(device.evidence, ["logout_unconfirmed"])

    def test_the_whole_flow_survives_a_crash_at_the_end(self):
        device = FakeReplitDevice(after_logout="crash", relaunch_ticks=5, notifications_prompt=True)
        self.assertTrue(run_flow(device))
        self.assertEqual(device.fields["email"], EMAIL)
        self.assertEqual(device.fields["password"], PASSWORD)
        self.assertEqual(device.evidence, [])


if __name__ == "__main__":
    unittest.main()
