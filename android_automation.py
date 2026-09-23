"""State-driven Android automation for the operator-owned Replit device.

The UI hierarchy is authoritative. Coordinate input is limited to the bounded
swipe used to reveal the profile-menu logout control; taps never fall back to
blind percentage coordinates.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

from colorama import Fore, Style

try:
    from config import ELEMENT_WAIT_TIMEOUT, REPLIT_PACKAGE_NAME
except ImportError:
    ELEMENT_WAIT_TIMEOUT = 10
    REPLIT_PACKAGE_NAME = "com.replit.app"


BOUNDS_PATTERN = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
EMAIL_LOGIN_TEXT = ("continue with email", "email or username")
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


def classify_screen(root: Optional[ET.Element]) -> str:
    """Classify the visible Android state represented by a UI dump."""

    text = _node_text(root).casefold()
    if "are you sure you want to log out" in text:
        return "logout_dialog"
    if "invalid username or password" in text:
        return "invalid_credentials"
    if "what are we working on today" in text:
        return "main"
    if "usage" in text and "edit profile" in text:
        return "profile_menu"
    if "start your building journey" in text:
        return "plan"
    if "what best describes you" in text:
        return "role"
    if "how did you hear about replit" in text:
        return "source"
    if "let's create a username" in text or "let’s create a username" in text:
        return "username"
    if "what's your name" in text or "what’s your name" in text:
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

    def swipe_up(self) -> bool:
        """Use one bounded scroll gesture, then require a UI-state recheck."""

        width, height = self._get_screen_size()
        x = width // 2
        start_y = int(height * 0.78)
        end_y = int(height * 0.28)
        self.log("Scrolling the profile menu to reveal semantic controls.", Fore.CYAN)
        self.run_adb(
            ["shell", "input", "swipe", str(x), str(start_y), str(x), str(end_y), "500"],
            wait=0.1,
        )
        return self.last_adb_ok

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

    def package_running(self, package_name: str) -> Optional[bool]:
        """Return whether Android still reports a process for the package."""

        output = self.run_adb(["shell", "pidof", package_name], wait=0.0)
        if not self.last_adb_ok:
            return None
        return bool(output.strip())

    def force_stop_and_verify(self, package_name: str) -> bool:
        if not self.close_app(package_name):
            return False
        deadline = time.monotonic() + ELEMENT_WAIT_TIMEOUT
        while time.monotonic() < deadline:
            foreground = self.foreground_package()
            running = self.package_running(package_name)
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
                value = node.get("text") or ""
                checks.append(text in value if partial else value == text)
            if desc is not None:
                value = node.get("content-desc") or ""
                checks.append(desc in value if partial else value == desc)
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
        while time.monotonic() < deadline:
            root = self.dump_ui()
            state = classify_screen(root)
            if root is not None and state in expected_states:
                references = screenshot_references_for_state(state)
                reference_note = f" ({len(references)} screenshot reference(s))" if references else ""
                self.log(
                    f"{transition}: observed Android state {state}{reference_note}.",
                    Fore.GREEN,
                )
                return root
            time.sleep(0.5)
        self.save_failure_evidence(transition)
        return None

    def _tap_and_wait(
        self,
        transition: str,
        source_states: Iterable[str],
        matchers: Sequence[dict],
        destination_states: Iterable[str],
    ) -> Optional[ET.Element]:
        root = self.wait_for_state(source_states, f"{transition}_source")
        if root is None:
            return None
        node = self._find_first(root, matchers)
        if not self.tap_node(node):
            self.save_failure_evidence(f"{transition}_tap")
            return None
        return self.wait_for_state(destination_states, transition)

    def _type_into_field(self, root: Optional[ET.Element], value: str, transition: str) -> bool:
        node = self.find_element(root=root, cls="android.widget.EditText")
        if node is None or not self.tap_node(node) or not self.type_text(value):
            self.save_failure_evidence(f"{transition}_input")
            return False
        return True

    def save_failure_evidence(self, tag: str = "error") -> None:
        safe_tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", tag)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_dir = Path("screenshots")
        output_dir.mkdir(exist_ok=True)
        filename = f"android_{safe_tag}_{timestamp}.png"
        remote_path = f"/sdcard/{filename}"
        self.run_adb(["shell", "screencap", "-p", remote_path], wait=0.0)
        if self.last_adb_ok:
            self.run_adb(["pull", remote_path, str(output_dir / filename)], wait=0.0)
        self.dump_ui()
        self.log(f"Failure evidence saved for transition {safe_tag}.", Fore.YELLOW)

    # ================= HIGH-LEVEL REPLIT FLOW =================

    def _run_login(self, email: str, password: str) -> bool:
        root = self.wait_for_state(
            {"initial_login", "email_choice", "email_login"},
            "launch_initial_state",
        )
        if root is None:
            return False
        state = classify_screen(root)
        if state == "initial_login":
            root = self._tap_and_wait(
                "welcome_continue",
                {"initial_login"},
                [{"text": "Continue"}],
                {"email_choice", "email_login"},
            )
            if root is None:
                return False
            state = classify_screen(root)
        if state == "email_choice":
            root = self._tap_and_wait(
                "select_email",
                {"email_choice"},
                [{"text": "Continue with Email"}, {"text": "Email"}],
                {"email_login"},
            )
            if root is None:
                return False

        root = self.wait_for_state({"email_login"}, "email_login_screen")
        if root is None or not self._type_into_field(root, email, "email"):
            return False
        root = self._tap_and_wait(
            "submit_email",
            {"email_login"},
            [{"text": "Continue"}],
            {"password_login"},
        )
        if root is None:
            return False

        if not self._type_into_field(root, password, "password"):
            return False
        root = self._tap_and_wait(
            "submit_login",
            {"password_login"},
            [{"text": "Login"}],
            {"invalid_credentials", "welcome_onboarding", "login_processing"},
        )
        if root is None:
            return False
        state = classify_screen(root)
        if state == "login_processing":
            root = self.wait_for_state(
                {"invalid_credentials", "welcome_onboarding"},
                "login_result",
            )
            if root is None:
                return False
            state = classify_screen(root)
        if state == "invalid_credentials":
            self.log("Invalid credentials banner observed; retrying Login once.", Fore.YELLOW)
            root = self._tap_and_wait(
                "retry_login",
                {"invalid_credentials"},
                [{"text": "Login"}],
                {"invalid_credentials", "welcome_onboarding"},
            )
            if root is None or classify_screen(root) == "invalid_credentials":
                self.save_failure_evidence("login_retry_failed")
                return False
        return True

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
            if not self._run_login(email, password):
                return False

            root = self._tap_and_wait(
                "welcome_onboarding_continue",
                {"welcome_onboarding"},
                [{"text": "Continue"}],
                {"name"},
            )
            if root is None:
                return False

            first_name = re.split(r"\d", username or "")[0].strip() or username
            if not self._type_into_field(root, first_name.capitalize(), "first_name"):
                return False
            root = self._tap_and_wait(
                "name_continue",
                {"name"},
                [{"text": "Continue"}],
                {"username"},
            )
            if root is None:
                return False

            root = self._tap_and_wait(
                "username_continue",
                {"username"},
                [{"text": "Continue"}],
                {"source"},
            )
            if root is None:
                return False

            root = self._tap_and_wait(
                "source_google_search",
                {"source"},
                [{"text": "Google search"}],
                {"source"},
            )
            if root is None:
                return False
            root = self._tap_and_wait(
                "source_continue",
                {"source"},
                [{"text": "Continue"}],
                {"role"},
            )
            if root is None:
                return False

            root = self._tap_and_wait(
                "role_developer",
                {"role"},
                [{"text": "Developer"}],
                {"role"},
            )
            if root is None:
                return False
            root = self._tap_and_wait(
                "role_continue",
                {"role"},
                [{"text": "Continue"}],
                {"plan"},
            )
            if root is None:
                return False

            root = self._tap_and_wait(
                "skip_plan",
                {"plan"},
                [{"text": "Skip"}],
                {"main"},
            )
            if root is None:
                return False

            root = self._tap_and_wait(
                "open_profile",
                {"main"},
                [
                    {"text": "MI"},
                    {"desc": "Profile", "partial": True},
                    {"desc": "Account", "partial": True},
                ],
                {"profile_menu"},
            )
            if root is None:
                return False

            logout_node: Optional[ET.Element] = None
            for _ in range(4):
                logout_node = self.find_element(root=root, text="Log Out")
                if logout_node is not None and node_is_actionable(logout_node):
                    break
                if not self.swipe_up():
                    self.save_failure_evidence("profile_scroll")
                    return False
                root = self.wait_for_state({"profile_menu"}, "profile_scroll")
                if root is None:
                    return False
            if logout_node is None or not node_is_actionable(logout_node):
                self.save_failure_evidence("logout_not_visible")
                return False
            if not self.tap_node(logout_node):
                self.save_failure_evidence("logout_tap")
                return False
            root = self.wait_for_state({"logout_dialog"}, "logout_dialog")
            if root is None:
                return False
            root = self.wait_for_state({"logout_dialog"}, "confirm_logout_source")
            if root is None:
                return False
            logout_confirm = self._find_first(root, [{"text": "LOG OUT"}])
            if not self.tap_node(logout_confirm):
                self.save_failure_evidence("confirm_logout_tap")
                return False
            # Logout is a device-terminal transition. Do not require another
            # recognized Replit screen after the app has accepted the action.
            if not self.force_stop_and_verify(REPLIT_PACKAGE_NAME):
                return False

            self.log("\nANDROID ONBOARDING COMPLETE!", Fore.GREEN)
            return True
        except Exception:
            self.log("\nAndroid automation failed unexpectedly.", Fore.RED)
            self.save_failure_evidence("android_error")
            return False