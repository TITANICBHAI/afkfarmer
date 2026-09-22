import subprocess
import time
import re
import xml.etree.ElementTree as ET
from colorama import Fore, Style

try:
    from config import ELEMENT_WAIT_TIMEOUT, REPLIT_PACKAGE_NAME, ANDROID_STEP_DELAY
except ImportError:
    ELEMENT_WAIT_TIMEOUT = 10
    REPLIT_PACKAGE_NAME = "com.replit.android"
    ANDROID_STEP_DELAY = 2.0


class AndroidAutomation:
    def __init__(self, device_id=None):
        self.device_id = device_id
        self.log_file = "android_actions.log"
        self.screen_w, self.screen_h = self._get_screen_size()
        self.log(f"Screen size detected: {self.screen_w}x{self.screen_h}", Fore.CYAN)

    # ================= LOW-LEVEL ADB HELPERS =================

    def log(self, message, color=Fore.WHITE):
        timestamp = time.strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        print(f"{color}{log_message}{Style.RESET_ALL}")
        with open(self.log_file, 'a') as f:
            f.write(f"{log_message}\n")

    def run_adb(self, command, wait=1.0):
        try:
            prefix = f"adb -s {self.device_id} " if self.device_id else "adb "
            result = subprocess.run(
                prefix + command, shell=True,
                capture_output=True, text=True, timeout=20
            )
            if wait:
                time.sleep(wait)
            return result.stdout.strip()
        except Exception as e:
            self.log(f"ADB Error: {e}", Fore.RED)
            return ""

    def _get_screen_size(self):
        out = self.run_adb("shell wm size", wait=0.5)
        m = re.search(r'(\d+)x(\d+)', out)
        if m:
            return int(m.group(1)), int(m.group(2))
        return 1080, 2400  # safe fallback

    def tap(self, x, y, wait=1.5):
        self.run_adb(f"shell input tap {x} {y}", wait)

    def tap_pct(self, x_pct, y_pct, wait=1.5):
        """Tap by percentage of screen -> works on ANY resolution."""
        x = int(self.screen_w * x_pct / 100)
        y = int(self.screen_h * y_pct / 100)
        self.tap(x, y, wait)

    def swipe_pct(self, x1, y1, x2, y2, duration=500, wait=1.0):
        X1 = int(self.screen_w * x1 / 100); Y1 = int(self.screen_h * y1 / 100)
        X2 = int(self.screen_w * x2 / 100); Y2 = int(self.screen_h * y2 / 100)
        self.run_adb(f"shell input swipe {X1} {Y1} {X2} {Y2} {duration}", wait)

    def type_text(self, text):
        safe_text = text.replace(' ', '%s')
        self.run_adb(f'shell input text "{safe_text}"', wait=0.5)

    def press_key(self, key_code, wait=1.0):
        self.run_adb(f"shell input keyevent {key_code}", wait)

    def press_enter(self):
        self.press_key(66)

    def hide_keyboard(self):
        """ESCAPE hides the soft keyboard without leaving the screen."""
        self.run_adb("shell input keyevent 111", wait=0.8)

    def open_app(self, package_name):
        self.run_adb(
            f"shell monkey -p {package_name} -c android.intent.category.LAUNCHER 1",
            wait=3.0
        )

    def close_app(self, package_name):
        self.run_adb(f"shell am force-stop {package_name}", wait=1.0)

    def take_screenshot(self, filename="debug.png"):
        self.run_adb(f"shell screencap -p /sdcard/{filename}")
        self.run_adb(f"pull /sdcard/{filename} ./{filename}", wait=1.0)
        self.log(f"Screenshot saved as {filename}", Fore.CYAN)

    # ================= DYNAMIC UI FINDING (uiautomator) =================

    def dump_ui(self):
        self.run_adb("shell uiautomator dump /sdcard/ui.xml", wait=1.0)
        self.run_adb("pull /sdcard/ui.xml ./ui.xml", wait=1.0)
        try:
            return ET.parse('ui.xml').getroot()
        except Exception:
            return None

    def find_element(self, text=None, desc=None, res_id=None, cls=None, partial=False):
        root = self.dump_ui()
        if root is None:
            return None
        for node in root.iter('node'):
            ok = True
            if text is not None:
                ntext = node.get('text') or ''
                ok = ok and (text in ntext if partial else ntext == text)
            if desc is not None:
                ok = ok and desc in (node.get('content-desc') or '')
            if res_id is not None:
                ok = ok and res_id in (node.get('resource-id') or '')
            if cls is not None:
                ok = ok and (node.get('class') or '') == cls
            if ok:
                return node
        return None

    def wait_for_element(self, timeout=None, **matchers):
        timeout = timeout or ELEMENT_WAIT_TIMEOUT
        deadline = time.time() + timeout
        while time.time() < deadline:
            node = self.find_element(**matchers)
            if node is not None:
                return node
            time.sleep(1)
        return None

    def tap_element(self, timeout=None, **matchers):
        node = self.wait_for_element(timeout, **matchers)
        if node is None:
            return False
        nums = [int(n) for n in re.findall(r'\d+', node.get('bounds') or '')]
        if len(nums) != 4:
            return False
        cx = (nums[0] + nums[2]) // 2
        cy = (nums[1] + nums[3]) // 2
        self.log(f"Dynamic tap on {matchers} at ({cx},{cy})", Fore.GREEN)
        self.tap(cx, cy, wait=1.5)
        return True

    def tap_text_or_pct(self, text, x_pct, y_pct, timeout=5):
        """Try dynamic text find first; fall back to percentage tap."""
        if self.tap_element(timeout=timeout, text=text):
            return True
        self.log(f"'{text}' not found in UI dump -> fallback % tap", Fore.YELLOW)
        self.tap_pct(x_pct, y_pct)
        return True

    def save_failure_evidence(self, tag="error"):
        self.take_screenshot(f"{tag}.png")
        self.dump_ui()
        self.log(f"Failure evidence saved: {tag}.png + ui.xml", Fore.YELLOW)

    # ================= HIGH-LEVEL REPLIT FLOW =================

    def complete_onboarding(self, email, password, username):
        self.log("\n" + "=" * 60, Fore.MAGENTA)
        self.log("STARTING ANDROID ONBOARDING", Fore.MAGENTA)
        self.log("=" * 60, Fore.MAGENTA)

        try:
            self.log("[1] Launching Replit...", Fore.CYAN)
            self.open_app(REPLIT_PACKAGE_NAME)

            self.log("[2] Welcome screen -> Continue", Fore.CYAN)
            self.tap_text_or_pct("Continue", 50, 90)

            self.log("[3] Selecting 'Continue with Email'", Fore.CYAN)
            self.tap_text_or_pct("Continue with Email", 50, 48)

            self.log(f"[4] Entering email: {email}", Fore.CYAN)
            if not self.tap_element(timeout=8, cls="android.widget.EditText"):
                self.tap_pct(50, 19)
            self.type_text(email)
            self.hide_keyboard()
            self.press_enter()
            time.sleep(2.0)

            self.log("[5] Entering password", Fore.CYAN)
            if not self.tap_element(timeout=8, cls="android.widget.EditText"):
                self.tap_pct(50, 25)
            self.type_text(password)
            self.hide_keyboard()
            self.tap_text_or_pct("Login", 50, 33)

            self.log("[6] Checking for 'Invalid username or password'...", Fore.CYAN)
            time.sleep(2.0)
            if self.find_element(text="Invalid username or password", partial=True):
                self.log("Error banner detected -> retrying Login", Fore.YELLOW)
                self.tap_text_or_pct("Login", 50, 33)

            self.log("[7] 'Welcome to Replit!' -> Continue", Fore.CYAN)
            self.tap_text_or_pct("Continue", 50, 90)

            self.log("[8] Entering first name", Fore.CYAN)
            if not self.tap_element(timeout=8, cls="android.widget.EditText"):
                self.tap_pct(50, 25)
            self.type_text(username.capitalize())
            self.hide_keyboard()
            self.tap_text_or_pct("Continue", 50, 44)

            self.log("[9] Username screen -> Continue", Fore.CYAN)
            self.tap_text_or_pct("Continue", 50, 50)

            self.log("[10] Selecting 'Developer'", Fore.CYAN)
            self.tap_text_or_pct("Developer", 18, 27)
            self.tap_text_or_pct("Continue", 50, 90)

            self.log("[11] Selecting 'Google search'", Fore.CYAN)
            self.tap_text_or_pct("Google search", 18, 27)
            self.tap_text_or_pct("Continue", 50, 90)

            self.log("[12] Skipping main interface prompt", Fore.CYAN)
            self.tap_text_or_pct("Skip", 88, 6)

            self.log("[13] Skipping subscription prompt", Fore.CYAN)
            self.tap_text_or_pct("Skip", 88, 6)

            self.log("[14] Opening profile menu", Fore.CYAN)
            self.tap_pct(90, 8)

            self.log("[15] Scrolling until 'Log Out' is visible", Fore.CYAN)
            for _ in range(4):
                if self.find_element(text="Log Out"):
                    break
                self.swipe_pct(50, 62, 50, 21)

            self.log("[16] Tapping 'Log Out'", Fore.CYAN)
            if not self.tap_element(timeout=5, text="Log Out"):
                self.tap_pct(50, 75)

            self.log("[17] Confirming logout dialog", Fore.CYAN)
            if not self.tap_element(timeout=5, text="LOG OUT"):
                self.tap_pct(69, 42)

            self.log("[18] Closing Replit app", Fore.CYAN)
            time.sleep(2.0)
            self.close_app(REPLIT_PACKAGE_NAME)

            self.log("\nANDROID ONBOARDING COMPLETE!", Fore.GREEN)
            return True

        except Exception as e:
            self.log(f"\nAndroid Automation Error: {e}", Fore.RED)
            self.save_failure_evidence("android_error")
            return False