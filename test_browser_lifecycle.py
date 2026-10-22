"""Real-Chromium proof of the browser lifecycle the operator asked for.

* the window is NOT closed when the script ends (not even when the whole Python
  process exits),
* the next run attaches to that same window and its tabs,
* the Replit login survives a browser restart (persistent profile folder).

A local web server stands in for the site and sets a persistent and a session
cookie. Headless mode is used only because the test machine has no display.
Skipped when patchright or a browser is missing, or AFK_SKIP_BROWSER_TESTS=1.
"""

import contextlib
import http.server
import io
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import browser_locator

SKIP = os.environ.get("AFK_SKIP_BROWSER_TESTS") == "1"
try:
    from patchright.sync_api import sync_playwright  # noqa: F401
    from pc_automation import PCAutomation
except Exception:  # pragma: no cover - depends on the machine
    PCAutomation = None


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class CookieSite(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Set-Cookie", "sid=kept; Max-Age=86400; Path=/")
        self.send_header("Set-Cookie", "tmp=session; Path=/")
        self.end_headers()
        self.wfile.write(b"<html><body>personal workspace</body></html>")

    def log_message(self, *args):
        pass


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie still answers signal 0
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().split(") ")[-1].split()[0] != "Z"
    except OSError:
        return True


@unittest.skipIf(SKIP or PCAutomation is None or os.name == "nt", "needs patchright and POSIX")
class BrowserLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.port = free_port()
        self.profile = os.path.join(self.tmp.name, "profile")
        self.env = {
            "AFK_BROWSER_PROFILE_DIR": self.profile,
            "AFK_BROWSER_DEBUG_PORT": str(self.port),
            "AFK_BROWSER_EXTRA_ARGS": "--headless=new",
            "AFK_BROWSER_MODE": "persistent",
        }
        if not browser_locator.find_browser_executables():
            self.skipTest("no Chromium-family browser installed")
        self.site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CookieSite)
        threading.Thread(target=self.site.serve_forever, daemon=True).start()
        self.addCleanup(self.site.shutdown)
        self.url = f"http://127.0.0.1:{self.site.server_address[1]}/"
        self.pids = []
        self.addCleanup(self.kill_browsers)
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self._old_cwd)

    def kill_browsers(self):
        # Chromium's processes are in the session the launcher created.
        for pid in self.pids:
            with contextlib.suppress(OSError):
                os.killpg(pid, signal.SIGTERM)
        deadline = time.time() + 5
        while time.time() < deadline and any(alive(pid) for pid in self.pids):
            time.sleep(0.1)
        for pid in self.pids:
            with contextlib.suppress(OSError):
                os.killpg(pid, signal.SIGKILL)

    def start(self):
        pc = PCAutomation("a@b.test", "pw")
        with patch.dict(os.environ, self.env), contextlib.redirect_stdout(io.StringIO()):
            pc.setup_browser()
        if pc.browser_process is not None:
            self.pids.append(pc.browser_process.pid)
        self.addCleanup(self.stop_playwright, pc)
        return pc

    @staticmethod
    def stop_playwright(pc):
        if pc.playwright is not None:
            with contextlib.suppress(Exception):
                pc.playwright.stop()

    def finish(self, pc):
        with patch.dict(os.environ, self.env), contextlib.redirect_stdout(io.StringIO()):
            pc.close_browser()

    def cookies(self, pc):
        return {cookie["name"] for cookie in pc.context.cookies()}

    def test_window_outlives_the_script_and_the_next_run_reuses_it(self):
        first = self.start()
        self.assertTrue(first._persistent and first.keeps_browser_open)
        first.page.goto(self.url)
        process = first.browser_process
        self.finish(first)

        # Closing the automation did not close the window.
        self.assertIsNone(process.poll(), "the browser was closed with the script")
        self.assertTrue(browser_locator.endpoint_alive(f"http://127.0.0.1:{self.port}"))

        second = self.start()
        self.assertIsNone(second.browser_process, "a second window was opened")
        self.assertTrue(second._persistent)
        self.assertIn("personal workspace", second.page.locator("body").inner_text())
        self.assertEqual(self.cookies(second), {"sid", "tmp"})
        self.finish(second)
        self.assertIsNone(process.poll())

    def test_the_login_cookie_survives_a_browser_restart(self):
        first = self.start()
        first.page.goto(self.url)
        self.assertIn("sid", self.cookies(first))
        process = first.browser_process
        # The person closes the window: a graceful browser shutdown, which is
        # also when Chromium writes its cookies to the profile folder.
        first.browser.new_browser_cdp_session().send("Browser.close")
        with contextlib.suppress(Exception):
            process.wait(timeout=15)
        self.finish(first)
        deadline = time.time() + 10
        while browser_locator.endpoint_alive(f"http://127.0.0.1:{self.port}"):
            self.assertLess(time.time(), deadline, "the browser did not stop")
            time.sleep(0.2)

        third = self.start()
        self.assertIsNotNone(third.browser_process, "a fresh window was expected")
        self.assertIn("sid", self.cookies(third))   # persistent cookie: still logged in
        self.finish(third)

    def test_the_window_survives_the_whole_python_process_ending(self):
        code = (
            "import os, sys\n"
            "from pc_automation import PCAutomation\n"
            "pc = PCAutomation('a@b.test', 'pw')\n"
            "pc.setup_browser()\n"
            "print('PID:', pc.browser_process.pid, flush=True)\n"
            "pc.page.goto(sys.argv[1])\n"
            "pc.close_browser()\n"
        )
        env = dict(os.environ, **self.env)
        env["PYTHONPATH"] = self._old_cwd
        result = subprocess.run(
            [sys.executable, "-c", code, self.url],
            capture_output=True, text=True, env=env, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        pid = int(next(
            line.split()[-1] for line in result.stdout.splitlines() if "PID:" in line
        ))
        self.pids.append(pid)
        time.sleep(1.0)
        self.assertTrue(alive(pid), "the browser died with the script's process")
        self.assertTrue(browser_locator.endpoint_alive(f"http://127.0.0.1:{self.port}"))


if __name__ == "__main__":
    unittest.main()
