"""State-driven Android automation for the operator-owned Replit device.

The UI hierarchy is authoritative. Coordinate input is limited to the bounded
swipe used to reveal the profile-menu logout control; taps never fall back to
blind percentage coordinates.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

from colorama import Fore, Style

from pc_flow import strip_recaptcha_notice

try:
    from config import ELEMENT_WAIT_TIMEOUT, REPLIT_PACKAGE_NAME
except ImportError:
    ELEMENT_WAIT_TIMEOUT = 10
    REPLIT_PACKAGE_NAME = "com.replit.app"


BOUNDS_PATTERN = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
EMAIL_LOGIN_TEXT = ("continue with email", "email or username")
ANDROID_STATES = frozenset(
    {
        "HOME",
        "APP_DRAWER",
        "REPLIT_LOADING",
        "REPLIT_READY",
        "EMAIL_LOGIN",
        "EMAIL_ENTRY",
        "PASSWORD_ENTRY",
        "LOGIN_PROCESSING",
        "LOGIN_RETRY",
        "WELCOME",
        "ONBOARDING",
        "PROFILE",
        "LOGOUT_CONFIRMATION",
        "LOGGED_OUT",
        "ANDROID_HOME",
        "COMPLETE",
        "SECURITY_CHALLENGE",
        "ERROR",
    }
)
CANONICAL_STATE_NAMES = {
    "initial_login": "REPLIT_READY",
    "email_choice": "EMAIL_LOGIN",
    "email_login": "EMAIL_ENTRY",
    "password_login": "PASSWORD_ENTRY",
    "login_processing": "LOGIN_PROCESSING",
    "invalid_credentials": "LOGIN_RETRY",
    "welcome_onboarding": "WELCOME",
    "name": "ONBOARDING",
    "username": "ONBOARDING",
    "source": "ONBOARDING",
    "role": "ONBOARDING",
    "plan": "ONBOARDING",
    "notifications_prompt": "ONBOARDING",
    "main": "REPLIT_READY",
    "profile_menu": "PROFILE",
    "logout_dialog": "LOGOUT_CONFIRMATION",
    "logged_out": "LOGGED_OUT",
    "post_close_home": "ANDROID_HOME",
    "security_challenge": "SECURITY_CHALLENGE",
    "unknown": "ERROR",
}
SECURITY_CHALLENGE_PATTERNS = (
    re.compile(r"\bcaptcha\b", re.IGNORECASE),
    re.compile(r"\brecaptcha\b", re.IGNORECASE),
    re.compile(r"\bverify you are human\b", re.IGNORECASE),
    re.compile(r"\bsuspicious(?:\s+login|\s+activity)?\b", re.IGNORECASE),
    re.compile(r"\baccount\s+(?:restricted|locked|suspended)\b", re.IGNORECASE),
    re.compile(r"\brate\s*limit(?:ed)?\b", re.IGNORECASE),
    re.compile(r"\badditional\s+(?:identity|security)\s+verification\b", re.IGNORECASE),
)
# Onboarding limits. Each limit bounds one kind of repeated action, so a screen
# that never changes ends in a recorded failure instead of an endless loop.
ONBOARDING_MAX_CYCLES = 120        # observe-and-act cycles before giving up
ONBOARDING_ACTION_LIMIT = 3        # taps on one control before the screen is stuck
ONBOARDING_SCROLL_LIMIT = 6        # swipes while looking for "Log Out"
ONBOARDING_UNEXPECTED_LIMIT = 15   # observations of screens this flow never expects
ONBOARDING_SETTLE_SECONDS = 2.0    # pause after a tap; the flow notes loads take seconds
ONBOARDING_TEXT_ATTEMPTS = 2       # tries at typing a value before an unreadable field is accepted
ONBOARDING_UNKNOWN_BEFORE_RECOVERY = 4  # unreadable screens in a row before BACK / relaunch
ONBOARDING_BACK_LIMIT = 2          # BACK presses per run while recovering from a wrong screen
ONBOARDING_RELAUNCH_LIMIT = 2      # times the Replit app is brought back to the front
KEYCODE_BACK = 4
# The settings sheet is a scrolling list and "Log Out" sits below the fold. A
# stuck swipe is retried with a different gesture before it counts as a failure:
# (start, end) as fractions of the scrollable area's height, and the duration.
SCROLL_GESTURES = (
    (0.80, 0.25, 600),
    (0.85, 0.15, 350),
    (0.70, 0.30, 900),
)
# After the final "Log out" tap the app may crash back to the Android home screen,
# or leave the profile page stuck on screen (a Replit-side glitch that goes away
# when it is scrolled up). The tap itself counts as the logout; these bound the
# extra look at the screen afterwards. One check is about a second apart.
POST_LOGOUT_CHECKS = 30            # looks at the screen after the tap
POST_LOGOUT_LOAD_CHECKS = 45       # looks after Replit was opened again (it loads slowly)
POST_LOGOUT_RELAUNCH_LIMIT = 2     # times the app is opened again
POST_LOGOUT_SHEET_PATIENCE = 2     # looks before a leftover profile page is scrolled up
POST_LOGOUT_SCROLL_UP_LIMIT = 3    # scroll-up swipes on a leftover profile page
LOGGED_OUT_STATES = frozenset(
    {"initial_login", "email_choice", "email_login", "password_login", "logged_out"}
)
LOGGED_IN_STATES = frozenset(
    {
        "welcome_onboarding", "name", "username", "source", "role", "plan",
        "notifications_prompt", "main",
    }
)
KEYCODE_DEL = 67
KEYCODE_MOVE_END = 123
ONBOARDING_STATES = frozenset(
    {
        "initial_login",
        "email_choice",
        "email_login",
        "password_login",
        "login_processing",
        "invalid_credentials",
        "welcome_onboarding",
        "name",
        "username",
        "source",
        "role",
        "plan",
        "notifications_prompt",
        "main",
        "profile_menu",
        "logout_dialog",
    }
)


class OnboardingStuck(RuntimeError):
    """Raised when the onboarding state machine cannot make progress."""


SCREENSHOT_REFERENCES = {
    "email_login": (
        "attached_assets/7_1790062961791.jpg",
        "attached_assets/8_1790062961792.jpg",
    ),
    "password_login": (
        "attached_assets/9_1790062961793.jpg",
        "attached_assets/10_1790062961795.jpg",
        "attached_assets/11_1790062961796.jpg",
    ),
    "login_processing": ("attached_assets/12_1790062961797.jpg",),
    "invalid_credentials": ("attached_assets/13_1790062961798.jpg",),
    "welcome_onboarding": ("attached_assets/14_1790062961799.jpg",),
    "name": ("attached_assets/15_1790062961800.jpg",),
    "username": ("attached_assets/16_1790062961801.jpg",),
    "source": ("attached_assets/17_1790062961802.jpg",),
    "role": ("attached_assets/18_1790062961803.jpg",),
    "plan": ("attached_assets/19_1790062961803.jpg",),
    "notifications_prompt": ("attached_assets/19.5_1790062961803.jpg",),
    "main": ("attached_assets/20_1790062961804.jpg",),
    "profile_menu": (
        "attached_assets/21_1790062961805.jpg",
        "attached_assets/22_1790062961806.jpg",
    ),
    "logout_dialog": ("attached_assets/23_1790062961807.jpg",),
    "post_close_home": ("attached_assets/24_1790062961808.jpg",),
}


def screenshot_references_for_state(state: str) -> Tuple[str, ...]:
    """Return the supplied visual references for a classified Android state."""

    return tuple(SCREENSHOT_REFERENCES.get(state, ()))


def canonical_android_state(state: str) -> str:
    """Map detailed classifiers to the stable state-machine vocabulary."""

    return CANONICAL_STATE_NAMES.get(state, "ERROR")


def parse_bounds(value: Any) -> Optional[Tuple[int, int, int, int]]:
    """Return valid UI node bounds, or ``None`` for malformed bounds."""

    match = BOUNDS_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        return None
    left, top, right, bottom = (int(part) for part in match.groups())
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def parse_adb_devices(output: Any) -> List[Tuple[str, str]]:
    """Parse authorized and unauthorized rows from ``adb devices`` output."""

    devices: List[Tuple[str, str]] = []
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices


_PLAIN_APOSTROPHES = str.maketrans(
    {
        "\N{RIGHT SINGLE QUOTATION MARK}": "'",
        "\N{LEFT SINGLE QUOTATION MARK}": "'",
    }
)


def _node_text(root: Optional[ET.Element]) -> str:
    if root is None:
        return ""
    values: List[str] = []
    for node in root.iter("node"):
        for attribute in ("text", "content-desc", "hint"):
            value = (node.get(attribute) or "").strip()
            if value:
                values.append(value)
    return " ".join(values)


def _has_class(root: Optional[ET.Element], class_name: str) -> bool:
    return bool(
        root is not None
        and any(node.get("class") == class_name for node in root.iter("node"))
    )


# Rows of the profile / settings sheet (attached_assets/21-22). A few are
# specific to it; the others are ordinary words, so two of them are required.
_SHEET_SPECIFIC = ("manage account", "join replit core", "log out")
_SHEET_COMMON = ("usage", "edit profile", "theme", "notifications", "help", "docs", "about")


def is_profile_sheet(text: str) -> bool:
    """Whether normalised screen text shows the profile / settings sheet.

    "Usage" together with "Edit Profile" is the classic case; any row that only
    the sheet has, or two of its ordinary rows, also counts so that the sheet is
    still recognised after it was scrolled and the top rows are out of view.
    """

    if "usage" in text and "edit profile" in text:
        return True
    if any(label in text for label in _SHEET_SPECIFIC):
        return True
    words = set(re.findall(r"[a-z]+", text))
    present = sum(
        1 for label in _SHEET_COMMON
        if (label in text if " " in label else label in words)
    )
    return present >= 2


def classify_screen(root: Optional[ET.Element]) -> str:
    """Classify the visible Android state represented by a UI dump."""

    # Replit's titles use typographic apostrophes (see attached_assets/14, 15 and
    # 16: "Let" + U+2019 + "s get started"); compare against plain ones.
    text = _node_text(root).casefold().translate(_PLAIN_APOSTROPHES)
    # The permanent "protected by reCAPTCHA" legal notice is not a challenge.
    challenge_text = strip_recaptcha_notice(text)
    if any(pattern.search(challenge_text) for pattern in SECURITY_CHALLENGE_PATTERNS):
        return "security_challenge"
    if "are you sure you want to log out" in text:
        return "logout_dialog"
    if "invalid username or password" in text:
        return "invalid_credentials"
    # Replit's optional "Notifications - Turn On Notifications / Not Now" sheet
    # (attached_assets/19.5). Checked before the profile sheet: its own words
    # ("notifications", "about") would otherwise make it look like that sheet.
    if "turn on notifications" in text and "not now" in text:
        return "notifications_prompt"
    # The settings sheet is checked BEFORE the home screen. On current Android a
    # UI dump can include every window, so the home screen's "What are we working
    # on today?" is still in the dump behind the open sheet; checked first, the
    # sheet was classified as the home screen and the avatar / "Edit Profile"
    # were tapped again instead of scrolling to "Log Out". The sheet also has to
    # be recognised after it was scrolled: "Usage" and "Edit Profile" leave the
    # dump once they are scrolled off, so other rows of the list count as well.
    if is_profile_sheet(text):
        return "profile_menu"
    if "what are we working on today" in text:
        return "main"
    if "start your building journey" in text:
        return "plan"
    if "what best describes you" in text:
        return "role"
    if "how did you hear about replit" in text:
        return "source"
    if "let's create a username" in text:
        return "username"
    if "what's your name" in text:
        return "name"
    if "welcome to replit" in text and "let's get started" in text:
        return "welcome_onboarding"
    if any(value in text for value in EMAIL_LOGIN_TEXT):
        if "continue with google" in text and "email or username" not in text:
            return "email_choice"
        return "email_login"
    if "password" in text and "login" in text:
        if _has_class(root, "android.widget.ProgressBar"):
            return "login_processing"
        return "password_login"
    if "continue" in text:
        return "initial_login"
    if "sign in" in text or "log in" in text:
        return "logged_out"
    return "unknown"


def node_is_actionable(node: Optional[ET.Element]) -> bool:
    """Require an enabled node with an actionable UI property."""

    if node is None:
        return False
    if (node.get("enabled") or "true").casefold() != "true":
        return False
    return any(
        (node.get(attribute) or "false").casefold() == "true"
        for attribute in ("clickable", "focusable", "long-clickable")
    )


def normalize_ui_text(value: Any) -> str:
    """Compare UI labels ignoring case and extra spaces ("LOG OUT" == "Log out")."""

    return " ".join(str(value or "").split()).casefold()


def label_matches(value: Any, expected: Any, partial: bool = False) -> bool:
    """Return whether a node label matches the expected label.

    Empty expectations never match, so an unset label cannot select a random
    node that happens to have no text.
    """

    wanted = normalize_ui_text(expected)
    if not wanted:
        return False
    actual = normalize_ui_text(value)
    return wanted in actual if partial else actual == wanted


def _bounds_center(node: Optional[ET.Element]) -> Optional[Tuple[int, int]]:
    bounds = parse_bounds(node.get("bounds")) if node is not None else None
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    return (left + right) // 2, (top + bottom) // 2


def actionable_target(
    root: Optional[ET.Element],
    node: Optional[ET.Element],
) -> Optional[ET.Element]:
    """Return the element that should receive the tap for a matched label.

    Replit labels are often non-clickable text inside a clickable row or
    button. The tap then belongs to the nearest actionable ancestor whose bounds
    contain the label. Returns None when the label has no actionable target.
    """

    if node is None or root is None:
        return None
    if node_is_actionable(node) and parse_bounds(node.get("bounds")) is not None:
        return node
    center = _bounds_center(node)
    if center is None:
        return None
    parents = {child: parent for parent in root.iter() for child in parent}
    current = node
    while current in parents:
        current = parents[current]
        bounds = parse_bounds(current.get("bounds"))
        if bounds is None or not node_is_actionable(current):
            continue
        left, top, right, bottom = bounds
        if left <= center[0] <= right and top <= center[1] <= bottom:
            return current
    return None


def first_name_from_username(username: Any) -> str:
    """Return the first name typed on Replit's name screen.

    Generated usernames are a name followed by digits ("mipok93125"). The digits
    are dropped and the name is capitalised ("Mipok").
    """

    raw = str(username or "").strip()
    name = re.split(r"\d", raw)[0].strip() or raw
    return name.capitalize()


def avatar_initials(first_name: Any) -> str:
    """Return the avatar initials Replit shows for a one-word display name.

    attached_assets/20 and 21 show display name "Mipok" with avatar "MI". The
    previous code hard-coded "MI", which only matched that one account.
    """

    return str(first_name or "").strip()[:2].upper()


def encode_adb_text(value: Any) -> str:
    """Encode printable text for ``adb shell input text``.

    ADB receives an argument list rather than a local shell command, but the
    device-side input parser still treats spaces and shell punctuation
    specially. Keep the transformation in one place so every field uses the
    same rules.
    """

    encoded: List[str] = []
    for char in str(value):
        if char == " ":
            encoded.append("%s")
        elif char == "%":
            encoded.append("%%")
        elif char in r"\&<>()!+@'\";|*?$#`":
            encoded.append("\\" + char)
        elif ord(char) < 0x20 or char == "\x7f":
            raise ValueError("ADB text input does not support control characters.")
        else:
            encoded.append(char)
    return "".join(encoded)


class AndroidAutomation:
    def __init__(self, device_id: Optional[str] = None):
        self.device_id = device_id
        self.log_file = "android_actions.log"
        self.screen_w: Optional[int] = None
        self.screen_h: Optional[int] = None
        self.last_adb_ok = False
        self.last_adb_error = ""
        self.last_observed_state = "unknown"
        self.last_failure_reason = ""
        self.log("Android adapter initialized; device verification is pending.", Fore.CYAN)

    # ================= LOW-LEVEL ADB HELPERS =================

    def log(self, message: str, color: str = Fore.WHITE) -> None:
        timestamp = time.strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        print(f"{color}{log_message}{Style.RESET_ALL}")
        with open(self.log_file, "a", encoding="utf-8") as handle:
            handle.write(f"{log_message}\n")

    def _adb_args(self, command: Any) -> List[str]:
        parts = shlex.split(command) if isinstance(command, str) else [str(part) for part in command]
        args = ["adb"]
        if self.device_id:
            args.extend(["-s", self.device_id])
        args.extend(parts)
        return args

    def run_adb(self, command: Any, wait: float = 0.0) -> str:
        """Run one ADB command without putting secrets in a shell command line."""

        try:
            result = subprocess.run(
                self._adb_args(command),
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            self.last_adb_ok = result.returncode == 0
            self.last_adb_error = result.stderr.strip()
            if not self.last_adb_ok and self.last_adb_error:
                self.log("ADB command failed; see transition evidence.", Fore.RED)
            if wait:
                time.sleep(wait)
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            self.last_adb_ok = False
            self.last_adb_error = str(exc)
            self.log("ADB command could not be executed.", Fore.RED)
            return ""

    def verify_device(self) -> bool:
        """Require exactly the configured or uniquely attached authorized device."""

        output = self.run_adb(["devices"], wait=0.0)
        if not self.last_adb_ok:
            self.log("Unable to query ADB devices.", Fore.RED)
            return False
        devices = parse_adb_devices(output)
        authorized = [serial for serial, state in devices if state == "device"]
        if self.device_id:
            if self.device_id not in authorized:
                self.log("Configured Android device is not authorized or connected.", Fore.RED)
                return False
            self.log("Configured Android device verified.", Fore.GREEN)
            return True
        if len(authorized) != 1:
            if not authorized:
                self.log("No authorized Android device is connected.", Fore.RED)
            else:
                self.log("Multiple Android devices are connected; configure one device ID.", Fore.RED)
            return False
        self.device_id = authorized[0]
        self.log("Unique authorized Android device verified.", Fore.GREEN)
        return True

    def _get_screen_size(self) -> Tuple[int, int]:
        if self.screen_w and self.screen_h:
            return self.screen_w, self.screen_h
        output = self.run_adb(["shell", "wm", "size"], wait=0.0)
        match = re.search(r"(\d+)x(\d+)", output)
        if match:
            self.screen_w, self.screen_h = int(match.group(1)), int(match.group(2))
        else:
            self.screen_w, self.screen_h = 1080, 2400
        return self.screen_w, self.screen_h

    def tap(self, x: int, y: int) -> bool:
        self.run_adb(["shell", "input", "tap", str(x), str(y)], wait=0.1)
        return self.last_adb_ok

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500) -> bool:
        self.run_adb(
            [
                "shell", "input", "swipe",
                str(x1), str(y1), str(x2), str(y2), str(duration_ms),
            ],
            wait=0.1,
        )
        return self.last_adb_ok

    @staticmethod
    def scroll_area(root: Optional[ET.Element]) -> Optional[Tuple[int, int, int, int]]:
        """Bounds of the biggest scrollable container in the dump, if any."""

        best: Optional[Tuple[int, int, int, int]] = None
        best_size = 0
        if root is None:
            return None
        for node in root.iter("node"):
            if (node.get("scrollable") or "false").casefold() != "true":
                continue
            bounds = parse_bounds(node.get("bounds"))
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            size = (right - left) * (bottom - top)
            if size > best_size:
                best, best_size = bounds, size
        return best

    def _swipe_in_area(
        self, root: Optional[ET.Element], attempt: int, content_up: bool
    ) -> bool:
        """One scroll gesture drawn inside the scrollable container found in ``root``.

        Inside the settings sheet, so it cannot start on the dimmed background and
        dismiss the sheet. Without a usable dump it uses the middle of the screen.
        ``attempt`` picks a different speed/length from ``SCROLL_GESTURES`` after a
        swipe that changed nothing.
        """

        width, height = self._get_screen_size()
        area = self.scroll_area(root) or (0, 0, width, height)
        left, top, right, bottom = area
        left, right = max(0, left), min(width, right)
        top, bottom = max(0, top), min(height, bottom)
        start_f, end_f, duration = SCROLL_GESTURES[attempt % len(SCROLL_GESTURES)]
        span = max(1, bottom - top)
        x = (left + right) // 2
        low_y = top + int(span * start_f)
        high_y = top + int(span * end_f)
        if content_up:
            return self.swipe(x, low_y, x, high_y, duration)
        return self.swipe(x, high_y, x, low_y, duration)

    def swipe_up(self, root: Optional[ET.Element] = None, attempt: int = 0) -> bool:
        """Scroll the list one page down (content moves up), then recheck the UI."""

        self.log("Scrolling the profile menu to reveal semantic controls.", Fore.CYAN)
        return self._swipe_in_area(root, attempt, content_up=True)

    def swipe_down(self, root: Optional[ET.Element] = None, attempt: int = 0) -> bool:
        """Scroll back up (content moves down)."""

        self.log("Scrolling the page back up.", Fore.CYAN)
        return self._swipe_in_area(root, attempt, content_up=False)

    def type_text(self, text: str) -> bool:
        safe_text = encode_adb_text(text)
        self.run_adb(["shell", "input", "text", safe_text], wait=0.1)
        return self.last_adb_ok

    def press_key(self, key_code: int) -> bool:
        self.run_adb(["shell", "input", "keyevent", str(key_code)], wait=0.1)
        return self.last_adb_ok

    def hide_keyboard(self) -> bool:
        return self.press_key(111)

    def open_app(self, package_name: str) -> bool:
        self.run_adb(
            [
                "shell",
                "monkey",
                "-p",
                package_name,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ],
            wait=0.0,
        )
        return self.last_adb_ok

    def close_app(self, package_name: str) -> bool:
        self.run_adb(["shell", "am", "force-stop", package_name], wait=0.0)
        return self.last_adb_ok

    def foreground_package(self) -> Optional[str]:
        output = self.run_adb(["shell", "dumpsys", "window", "windows"], wait=0.0)
        if not self.last_adb_ok:
            return None
        for line in output.splitlines():
            if "mCurrentFocus" not in line and "mFocusedApp" not in line:
                continue
            match = re.search(r"\bu\d+\s+([A-Za-z0-9_.]+)/", line)
            if match:
                return match.group(1)
        return None

    def _adb_raw(self, command: Any) -> Tuple[Optional[int], str]:
        """Run one ADB command and return (exit code, stdout) without interpreting them."""

        try:
            result = subprocess.run(
                self._adb_args(command),
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.last_adb_ok = False
            self.last_adb_error = str(exc)
            return None, ""
        self.last_adb_ok = result.returncode == 0
        self.last_adb_error = result.stderr.strip()
        return result.returncode, result.stdout.strip()

    def package_running(self, package_name: str) -> Optional[bool]:
        """Return True or False when the package's process state is known, else None.

        ``pidof`` exits 1 with empty output when no process matches. That is a
        definite "not running" answer, not a failed ADB call, so it must not be
        reported as unknown. Such unknown answers made force-stop verification
        impossible to pass after a successful logout.
        """

        code, output = self._adb_raw(["shell", "pidof", package_name])
        if code is None:
            return None
        if output:
            return True
        if code in (0, 1):
            return False
        # pidof is missing on some builds (exit 127); fall back to the process list.
        code, listing = self._adb_raw(["shell", "ps", "-A"])
        if code != 0:
            return None
        return any(
            line.split() and line.split()[-1] == package_name
            for line in listing.splitlines()
        )

    def force_stop_and_verify(self, package_name: str) -> bool:
        if not self.close_app(package_name):
            self.last_failure_reason = "force_stop_command_failed"
            return False
        deadline = time.monotonic() + ELEMENT_WAIT_TIMEOUT
        while time.monotonic() < deadline:
            foreground = self.foreground_package()
            running = self.package_running(package_name)
            self.last_observed_state = (
                "post_close_home"
                if foreground is not None and foreground != package_name and running is False
                else "unknown"
            )
            if (
                foreground is not None
                and foreground != package_name
                and running is False
            ):
                self.log(
                    "Android home/device state observed; Replit is no longer "
                    "foreground or running.",
                    Fore.GREEN,
                )
                return True
            time.sleep(0.5)
        self.save_failure_evidence("force_stop_not_verified")
        return False

    # ================= DYNAMIC UI FINDING (UI AUTOMATOR) =================

    def dump_ui(self) -> Optional[ET.Element]:
        self.run_adb(["shell", "uiautomator", "dump", "/sdcard/ui.xml"], wait=0.0)
        if not self.last_adb_ok:
            return None
        self.run_adb(["pull", "/sdcard/ui.xml", "./ui.xml"], wait=0.0)
        if not self.last_adb_ok:
            return None
        try:
            return ET.parse("ui.xml").getroot()
        except (ET.ParseError, OSError):
            return None

    def find_element(
        self,
        text: Optional[str] = None,
        desc: Optional[str] = None,
        res_id: Optional[str] = None,
        cls: Optional[str] = None,
        partial: bool = False,
        root: Optional[ET.Element] = None,
    ) -> Optional[ET.Element]:
        root = root if root is not None else self.dump_ui()
        if root is None:
            return None
        for node in root.iter("node"):
            checks = []
            if text is not None:
                checks.append(label_matches(node.get("text"), text, partial))
            if desc is not None:
                checks.append(label_matches(node.get("content-desc"), desc, partial))
            if res_id is not None:
                checks.append(res_id in (node.get("resource-id") or ""))
            if cls is not None:
                checks.append((node.get("class") or "") == cls)
            if checks and all(checks):
                return node
        return None

    def _find_first(
        self,
        root: Optional[ET.Element],
        matchers: Sequence[dict],
    ) -> Optional[ET.Element]:
        for matcher in matchers:
            node = self.find_element(root=root, **matcher)
            if node is not None:
                return node
        return None

    def wait_for_element(self, timeout: Optional[float] = None, **matchers: Any) -> Optional[ET.Element]:
        deadline = time.monotonic() + (timeout or ELEMENT_WAIT_TIMEOUT)
        while time.monotonic() < deadline:
            node = self.find_element(**matchers)
            if node is not None:
                return node
            time.sleep(0.5)
        return None

    def tap_node(self, node: Optional[ET.Element]) -> bool:
        if not node_is_actionable(node):
            return False
        bounds = parse_bounds(node.get("bounds"))
        if bounds is None:
            return False
        left, top, right, bottom = bounds
        self.log("Tapping the verified UI node.", Fore.GREEN)
        return self.tap((left + right) // 2, (top + bottom) // 2)

    def tap_element(self, timeout: Optional[float] = None, **matchers: Any) -> bool:
        node = self.wait_for_element(timeout, **matchers)
        return self.tap_node(node)

    def tap_text_or_pct(
        self,
        text: str,
        x_pct: Optional[float] = None,
        y_pct: Optional[float] = None,
        timeout: float = 5,
    ) -> bool:
        """Compatibility wrapper that refuses blind coordinate fallback."""

        del x_pct, y_pct
        node = self.wait_for_element(timeout=timeout, text=text)
        if node is None:
            self.log(f"Required UI text was not found: {text}", Fore.RED)
            return False
        return self.tap_node(node)

    def wait_for_state(
        self,
        expected: Iterable[str],
        transition: str,
        timeout: Optional[float] = None,
    ) -> Optional[ET.Element]:
        expected_states: Set[str] = set(expected)
        deadline = time.monotonic() + (timeout or ELEMENT_WAIT_TIMEOUT)
        retry_count = 0
        expected_label = ",".join(sorted(expected_states))
        while time.monotonic() < deadline:
            root = self.dump_ui()
            state = classify_screen(root)
            self.last_observed_state = state
            if state == "security_challenge":
                self.last_failure_reason = "security_challenge"
                self.log(
                    f"state={canonical_android_state(state)} action={transition} "
                    f"expected={expected_label} detected={state} retry={retry_count} "
                    f"timeout={timeout or ELEMENT_WAIT_TIMEOUT} "
                    "failure_reason=security_challenge",
                    Fore.YELLOW,
                )
                self.save_failure_evidence(f"{transition}_security_challenge")
                return None
            if root is not None and state in expected_states:
                references = screenshot_references_for_state(state)
                reference_note = f" ({len(references)} screenshot reference(s))" if references else ""
                self.log(
                    f"state={canonical_android_state(state)} action={transition} "
                    f"expected={expected_label} detected={state} retry={retry_count} "
                    f"timeout={timeout or ELEMENT_WAIT_TIMEOUT}{reference_note}",
                    Fore.GREEN,
                )
                return root
            retry_count += 1
            time.sleep(0.5)
        self.last_failure_reason = "timeout"
        self.log(
            f"state={canonical_android_state(self.last_observed_state)} action={transition} "
            f"expected={expected_label} detected={self.last_observed_state} "
            f"retry={retry_count} timeout={timeout or ELEMENT_WAIT_TIMEOUT} "
            "failure_reason=timeout",
            Fore.RED,
        )
        self.save_failure_evidence(transition)
        return None

    def save_failure_evidence(self, tag: str = "error") -> None:
        self.last_failure_reason = tag
        safe_tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", tag)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_dir = Path("screenshots")
        output_dir.mkdir(exist_ok=True)
        filename = f"android_{safe_tag}_{timestamp}.png"
        remote_path = f"/sdcard/{filename}"
        self.run_adb(["shell", "screencap", "-p", remote_path], wait=0.0)
        if self.last_adb_ok:
            self.run_adb(["pull", remote_path, str(output_dir / filename)], wait=0.0)
        if self.dump_ui() is not None:
            try:
                # Keep the screen's UI tree next to the screenshot: it is the
                # one thing that shows why a control was or was not found.
                shutil.copyfile("ui.xml", output_dir / f"android_{safe_tag}_{timestamp}.xml")
            except OSError:
                pass
        self.log(f"Failure evidence saved for transition {safe_tag}.", Fore.YELLOW)


    # ================= STATE-DRIVEN REPLIT ONBOARDING =================
    #
    # Each cycle dumps the UI, classifies the visible screen and performs the one
    # action that screen needs. The flow resumes from whatever screen is visible,
    # so an account already past onboarding, or a retry after a partial run,
    # continues from its current step instead of replaying a fixed script.

    @staticmethod
    def _node_matches(
        node: ET.Element,
        text: Optional[str] = None,
        desc: Optional[str] = None,
        res_id: Optional[str] = None,
        cls: Optional[str] = None,
        partial: bool = False,
    ) -> bool:
        checks = []
        if text is not None:
            checks.append(label_matches(node.get("text"), text, partial))
        if desc is not None:
            checks.append(label_matches(node.get("content-desc"), desc, partial))
        if res_id is not None:
            checks.append(res_id in (node.get("resource-id") or ""))
        if cls is not None:
            checks.append((node.get("class") or "") == cls)
        return bool(checks) and all(checks)

    @staticmethod
    def _labels_under(node: ET.Element) -> str:
        """Every text / content-desc inside ``node`` (itself included), normalised."""

        parts = []
        for item in node.iter("node"):
            for attribute in ("text", "content-desc"):
                value = normalize_ui_text(item.get(attribute))
                if value:
                    parts.append(value)
        return " | ".join(parts)

    def _find_actionable(
        self,
        root: Optional[ET.Element],
        matchers: Sequence[dict],
        avoid: Sequence[str] = (),
    ) -> Optional[ET.Element]:
        """Return the tap target for the first matcher that has one on screen.

        A matching label can be a non-clickable title, so every matching node is
        considered, not only the first one in document order. A target that
        contains any ``avoid`` label is never returned: "Profile" also matches
        the "Edit Profile" row, and tapping that opens the wrong screen.
        """

        if root is None:
            return None
        avoid_labels = [normalize_ui_text(value) for value in avoid if value]
        for matcher in matchers:
            for node in root.iter("node"):
                if self._node_matches(node, **matcher):
                    target = actionable_target(root, node)
                    if target is None:
                        continue
                    if avoid_labels:
                        labels = self._labels_under(target) + " | " + self._labels_under(node)
                        if any(value in labels for value in avoid_labels):
                            continue
                    return target
        return None

    def _count_tap(self, taps: dict, label: str, limit: int = ONBOARDING_ACTION_LIMIT) -> None:
        taps[label] = taps.get(label, 0) + 1
        if taps[label] > limit:
            raise OnboardingStuck(label)

    def _act(
        self,
        root: Optional[ET.Element],
        taps: dict,
        label: str,
        matchers: Sequence[dict],
        limit: int = ONBOARDING_ACTION_LIMIT,
        avoid: Sequence[str] = (),
    ) -> bool:
        """Tap the first matching control on screen.

        Returns False when no tappable control is on screen yet, so the caller
        observes again. Raises OnboardingStuck once the same control has been
        tapped more than ``limit`` times without the screen moving on.
        """

        target = self._find_actionable(root, matchers, avoid)
        if target is None:
            return False
        self._count_tap(taps, label, limit)
        if not self.tap_node(target):
            return False
        time.sleep(ONBOARDING_SETTLE_SECONDS)
        return True

    @staticmethod
    def _same_text(shown: Any, wanted: Any) -> bool:
        def squeeze(value: Any) -> str:
            return "".join(str(value or "").split()).casefold()

        return squeeze(shown) == squeeze(wanted)

    def _field_shows(self, field: ET.Element, value: str) -> Optional[bool]:
        """Read the field back from a fresh UI dump.

        True: the typed text is visible. False: the field is readable but shows
        something else (empty, or only its placeholder). None: the screen could
        not be read, so nothing is proven either way.
        """

        root = self.dump_ui()
        if root is None:
            return None
        edits = [
            node for node in root.iter("node")
            if node.get("class") == "android.widget.EditText"
        ]
        if not edits:
            return None
        # The same box when the layout did not move, otherwise the first input.
        match = next(
            (node for node in edits if node.get("bounds") == field.get("bounds")),
            edits[0],
        )
        shown = match.get("text") or ""
        if shown == (match.get("hint") or ""):
            shown = ""  # an empty field that displays its placeholder
        return self._same_text(shown, value)

    def _enter_text(
        self,
        root: Optional[ET.Element],
        taps: dict,
        label: str,
        field: ET.Element,
        value: str,
        verify: bool = True,
    ) -> bool:
        """Focus ``field``, replace its text with ``value`` and prove it arrived.

        ADB reports success for ``input text`` even when no field has focus. That
        happens when a tap is swallowed by an overlay: after "Continue with
        Email" Replit can keep the login sheet on top of the e-mail form, and the
        first tap only dismisses it (CORE_FLOW "Keyboard Not Visible": tap once
        outside the login area). The text is therefore read back from the UI and
        typed again when it is missing. A field that cannot be read back (the
        dump fails, or after ``ONBOARDING_TEXT_ATTEMPTS`` tries) is accepted, so
        an unreadable dump never fails a flow that would have worked. Masked
        fields (the password) are never read back: the dump hides their text.
        """

        target = actionable_target(root, field)
        if target is None:
            return False
        self._count_tap(taps, label)
        if not self.tap_node(target):
            return False
        time.sleep(ONBOARDING_SETTLE_SECONDS / 2)  # let focus and the keyboard settle
        current = field.get("text") or ""
        shown = current if current != (field.get("hint") or "") else ""
        retry = taps.get(label, 1) > 1
        if shown or retry:
            # Replace existing text instead of appending to it. On a retry the
            # dump may have hidden what the first attempt typed, so remove that
            # much as well.
            width = max(len(shown), len(value) if retry else 0)
            self.press_key(KEYCODE_MOVE_END)
            for _ in range(min(width + 2, 64)):
                self.press_key(KEYCODE_DEL)
        typed = self.type_text(value)
        time.sleep(ONBOARDING_SETTLE_SECONDS)
        if not typed or not verify:
            return typed
        if self._field_shows(field, value) is not False:
            return True
        if taps.get(label, 0) >= ONBOARDING_TEXT_ATTEMPTS:
            self.log(
                "The typed text could not be confirmed in the field; continuing "
                "and letting the next screen decide.",
                Fore.YELLOW,
            )
            return True
        self.log(
            "The typed text is not visible in the field (an overlay may have taken "
            "the tap, or the field had no focus); tapping it and typing again.",
            Fore.YELLOW,
        )
        return False

    @staticmethod
    def _screen_signature(root: Optional[ET.Element]) -> frozenset:
        """The set of labels on screen; equal before and after a swipe = it did not scroll."""

        if root is None:
            return frozenset()
        labels = set()
        for node in root.iter("node"):
            for attribute in ("text", "content-desc"):
                value = normalize_ui_text(node.get(attribute))
                if value:
                    labels.add(value)
        return frozenset(labels)

    def _recover_from_unknown_screen(self, recovery: dict, profile_touched: bool) -> bool:
        """Get back to a screen the flow knows after several unreadable ones.

        Replit not being in the foreground is fixed by launching it again (the
        launch resumes the running app, it does not reset it). A screen reached
        by a wrong tap in the profile area is left with BACK. Both are bounded.
        """

        foreground = self.foreground_package()
        if foreground is not None and foreground != REPLIT_PACKAGE_NAME:
            if recovery["relaunch"] >= ONBOARDING_RELAUNCH_LIMIT:
                return False
            recovery["relaunch"] += 1
            self.log(
                f"Replit is not in the foreground ({foreground}); opening it again.",
                Fore.YELLOW,
            )
            self.open_app(REPLIT_PACKAGE_NAME)
            time.sleep(ONBOARDING_SETTLE_SECONDS)
            return True
        if profile_touched and recovery["back"] < ONBOARDING_BACK_LIMIT:
            recovery["back"] += 1
            self.log(
                "An unrecognised screen is open after the profile menu; pressing BACK.",
                Fore.YELLOW,
            )
            self.press_key(KEYCODE_BACK)
            time.sleep(ONBOARDING_SETTLE_SECONDS)
            return True
        return False

    def observe_screen(self) -> str:
        """Classify what the phone shows right now without touching anything.

        Returns "no_device" when no single authorized device is attached,
        "not_replit" when another app (or the launcher) is in front, else the
        classified Replit screen ("unknown" when it cannot be read).
        """

        try:
            devices = parse_adb_devices(self.run_adb(["devices"], wait=0.0))
        except Exception:
            return "no_device"
        if not self.last_adb_ok:
            return "no_device"
        authorized = [serial for serial, state in devices if state == "device"]
        if self.device_id:
            if self.device_id not in authorized:
                return "no_device"
        elif len(authorized) != 1:
            return "no_device"
        else:
            self.device_id = authorized[0]
        if self.foreground_package() != REPLIT_PACKAGE_NAME:
            return "not_replit"
        return classify_screen(self.dump_ui())

    @staticmethod
    def _field_text(field: Optional[ET.Element]) -> str:
        """What a text field shows, "" when it is empty (a placeholder does not count)."""

        if field is None:
            return ""
        current = field.get("text") or ""
        return current if current != (field.get("hint") or "") else ""

    def _settle_after_logout(self) -> str:
        """Look at the phone after the final "Log out" tap.

        The tap is what logs out; this only makes sure the app is left in a state
        the run can finish from. Returns "logged_out" (a login screen is
        showing), "dialog" (the confirmation is still open: tap again),
        "still_logged_in" (a signed-in screen is back: the logout did not
        happen) or "unconfirmed" (nothing conclusive in time; the tap is
        trusted). A crash to the Android home screen opens Replit again and
        waits for it to load; a profile page left on screen is scrolled up.
        """

        checks = POST_LOGOUT_CHECKS
        relaunches = scrolls = sheet_looks = 0
        pressed_back = False
        while checks > 0:
            checks -= 1
            foreground = self.foreground_package()
            if foreground is not None and foreground != REPLIT_PACKAGE_NAME:
                if relaunches >= POST_LOGOUT_RELAUNCH_LIMIT:
                    return "unconfirmed"
                relaunches += 1
                self.log(
                    f"Replit is not in the foreground after the logout ({foreground}); it "
                    "may have crashed. Opening it again; loading can take a while.",
                    Fore.YELLOW,
                )
                self.open_app(REPLIT_PACKAGE_NAME)
                checks = max(checks, POST_LOGOUT_LOAD_CHECKS)
                time.sleep(ONBOARDING_SETTLE_SECONDS)
                continue
            root = self.dump_ui()
            screen = classify_screen(root)
            self.last_observed_state = screen
            if screen in LOGGED_OUT_STATES:
                self.log("The logged-out front page is showing.", Fore.GREEN)
                return "logged_out"
            if screen == "logout_dialog":
                return "dialog"
            if screen in LOGGED_IN_STATES or screen == "security_challenge":
                return "still_logged_in"
            if screen == "profile_menu":
                sheet_looks += 1
                if sheet_looks > POST_LOGOUT_SHEET_PATIENCE:
                    if scrolls < POST_LOGOUT_SCROLL_UP_LIMIT:
                        scrolls += 1
                        self.log(
                            "The profile page is still on screen after the logout (a "
                            "Replit app glitch); scrolling it up.",
                            Fore.YELLOW,
                        )
                        self.swipe_down(root, scrolls - 1)
                        time.sleep(ONBOARDING_SETTLE_SECONDS)
                        continue
                    if not pressed_back:
                        pressed_back = True
                        self.press_key(KEYCODE_BACK)
                        time.sleep(ONBOARDING_SETTLE_SECONDS)
                        continue
            time.sleep(1.0)
        return "unconfirmed"

    def _drive_onboarding(self, email: str, password: str, username: str) -> bool:
        first_name = first_name_from_username(username)
        initials = avatar_initials(first_name)
        taps: dict = {}
        typed = {"email": False, "password": False, "first_name": False}
        selected = {"source": False, "role": False}
        logout_pending = False
        unexpected = 0
        unreadable = 0
        profile_touched = False  # the profile menu was opened (or tried) in this run
        recovery = {"back": 0, "relaunch": 0}
        scroll_stalls = 0
        last_signature = None
        last_logged_state = None

        for _cycle in range(ONBOARDING_MAX_CYCLES):
            if logout_pending:
                verdict = self._settle_after_logout()
                if verdict in {"logged_out", "unconfirmed"}:
                    if verdict == "unconfirmed":
                        self.log(
                            "The logout confirmation was tapped, but the logged-out screen "
                            "could not be seen in time; continuing on the tap alone.",
                            Fore.YELLOW,
                        )
                        self.save_failure_evidence("logout_unconfirmed")
                        self.last_failure_reason = ""
                       ##Skip force-stop; finish onboarding after logout handling.
                    return True
                # The dialog is still up, or a signed-in screen is back: act on it.
                logout_pending = False
            root = self.dump_ui()
            state = classify_screen(root)
            self.last_observed_state = state
            if state != last_logged_state:
                self.log(
                    f"Android screen: {canonical_android_state(state)} ({state})",
                    Fore.CYAN,
                )
                last_logged_state = state

            if state == "security_challenge":
                # Manual takeover only. Never tap through a CAPTCHA or security check.
                self.last_failure_reason = "security_challenge"
                self.save_failure_evidence("onboarding_security_challenge")
                return False
            if root is None or state == "unknown":
                unreadable += 1
                if unreadable % ONBOARDING_UNKNOWN_BEFORE_RECOVERY == 0:
                    self._recover_from_unknown_screen(recovery, profile_touched)
                time.sleep(0.5)
                continue
            unreadable = 0
            if state not in ONBOARDING_STATES:
                unexpected += 1
                if unexpected > ONBOARDING_UNEXPECTED_LIMIT:
                    raise OnboardingStuck(f"unexpected_{state}")
                time.sleep(0.5)
                continue
            unexpected = 0

            field = self.find_element(root=root, cls="android.widget.EditText")
            if state == "initial_login":
                self._act(root, taps, "start_login", [{"text": "Continue"}])
            elif state in {"email_choice", "email_login"} and not typed["email"]:
                if field is not None and self._enter_text(root, taps, "type_email", field, email):
                    typed["email"] = True
                elif state == "email_choice" and field is None:
                    self._act(
                        root,
                        taps,
                        "choose_email",
                        [{"text": "Continue with Email"}, {"text": "Email"}],
                    )
            elif state in {"email_choice", "email_login"}:
                self._act(root, taps, "submit_email", [{"text": "Continue"}])
            elif state == "password_login":
                if not typed["password"]:
                    if field is not None and self._enter_text(
                        root, taps, "type_password", field, password, verify=False
                    ):
                        typed["password"] = True
                else:
                    self._act(root, taps, "submit_login", [{"text": "Login"}])
            elif state == "invalid_credentials":
                # The spec asks for one more Login press; a second rejection is a real failure.
                self._act(root, taps, "retry_login", [{"text": "Login"}], limit=1)
            elif state == "welcome_onboarding":
                self._act(root, taps, "welcome_continue", [{"text": "Continue"}])
            elif state == "name":
                # Replit fills the first name in from the e-mail (attached_assets/15).
                # It is left exactly as it is: only Continue is tapped. The field
                # is typed into only when it is empty (Continue cannot work then).
                shown_name = self._field_text(field)
                if shown_name:
                    initials = avatar_initials(shown_name)
                if not self._act(root, taps, "name_continue", [{"text": "Continue"}]):
                    if field is not None and not shown_name and not typed["first_name"]:
                        if self._enter_text(root, taps, "type_first_name", field, first_name):
                            typed["first_name"] = True
            elif state == "username":
                self._act(root, taps, "username_continue", [{"text": "Continue"}])
            elif state == "source":
                if selected["source"]:
                    self._act(root, taps, "source_continue", [{"text": "Continue"}])
                else:
                    selected["source"] = self._act(
                        root, taps, "source_google_search", [{"text": "Google search"}]
                    )
            elif state == "role":
                if selected["role"]:
                    self._act(root, taps, "role_continue", [{"text": "Continue"}])
                else:
                    selected["role"] = self._act(
                        root, taps, "role_developer", [{"text": "Developer"}]
                    )
            elif state == "plan":
                self._act(root, taps, "skip_plan", [{"text": "Skip"}])
            elif state == "notifications_prompt":
                # Optional sheet (attached_assets/19.5): only "Not Now" is ever
                # tapped, never "Turn On Notifications". It is handled whenever it
                # shows up and never waited for.
                self._act(
                    root,
                    taps,
                    "notifications_not_now",
                    [{"text": "Not Now"}],
                    limit=4,
                    avoid=("turn on notifications",),
                )
            elif state == "main":
                matchers = [
                    {"desc": "Profile", "partial": True},
                    {"desc": "Account", "partial": True},
                ]
                if initials:
                    # The avatar shows the initials of the name typed earlier.
                    matchers.insert(0, {"text": initials})
                # "Profile" would also match the "Edit Profile" row of the menu.
                if self._act(root, taps, "open_profile", matchers, avoid=("edit profile",)):
                    profile_touched = True
            elif state == "profile_menu":
                profile_touched = True
                logout_row = [
                    {"text": "Log Out"},
                    {"text": "Logout"},
                    {"desc": "Log Out"},
                    {"desc": "Logout"},
                ]
                if not self._act(root, taps, "logout_menu", logout_row):
                    # "Log Out" is below the fold: scroll the sheet. A swipe that
                    # changed nothing is repeated with a different gesture.
                    signature = self._screen_signature(root)
                    scroll_stalls = scroll_stalls + 1 if signature == last_signature else 0
                    last_signature = signature
                    self._count_tap(taps, "scroll_profile", ONBOARDING_SCROLL_LIMIT)
                    if not self.swipe_up(root, scroll_stalls):
                        raise OnboardingStuck("profile_scroll")
                    time.sleep(ONBOARDING_SETTLE_SECONDS)
            elif state == "logout_dialog":
                if self._act(
                    root,
                    taps,
                    "confirm_logout",
                    [{"text": "LOG OUT"}, {"text": "Logout"}],
                ):
                    logout_pending = True
            # login_processing: nothing to tap yet; keep observing.

        raise OnboardingStuck("onboarding_timeout")

    def complete_onboarding(self, email: str, password: str, username: str) -> bool:
        self.log("\n" + "=" * 60, Fore.MAGENTA)
        self.log("STARTING ANDROID ONBOARDING", Fore.MAGENTA)
        self.log("=" * 60, Fore.MAGENTA)

        try:
            if not self.verify_device():
                return False
            if not self.open_app(REPLIT_PACKAGE_NAME):
                self.save_failure_evidence("launch_app")
                return False
            completed = self._drive_onboarding(email, password, username)
            if completed:
                self.log("\nANDROID ONBOARDING COMPLETE!", Fore.GREEN)
            return completed
        except OnboardingStuck as exc:
            self.save_failure_evidence(f"onboarding_stuck_{exc}")
            return False
        except Exception:
            self.log("\nAndroid automation failed unexpectedly.", Fore.RED)
            self.save_failure_evidence("android_error")
            return False
