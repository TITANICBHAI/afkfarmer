"""State-machine tests for the Android onboarding driver.

``FakeReplitDevice`` renders the screens captured in ``attached_assets`` as UI
hierarchies and resolves taps against node bounds, so the real driver runs
against a simulated phone. No ADB, device, or browser is needed. These tests
prove the state logic only; they are not evidence that the live app behaves the
same way.
"""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import android_automation as aa
from android_automation import (
    AndroidAutomation,
    actionable_target,
    avatar_initials,
    first_name_from_username,
    label_matches,
)

EMAIL = "mipok93125@example.test"
PASSWORD = "12345678"
USERNAME = "mipok93125"


def _row(index):
    return f"[100,{200 + index * 150}][980,{300 + index * 150}]"


SCREEN_W, SCREEN_H = 1080, 2400
SHEET_TOP = 100
# The settings sheet's rows in content coordinates, as in attached_assets/21-22:
# (kind, label, top, height, fake-action). "Log Out" ends below the 2400 px
# screen, so the list has to be scrolled before it exists in the UI dump.
SHEET_ROWS = (
    ("button", "MI", 140, 130, "sheet_avatar"),
    ("text", "Mipok", 150, 50, None),
    ("text", "@mipok93125", 205, 50, None),
    ("row", "Join Replit Core", 445, 130, "core"),
    ("row", "Usage", 600, 130, "usage"),
    ("text", "PROFILE", 800, 40, None),
    ("row", "Edit Profile", 880, 130, "edit_profile"),
    ("text", "THEME", 1090, 40, None),
    ("row", "Theme - Dark", 1170, 130, "theme"),
    ("text", "NOTIFICATIONS", 1380, 40, None),
    ("row", "Notifications", 1460, 130, "notifications"),
    ("text", "SUPPORT", 1670, 40, None),
    ("row", "Help", 1750, 130, "help"),
    ("row", "Docs", 1900, 130, "docs"),
    ("text", "OTHER", 2100, 40, None),
    ("row", "About", 2180, 130, "about"),
    ("row", "Manage Account", 2330, 130, "manage_account"),
    ("row", "Log Out", 2480, 130, "logout_menu"),
)
SHEET_MAX_SCROLL = 382  # the last row ends at 2610; the list scrolls a little past that


def _node(parent, text="", cls="android.widget.TextView", bounds="[0,0][1,1]",
          clickable=False, **extra):
    attributes = {
        "text": text,
        "class": cls,
        "bounds": bounds,
        "enabled": "true",
        "clickable": "true" if clickable else "false",
        "focusable": "true" if clickable else "false",
    }
    attributes.update(extra)
    return ET.SubElement(parent, "node", attributes)


class FakeReplitDevice(AndroidAutomation):
    """A simulated phone running the Replit app, driven through the real class."""

    def __init__(self, *, start_screen="launcher", display_name="Mipok",
                 reject_logins=0, confirm_label="LOG OUT", ignore_confirm=0,
                 already_onboarded=False, fields=None, expected_email=EMAIL,
                 email_sheet=None, unreadable_fields=False, multi_window=True,
                 sheet_top=SHEET_TOP, scroll_gain=0.12, ignore_slow_swipes=False,
                 background_after_taps=None, prefill_name=True, notifications_prompt=False,
                 after_logout="normal", relaunch_ticks=0, relaunch_works=True):
        """``email_sheet`` reproduces attached_assets/6 and 7: after "Continue with
        Email" the login sheet stays on top of the e-mail form and swallows the
        first tap, like a modal scrim. "keyboard_visible" keeps the field focused
        behind it (the screenshots); "keyboard_hidden" is the edge case in the
        flow notes ("tap once outside the login area") where the field is not.
        ``unreadable_fields`` makes the UI dump hide what is typed into text
        fields, as a masked or custom input could.

        The settings sheet (attached_assets/21-22) is a scrolling list whose
        "Log Out" row starts below the fold. ``multi_window`` keeps the home
        screen in the UI dump behind the open sheet (what a modern ``uiautomator
        dump`` can do); ``scroll_gain`` is how far the list moves per pixel of
        swipe; ``ignore_slow_swipes`` makes swipes of 600 ms or more do nothing;
        ``background_after_taps`` sends the app to the background after that many
        taps (an incoming call, a stray Home press).

        Replit fills the first name in from the e-mail (attached_assets/15), so
        ``prefill_name`` shows ``display_name`` on the name screen;
        ``prefill_name=False`` is the edge case of an empty field.
        ``notifications_prompt`` shows the optional Notifications sheet
        (attached_assets/19.5) after the plan screen's Skip.

        ``after_logout`` is what the phone does after the final "Log Out" tap:
        "normal" shows the login screen, "crash" falls back to the Android home
        screen (the app comes back logged out), "crash_logged_in" does the same
        but the logout never happened, "stuck_profile" leaves the profile page on
        screen until it is scrolled up, "never" shows a screen nothing
        recognises. ``relaunch_ticks`` is how many looks the app needs to load
        after it is opened again; ``relaunch_works=False`` never brings it back.
        """

        super().__init__(device_id="FAKE")
        self.multi_window = multi_window
        self.sheet_top = sheet_top
        self.scroll_gain = scroll_gain
        self.ignore_slow_swipes = ignore_slow_swipes
        self.background_after_taps = background_after_taps
        self.backgrounded = False
        self.scroll_offset = 0
        self.swipes = []
        self.keys = []
        self.wrong_first_open = False  # the first avatar tap lands on the wrong screen
        self.unreadable_fields = unreadable_fields
        self.email_sheet = email_sheet
        self.sheet_open = False
        self.expected_email = expected_email
        self.screen = start_screen
        self.app_running = start_screen != "launcher"
        self.display_name = display_name
        self.reject_logins = reject_logins
        self.confirm_label = confirm_label
        self.ignore_confirm = ignore_confirm
        self.already_onboarded = already_onboarded
        self.fields = {"email": "", "password": "", "first_name": ""}
        self.fields.update(fields or {})
        self.prefill_name = prefill_name
        self.notifications_prompt = notifications_prompt
        self.after_logout = after_logout
        self.relaunch_ticks = relaunch_ticks
        self.relaunch_works = relaunch_works
        self.crashed = False
        self.crash_return = "initial_login"
        self.splash_ticks = 0
        self.stuck_profile = False
        self.opened = 0
        if start_screen == "name":
            self._prefill()
        self.focus = None
        self.scrolled = False
        self.source_selected = False
        self.role_selected = False
        self.login_attempts = 0
        self.processing_ticks = 0
        self.taps = []
        self.evidence = []

    # ---- rendering -------------------------------------------------------

    def _prefill(self):
        if self.prefill_name and not self.fields["first_name"]:
            self.fields["first_name"] = self.display_name

    def _avatar(self):
        return (self.fields["first_name"] or self.display_name)[:2].upper()

    def _build(self):
        root = ET.Element("hierarchy")
        screen = self.screen

        def text(value, index):
            _node(root, value, bounds=_row(index))

        def button(value, action, index):
            _node(root, value, "android.widget.Button", _row(index), True,
                  **{"fake-action": action})

        def field(name, hint, index):
            shown = "" if self.unreadable_fields else self.fields[name]
            _node(root, shown, "android.widget.EditText", _row(index),
                  True, hint=hint, **{"fake-field": name})

        if screen == "launcher":
            text("Home screen", 0)
        elif screen == "security":
            text("Please solve the captcha to continue", 0)
        elif screen == "mystery":
            text("Hello world", 0)
        elif screen == "logged_out_page":
            text("Sign in", 0)
        elif screen == "initial_login":
            text("Replit", 0)
            button("Continue", "start_login", 5)
        elif screen == "email_choice":
            button("Continue with Google", "google", 4)
            button("Continue with Email", "choose_email", 5)
        elif screen == "email_choice_keyboard":
            field("email", "Your email", 1)
            button("Continue with Google", "google", 4)
            button("Continue with Email", "choose_email", 5)
            button("Continue", "submit_email", 6)
        elif screen == "email_login":
            text("Email or username", 0)
            field("email", "Email", 1)
            button("Continue", "submit_email", 5)
            if self.sheet_open:
                for label, action, index in (("Continue with Google", "google", 8),
                                             ("Continue with Email", "sheet_email", 9)):
                    _node(root, label, "android.widget.Button", _row(index), True,
                          **{"fake-action": action, "fake-layer": "sheet"})
        elif screen in {"password_login", "login_processing", "invalid_credentials"}:
            if screen == "invalid_credentials":
                text("Invalid username or password", 0)
            text("Login to your account", 1)
            field("password", "Password", 2)
            if screen == "login_processing":
                _node(root, "", "android.widget.ProgressBar", _row(4))
            button("Login", "submit_login", 5)
        elif screen == "welcome_onboarding":
            text("Welcome to Replit. Let's get started.", 0)
            button("Continue", "welcome_continue", 5)
        elif screen == "splash":
            _node(root, "", "android.widget.ProgressBar", _row(4))
        elif screen == "notifications":
            if self.multi_window:
                # The home screen stays in the dump behind the bottom sheet.
                text("What are we working on today?", 3)
                home = _node(root, "", "android.view.ViewGroup", "[900,100][1000,200]",
                             True, **{"fake-action": "open_profile", "fake-layer": "home"})
                _node(home, self._avatar(), bounds="[920,120][980,180]")
            text("Notifications", 6)
            text("Receive important updates about your Projects and your account. "
                 "Notifications can be turned off from Account settings at any time.", 7)
            button("Turn On Notifications", "notifications_on", 8)
            button("Not Now", "notifications_not_now", 9)
        elif screen == "name":
            text("What's your name?", 0)
            field("first_name", "First name", 1)
            # Like the real form, Continue is disabled while the name is empty.
            _node(root, "Continue", "android.widget.Button", _row(5), True,
                  enabled="true" if self.fields["first_name"] else "false",
                  **{"fake-action": "name_continue"})
        elif screen == "username":
            text("Let's create a username", 0)
            button("Continue", "username_continue", 5)
        elif screen == "source":
            text("How did you hear about Replit?", 0)
            button("Google search", "source_google", 1)
            button("Continue", "source_continue", 5)
        elif screen == "role":
            text("What best describes you?", 0)
            button("Developer", "role_developer", 1)
            button("Continue", "role_continue", 5)
        elif screen == "plan":
            text("Start your building journey", 0)
            button("Skip", "skip_plan", 5)
        elif screen == "main":
            text("What are we working on today?", 3)
            # Like the real app, the label is plain text inside a clickable box.
            box = _node(root, "", "android.view.ViewGroup", "[900,100][1000,200]",
                        True, **{"fake-action": "open_profile"})
            _node(box, self._avatar(), bounds="[920,120][980,180]")
        elif screen == "profile_menu":
            if self.multi_window:
                # The home screen is still in the dump, behind the sheet.
                text("What are we working on today?", 3)
                home = _node(root, "", "android.view.ViewGroup", "[900,100][1000,200]",
                             True, **{"fake-action": "open_profile", "fake-layer": "home"})
                _node(home, self._avatar(), bounds="[920,120][980,180]")
            self._build_sheet(root)
        elif screen in {"edit_profile_page", "profile_page"}:
            text("Display name Bio Save changes", 0)
        elif screen == "logout_dialog":
            text("Are you sure you want to log out?", 0)
            button("CANCEL", "cancel_logout", 4)
            button(self.confirm_label, "confirm_logout", 5)
        return root

    def _build_sheet(self, root):
        """Render the visible part of the scrolling settings sheet.

        Rows outside the viewport do not exist in the dump (uiautomator omits
        nodes that are not visible); rows cut by the edge keep clipped bounds.
        """

        top, bottom = self.sheet_top, SCREEN_H
        scroller = _node(root, "", "android.widget.ScrollView",
                         f"[0,{top}][{SCREEN_W},{bottom}]", False, scrollable="true")
        for kind, label, y0, height, action in SHEET_ROWS:
            y_top = y0 - self.scroll_offset
            y_bottom = y_top + height
            if y_bottom <= top or y_top >= bottom:
                continue
            clipped = f"[40,{max(y_top, top)}][1040,{min(y_bottom, bottom)}]"
            if kind == "text":
                _node(scroller, label, bounds=clipped)
            elif kind == "button":
                _node(scroller, label, "android.widget.Button", "[40,%d][170,%d]" % (
                    max(y_top, top), min(y_bottom, bottom)), True,
                    **{"fake-action": action, "fake-layer": "sheet"})
            else:
                extra = {"fake-action": action, "fake-layer": "sheet"}
                if label == "Edit Profile":
                    extra["content-desc"] = "Edit Profile"
                row = _node(scroller, "", "android.view.ViewGroup", clipped, True, **extra)
                _node(row, label, bounds=clipped)

    def dump_ui(self):
        if self.screen == "splash":
            if self.splash_ticks > 0:
                self.splash_ticks -= 1
            else:
                self.screen = self.crash_return
        if self.backgrounded:
            root = ET.Element("hierarchy")
            _node(root, "Home screen", bounds=_row(0))
            return root
        if self.screen == "login_processing":
            if self.processing_ticks > 0:
                self.processing_ticks -= 1
            else:
                accepted = (
                    self.fields["password"] == PASSWORD
                    and self.login_attempts > self.reject_logins
                )
                if accepted:
                    self.screen = "main" if self.already_onboarded else "welcome_onboarding"
                else:
                    self.screen = "invalid_credentials"
        return self._build()

    # ---- input -----------------------------------------------------------

    def tap(self, x, y):
        hit = None
        for node in self._build().iter("node"):
            left, top, right, bottom = aa.parse_bounds(node.get("bounds"))
            if left <= x <= right and top <= y <= bottom and node.get("clickable") == "true":
                hit = node
        if self.screen == "notifications" and hit is not None and hit.get("fake-layer") == "home":
            self.taps.append("scrim")  # the sheet is modal: nothing behind it reacts
            return True
        if (
            self.screen == "profile_menu"
            and hit is not None
            and hit.get("fake-layer") == "home"
        ):
            # A tap on the dimmed home screen only closes the sheet.
            self.screen = "main"
            self.taps.append("scrim")
            return True
        if hit is None:
            self.taps.append("miss")
            return True
        if self.background_after_taps is not None and len(self.taps) + 1 >= self.background_after_taps:
            self.background_after_taps = None
            self.backgrounded = True
        if self.sheet_open and hit.get("fake-layer") != "sheet":
            # The scrim takes the tap: it only closes the sheet.
            self.sheet_open = False
            self.taps.append("scrim")
            return True
        if hit.get("fake-field"):
            self.focus = hit.get("fake-field")
            self.taps.append(f"focus:{self.focus}")
            return True
        action = hit.get("fake-action")
        self.taps.append(action)
        self._on_action(action)
        return True

    def type_text(self, text):
        if self.focus:
            self.fields[self.focus] += text
        return True

    def press_key(self, key_code):
        self.keys.append(key_code)
        if key_code == aa.KEYCODE_DEL and self.focus:
            self.fields[self.focus] = self.fields[self.focus][:-1]
        elif key_code == aa.KEYCODE_BACK:
            if self.screen in {"edit_profile_page", "profile_page"}:
                self.screen = "profile_menu"
            elif self.screen == "profile_menu":
                self.screen = "main"
        return True

    def _get_screen_size(self):
        return SCREEN_W, SCREEN_H

    def swipe(self, x1, y1, x2, y2, duration_ms=500):
        self.swipes.append((x1, y1, x2, y2, duration_ms))
        if self.screen != "profile_menu":
            return True
        if self.stuck_profile:
            # Replit's stuck profile page: only dragging it back down frees it.
            if y2 > y1:
                self.stuck_profile = False
                self.screen = "initial_login"
            return True
        if y1 < self.sheet_top:
            self.screen = "main"  # a drag that starts on the scrim dismisses the sheet
            return True
        if self.ignore_slow_swipes and duration_ms >= 600:
            return True
        moved = (y1 - y2) * self.scroll_gain
        self.scroll_offset = int(min(max(self.scroll_offset + moved, 0), SHEET_MAX_SCROLL))
        return True

    def _on_action(self, action):
        screen = self.screen
        if screen == "initial_login" and action == "start_login":
            self.screen = "email_choice"
        elif screen.startswith("email_choice") and action == "choose_email":
            self.screen = "email_login"
            if self.email_sheet:
                self.sheet_open = True
                self.focus = "email" if self.email_sheet == "keyboard_visible" else None
        elif screen in {"email_login", "email_choice_keyboard"} and action == "submit_email":
            if self.fields["email"] == self.expected_email:
                self.screen = "password_login"
        elif screen in {"password_login", "invalid_credentials"} and action == "submit_login":
            self.login_attempts += 1
            self.processing_ticks = 1
            self.screen = "login_processing"
        elif screen == "welcome_onboarding" and action == "welcome_continue":
            self.screen = "name"
            self._prefill()
        elif screen == "name" and action == "name_continue":
            if self.fields["first_name"] == "Mipok" or self.fields["first_name"]:
                self.screen = "username"
        elif screen == "username" and action == "username_continue":
            self.screen = "source"
        elif screen == "source" and action == "source_google":
            self.source_selected = True
        elif screen == "source" and action == "source_continue":
            if self.source_selected:
                self.screen = "role"
        elif screen == "role" and action == "role_developer":
            self.role_selected = True
        elif screen == "role" and action == "role_continue":
            if self.role_selected:
                self.screen = "plan"
        elif screen == "plan" and action == "skip_plan":
            self.screen = "notifications" if self.notifications_prompt else "main"
        elif screen == "notifications" and action in {"notifications_not_now", "notifications_on"}:
            self.screen = "main"
        elif screen == "main" and action == "open_profile":
            self.screen = "edit_profile_page" if self.wrong_first_open else "profile_menu"
            self.wrong_first_open = False
            self.scroll_offset = 0
        elif screen == "profile_menu" and action == "logout_menu":
            self.screen = "logout_dialog"
        elif screen == "profile_menu" and action in {"sheet_avatar", "edit_profile"}:
            # The tempting taps: they open a different screen, not the log-out row.
            self.screen = "edit_profile_page"
        elif screen == "logout_dialog" and action == "confirm_logout":
            if self.ignore_confirm > 0:
                self.ignore_confirm -= 1
            else:
                self._finish_logout()

    def _finish_logout(self):
        mode, self.after_logout = self.after_logout, "normal"
        if mode in {"crash", "crash_logged_in"}:
            self.crashed = True
            self.crash_return = "main" if mode == "crash_logged_in" else "initial_login"
            self.app_running = False
            self.screen = "launcher"
        elif mode == "stuck_profile":
            self.stuck_profile = True
            self.screen = "profile_menu"
        elif mode == "never":
            self.screen = "mystery"
        else:
            self.screen = "initial_login"  # logged out; the app is still running

    # ---- device state ----------------------------------------------------

    def verify_device(self):
        return True

    def open_app(self, package_name):
        self.opened += 1
        self.backgrounded = False
        if self.crashed and not self.relaunch_works:
            return True  # the app never comes back
        if not self.app_running:
            self.app_running = True
            if self.crashed:
                self.crashed = False
                self.splash_ticks = self.relaunch_ticks
                self.screen = "splash"
            else:
                self.screen = "initial_login"
        return True

    def close_app(self, package_name):
        self.app_running = False
        self.backgrounded = False
        self.screen = "launcher"
        return True

    def foreground_package(self):
        if self.backgrounded or not self.app_running:
            return "com.android.launcher3"
        return "com.replit.app"

    def package_running(self, package_name):
        return self.app_running

    def save_failure_evidence(self, tag="error"):
        self.last_failure_reason = tag
        self.evidence.append(tag)


class IsolatedCwdTestCase(unittest.TestCase):
    """Keep ui.xml, logs, and screenshots out of the project directory."""

    def setUp(self):
        self._previous_cwd = os.getcwd()
        self._tempdir = tempfile.TemporaryDirectory()
        os.chdir(self._tempdir.name)
        self._stdout = contextlib.redirect_stdout(io.StringIO())
        self._stdout.__enter__()

    def tearDown(self):
        self._stdout.__exit__(None, None, None)
        os.chdir(self._previous_cwd)
        self._tempdir.cleanup()


def run_flow(device):
    with patch.object(aa, "ONBOARDING_SETTLE_SECONDS", 0), \
            patch("android_automation.time.sleep"):
        return device.complete_onboarding(EMAIL, PASSWORD, USERNAME)


EXPECTED_FULL_FLOW = [
    "start_login", "choose_email", "submit_email", "submit_login",
    "welcome_continue", "name_continue", "username_continue",
    "source_google", "source_continue", "role_developer", "role_continue",
    "skip_plan", "open_profile", "logout_menu", "confirm_logout",
]


def actions(device):
    return [tap for tap in device.taps if not tap.startswith("focus:")]


class OnboardingFlowTests(IsolatedCwdTestCase):
    def test_full_flow_completes_and_closes_the_app(self):
        device = FakeReplitDevice()
        self.assertTrue(run_flow(device))
        self.assertEqual(actions(device), EXPECTED_FULL_FLOW)
        self.assertEqual(device.fields["email"], EMAIL)
        self.assertEqual(device.fields["password"], PASSWORD)
        self.assertEqual(device.fields["first_name"], "Mipok")
        self.assertFalse(device.app_running)
        self.assertEqual(device.evidence, [])

    def test_profile_is_opened_for_any_account_name(self):
        # The previous code only tapped a node whose text was the literal "MI".
        device = FakeReplitDevice(display_name="Xqzv", expected_email="xqzv8412@example.test")
        with patch.object(aa, "ONBOARDING_SETTLE_SECONDS", 0), \
                patch("android_automation.time.sleep"):
            self.assertTrue(device.complete_onboarding(
                "xqzv8412@example.test", PASSWORD, "xqzv8412"))
        self.assertEqual(device.fields["first_name"], "Xqzv")
        self.assertEqual(device._avatar(), "XQ")
        self.assertIn("open_profile", device.taps)

    def test_resume_from_an_already_onboarded_main_screen(self):
        device = FakeReplitDevice(start_screen="main")
        self.assertTrue(run_flow(device))
        self.assertEqual(actions(device), ["open_profile", "logout_menu", "confirm_logout"])
        self.assertNotIn("focus:email", device.taps)

    def test_resume_from_each_later_screen(self):
        for start in ("profile_menu", "logout_dialog", "plan", "role", "source", "username"):
            with self.subTest(start=start):
                device = FakeReplitDevice(start_screen=start)
                self.assertTrue(run_flow(device))
                self.assertEqual(device.evidence, [])
                self.assertFalse(device.app_running)

    def test_already_onboarded_account_goes_straight_to_logout_after_login(self):
        device = FakeReplitDevice(already_onboarded=True)
        self.assertTrue(run_flow(device))
        self.assertNotIn("welcome_continue", device.taps)
        self.assertIn("confirm_logout", device.taps)

    def test_text_is_replaced_not_appended_on_a_retried_screen(self):
        device = FakeReplitDevice(start_screen="email_login", fields={"email": EMAIL})
        self.assertTrue(run_flow(device))
        self.assertEqual(device.fields["email"], EMAIL)

    def test_invalid_credentials_get_exactly_one_retry(self):
        device = FakeReplitDevice(reject_logins=1)
        self.assertTrue(run_flow(device))
        self.assertEqual(device.taps.count("submit_login"), 2)

        device = FakeReplitDevice(reject_logins=2)
        self.assertFalse(run_flow(device))
        self.assertEqual(device.evidence, ["onboarding_stuck_retry_login"])
        self.assertTrue(device.app_running)  # a failed run must not close the app

    def test_keyboard_visible_variant_types_the_email_directly(self):
        device = FakeReplitDevice(start_screen="email_choice_keyboard")
        self.assertTrue(run_flow(device))
        self.assertNotIn("choose_email", device.taps)
        self.assertEqual(device.fields["email"], EMAIL)

    def test_a_login_sheet_left_over_the_email_form_does_not_lose_the_typed_email(self):
        # attached_assets/6-8 and the flow notes ("tap once outside the login
        # area"): the sheet stays above the form and the first tap only closes it.
        for variant in ("keyboard_visible", "keyboard_hidden"):
            with self.subTest(variant=variant):
                device = FakeReplitDevice(start_screen="email_choice", email_sheet=variant)
                self.assertTrue(run_flow(device))
                self.assertEqual(device.fields["email"], EMAIL)
                self.assertEqual(device.taps.count("scrim"), 1)
                self.assertEqual(device.evidence, [])

    def test_typed_text_that_never_arrived_is_typed_again(self):
        device = FakeReplitDevice(start_screen="email_choice", email_sheet="keyboard_hidden")
        self.assertTrue(run_flow(device))
        # The first attempt hit the sheet and typed into nothing; the second landed.
        self.assertEqual(device.taps.count("focus:email"), 1)
        self.assertEqual(device.fields["email"], EMAIL)

    def test_text_the_dump_cannot_show_is_accepted_and_never_typed_twice(self):
        # A field whose content the UI dump hides must not fail a flow that works,
        # and the retry must clear what the first attempt typed.
        device = FakeReplitDevice(start_screen="email_login", unreadable_fields=True)
        self.assertTrue(run_flow(device))
        self.assertEqual(device.fields["email"], EMAIL)
        self.assertEqual(device.fields["password"], PASSWORD)  # masked: never read back
        self.assertEqual(device.fields["first_name"], "Mipok")
        self.assertEqual(device.taps.count("focus:password"), 1)
        self.assertEqual(device.evidence, [])

    def test_a_field_that_stays_empty_fails_instead_of_looping(self):
        device = FakeReplitDevice(start_screen="email_login")
        device.type_text = lambda text: True  # ADB "succeeds" but nothing is typed
        self.assertFalse(run_flow(device))
        self.assertEqual(device.fields["email"], "")
        self.assertEqual(len(device.evidence), 1)
        self.assertTrue(device.evidence[0].startswith("onboarding_stuck_"), device.evidence)

    def test_logout_confirmation_label_case_does_not_matter(self):
        for label in ("LOG OUT", "Log out", "Log Out", "Logout"):
            with self.subTest(label=label):
                device = FakeReplitDevice(start_screen="logout_dialog", confirm_label=label)
                self.assertTrue(run_flow(device))

    def test_app_is_closed_only_after_the_logout_dialog_is_gone(self):
        device = FakeReplitDevice(start_screen="logout_dialog", ignore_confirm=1)
        self.assertTrue(run_flow(device))
        self.assertEqual(device.taps.count("confirm_logout"), 2)

    def test_security_challenge_is_terminal_and_never_tapped(self):
        device = FakeReplitDevice(start_screen="security")
        self.assertFalse(run_flow(device))
        self.assertEqual(device.taps, [])
        self.assertEqual(device.evidence, ["onboarding_security_challenge"])

    def test_unrecognised_screen_fails_after_bounded_cycles(self):
        device = FakeReplitDevice(start_screen="mystery")
        with patch.object(aa, "ONBOARDING_MAX_CYCLES", 5):
            self.assertFalse(run_flow(device))
        self.assertEqual(device.evidence, ["onboarding_stuck_onboarding_timeout"])
        self.assertEqual(device.taps, [])

    def test_screens_the_flow_never_expects_fail_instead_of_looping(self):
        device = FakeReplitDevice(start_screen="logged_out_page")
        with patch.object(aa, "ONBOARDING_UNEXPECTED_LIMIT", 3):
            self.assertFalse(run_flow(device))
        self.assertEqual(device.evidence, ["onboarding_stuck_unexpected_logged_out"])

    def test_missing_logout_row_fails_after_bounded_scrolls(self):
        device = FakeReplitDevice(start_screen="profile_menu")
        device.swipe = lambda *args, **kwargs: True  # the list never moves
        self.assertFalse(run_flow(device))
        self.assertEqual(device.evidence, ["onboarding_stuck_scroll_profile"])


class ProfileSheetTests(IsolatedCwdTestCase):
    """The settings sheet: scroll to "Log Out" and tap only that (user report)."""

    LOGOUT_ONLY = ["open_profile", "logout_menu", "confirm_logout"]
    WRONG_TAPS = {"sheet_avatar", "edit_profile", "core", "usage", "theme", "scrim"}

    def test_logout_is_reached_by_scrolling_and_nothing_else_is_tapped(self):
        for multi_window in (True, False):
            with self.subTest(multi_window=multi_window):
                device = FakeReplitDevice(start_screen="main", multi_window=multi_window)
                self.assertTrue(run_flow(device))
                self.assertEqual(actions(device), self.LOGOUT_ONLY)
                self.assertFalse(self.WRONG_TAPS & set(device.taps), device.taps)
                self.assertGreaterEqual(len(device.swipes), 1)
                self.assertEqual(device.evidence, [])
                self.assertFalse(device.app_running)

    def test_a_sheet_that_is_already_open_is_scrolled_not_tapped_again(self):
        # With the home screen still in the dump the old classifier called this
        # screen "main" and tapped the avatar / "Edit Profile" again.
        device = FakeReplitDevice(start_screen="profile_menu", multi_window=True)
        self.assertTrue(run_flow(device))
        self.assertEqual(actions(device), ["logout_menu", "confirm_logout"])
        self.assertFalse(self.WRONG_TAPS & set(device.taps), device.taps)

    def test_swipes_are_drawn_inside_the_sheet(self):
        device = FakeReplitDevice(start_screen="profile_menu", sheet_top=1200)
        self.assertTrue(run_flow(device))
        self.assertTrue(device.swipes)
        for _x1, y1, _x2, y2, _duration in device.swipes:
            self.assertTrue(1200 <= y1 <= SCREEN_H and 1200 <= y2 <= SCREEN_H, device.swipes)
        self.assertEqual(device.screen, "launcher")  # the sheet was never dismissed

    def test_a_swipe_that_changes_nothing_is_retried_with_another_gesture(self):
        device = FakeReplitDevice(start_screen="profile_menu", ignore_slow_swipes=True)
        self.assertTrue(run_flow(device))
        durations = [swipe[4] for swipe in device.swipes]
        self.assertGreaterEqual(durations[0], 600)       # first try does nothing
        self.assertTrue(any(value < 600 for value in durations), durations)
        self.assertEqual(device.evidence, [])

    def test_a_wrong_screen_after_the_avatar_tap_is_left_with_back(self):
        device = FakeReplitDevice(start_screen="main")
        device.wrong_first_open = True
        self.assertTrue(run_flow(device))
        self.assertEqual(device.keys.count(aa.KEYCODE_BACK), 1)
        self.assertEqual(actions(device)[-2:], ["logout_menu", "confirm_logout"])

    def test_back_is_pressed_a_bounded_number_of_times(self):
        device = FakeReplitDevice(start_screen="edit_profile_page")
        device.press_key = lambda key: True  # BACK does nothing on this screen
        with patch.object(aa, "ONBOARDING_MAX_CYCLES", 60):
            self.assertFalse(run_flow(device))
        self.assertEqual(device.taps, [])

    def test_the_app_is_opened_again_when_it_left_the_foreground(self):
        device = FakeReplitDevice(start_screen="role", background_after_taps=1)
        self.assertTrue(run_flow(device))
        self.assertEqual(device.opened, 2)  # once at the start, once to recover
        self.assertEqual(device.evidence, [])

    def test_the_app_is_not_relaunched_after_a_logout_was_confirmed(self):
        device = FakeReplitDevice(start_screen="logout_dialog")
        self.assertTrue(run_flow(device))
        self.assertEqual(device.opened, 1)

    def test_evidence_includes_the_ui_tree(self):
        class Recorder(AndroidAutomation):
            def run_adb(self, command, wait=0.0):
                self.last_adb_ok = True
                return ""

            def dump_ui(self):
                with open("ui.xml", "w", encoding="utf-8") as handle:
                    handle.write("<hierarchy><node text='Usage'/></hierarchy>")
                return ET.Element("hierarchy")

        Recorder(device_id="FAKE").save_failure_evidence("scroll_profile")
        saved = [name for name in os.listdir("screenshots") if name.endswith(".xml")]
        self.assertEqual(len(saved), 1, os.listdir("screenshots"))
        self.assertIn("scroll_profile", saved[0])
        with open(os.path.join("screenshots", saved[0]), encoding="utf-8") as handle:
            self.assertIn("Usage", handle.read())

    def test_observe_screen_reports_what_the_phone_shows(self):
        device = FakeReplitDevice(start_screen="profile_menu")
        device.run_adb = lambda command, wait=0.0: "List of devices attached\nFAKE\tdevice\n"
        device.last_adb_ok = True
        self.assertEqual(device.observe_screen(), "profile_menu")
        device.backgrounded = True
        self.assertEqual(device.observe_screen(), "not_replit")
        device.run_adb = lambda command, wait=0.0: "List of devices attached\n"
        self.assertEqual(device.observe_screen(), "no_device")


class HelperTests(IsolatedCwdTestCase):
    def test_label_matching_ignores_case_and_spacing_but_never_matches_empty(self):
        self.assertTrue(label_matches("LOG OUT", "Log Out"))
        self.assertTrue(label_matches("  Log   out ", "log out"))
        self.assertFalse(label_matches("Continue with Email", "Continue"))
        self.assertTrue(label_matches("Continue with Email", "continue", partial=True))
        self.assertFalse(label_matches("", ""))
        self.assertFalse(label_matches("anything", ""))

    def test_find_element_is_case_insensitive(self):
        root = ET.Element("hierarchy")
        _node(root, "LOG OUT", bounds="[0,0][10,10]")
        node = AndroidAutomation().find_element(text="Log out", root=root)
        self.assertIsNotNone(node)

    def test_actionable_target_prefers_the_node_then_the_clickable_ancestor(self):
        root = ET.Element("hierarchy")
        row = _node(root, "", "android.view.ViewGroup", "[0,0][200,100]", True)
        label = _node(row, "Log Out", bounds="[10,10][100,90]")
        button = _node(root, "Go", "android.widget.Button", "[0,200][200,300]", True)
        orphan = _node(root, "Plain", bounds="[0,400][200,500]")
        self.assertIs(actionable_target(root, button), button)
        self.assertIs(actionable_target(root, label), row)
        self.assertIsNone(actionable_target(root, orphan))
        self.assertIsNone(actionable_target(root, None))

    def test_ancestor_must_contain_the_label_to_be_a_target(self):
        root = ET.Element("hierarchy")
        row = _node(root, "", "android.view.ViewGroup", "[0,0][50,50]", True)
        label = _node(row, "Far", bounds="[300,300][400,400]")
        self.assertIsNone(actionable_target(root, label))

    def test_name_and_avatar_derivation(self):
        self.assertEqual(first_name_from_username("mipok93125"), "Mipok")
        self.assertEqual(first_name_from_username("123"), "123")
        self.assertEqual(first_name_from_username(None), "")
        self.assertEqual(avatar_initials("Mipok"), "MI")
        self.assertEqual(avatar_initials("X"), "X")
        self.assertEqual(avatar_initials(""), "")


def adb_responses(table):
    def run(args, **kwargs):
        joined = " ".join(args)
        for needle, (code, out) in table.items():
            if needle in joined:
                return subprocess.CompletedProcess(args, code, stdout=out, stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    return run


class ProcessStateTests(IsolatedCwdTestCase):
    """Regression tests for pidof exit-code handling (real subprocess path)."""

    def automation(self):
        return AndroidAutomation(device_id="FAKE")

    def test_pidof_exit_1_with_no_output_means_not_running(self):
        table = {"pidof": (1, "")}
        with patch("android_automation.subprocess.run", side_effect=adb_responses(table)):
            self.assertIs(self.automation().package_running("com.replit.app"), False)

    def test_pidof_with_a_pid_means_running(self):
        table = {"pidof": (0, "4321")}
        with patch("android_automation.subprocess.run", side_effect=adb_responses(table)):
            self.assertIs(self.automation().package_running("com.replit.app"), True)

    def test_missing_pidof_falls_back_to_the_process_list(self):
        listing = "u0_a77  4321  900  0 0 0 0 S com.replit.app\nu0_a1 1 0 0 0 0 0 S init\n"
        with patch("android_automation.subprocess.run",
                   side_effect=adb_responses({"pidof": (127, ""), "ps -A": (0, listing)})):
            self.assertIs(self.automation().package_running("com.replit.app"), True)
        with patch("android_automation.subprocess.run",
                   side_effect=adb_responses({"pidof": (127, ""), "ps -A": (0, "init\n")})):
            self.assertIs(self.automation().package_running("com.replit.app"), False)

    def test_adb_failures_stay_unknown(self):
        with patch("android_automation.subprocess.run", side_effect=OSError("no adb")):
            self.assertIsNone(self.automation().package_running("com.replit.app"))
        with patch("android_automation.subprocess.run",
                   side_effect=adb_responses({"pidof": (255, ""), "ps -A": (255, "")})):
            self.assertIsNone(self.automation().package_running("com.replit.app"))

    def test_force_stop_is_verified_when_pidof_reports_not_running(self):
        table = {
            "force-stop": (0, ""),
            "dumpsys window": (0, "  mCurrentFocus=Window{1a2b u0 com.android.launcher3/.Launcher}"),
            "pidof": (1, ""),
        }
        with patch.object(aa, "ELEMENT_WAIT_TIMEOUT", 2), \
                patch("android_automation.subprocess.run", side_effect=adb_responses(table)):
            self.assertTrue(self.automation().force_stop_and_verify("com.replit.app"))

    def test_force_stop_fails_while_the_process_is_still_alive(self):
        table = {
            "force-stop": (0, ""),
            "dumpsys window": (0, "  mCurrentFocus=Window{1a2b u0 com.android.launcher3/.Launcher}"),
            "pidof": (0, "4321"),
        }
        with patch.object(aa, "ELEMENT_WAIT_TIMEOUT", 1), \
                patch("android_automation.subprocess.run", side_effect=adb_responses(table)):
            self.assertFalse(self.automation().force_stop_and_verify("com.replit.app"))


if __name__ == "__main__":
    unittest.main()
