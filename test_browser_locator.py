"""Offline tests for browser discovery and the detached-launch helper."""

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import browser_locator as bl


class FindExecutableTests(unittest.TestCase):
    def test_windows_finds_edge_and_chrome_in_program_files(self):
        present = {
            r"C:\PF\Microsoft\Edge\Application\msedge.exe",
            r"C:\PF\Google\Chrome\Application\chrome.exe",
        }
        env = {"PROGRAMFILES": r"C:\PF", "PROGRAMFILES(X86)": r"C:\PF86", "LOCALAPPDATA": r"C:\L"}
        with patch.object(bl.os.path, "join", side_effect=lambda *p: "\\".join(p)):
            found = bl.find_browser_executables(
                env, windows=True, is_file=lambda path: path in present
            )
        self.assertEqual(found, sorted(present, key=lambda p: "chrome" in p))

    def test_posix_uses_path_lookup(self):
        which = lambda name: "/usr/bin/chromium" if name == "chromium" else None
        found = bl.find_browser_executables(
            {}, windows=False, which=which, is_file=lambda path: path == "/usr/bin/chromium"
        )
        self.assertEqual(found, ["/usr/bin/chromium"])

    def test_an_explicit_executable_is_the_only_choice(self):
        env = {"PLAYWRIGHT_EXECUTABLE_PATH": "/opt/mine/chrome"}
        found = bl.find_browser_executables(
            env, windows=False, which=lambda n: "/usr/bin/chromium", is_file=lambda p: True
        )
        self.assertEqual(found, ["/opt/mine/chrome"])
        missing = bl.find_browser_executables(
            env, windows=False, which=lambda n: "/usr/bin/chromium", is_file=lambda p: False
        )
        self.assertEqual(missing, [])  # never silently another browser


class ArgumentTests(unittest.TestCase):
    def test_arguments_carry_profile_port_and_unthrottling(self):
        args = bl.browser_arguments("/p", 9444, extra=["--headless=new"], sandbox=True)
        self.assertIn("--user-data-dir=/p", args)
        self.assertIn("--remote-debugging-port=9444", args)
        for flag in bl.UNTHROTTLED_FLAGS:
            self.assertIn(flag, args)
        self.assertIn("--headless=new", args)
        self.assertNotIn("--no-sandbox", args)
        self.assertEqual(args[-1], "about:blank")
        self.assertIn("--no-sandbox", bl.browser_arguments("/p", 1, sandbox=False))

    def test_the_child_is_detached_on_both_platforms(self):
        self.assertTrue(bl.detach_kwargs(windows=False)["start_new_session"])
        flags = bl.detach_kwargs(windows=True)["creationflags"]
        self.assertTrue(flags & 0x00000008 and flags & 0x00000200)
        self.assertNotIn("start_new_session", bl.detach_kwargs(windows=True))


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def clock(self):
        now = [0.0]
        return (lambda: now[0]), (lambda seconds: now.__setitem__(0, now[0] + seconds))

    def test_returns_once_the_debug_port_answers(self):
        monotonic, sleep = self.clock()
        process = Mock()
        process.poll.return_value = None
        answers = iter([False, False, True])
        popen = Mock(return_value=process)
        with patch.object(bl, "endpoint_alive", side_effect=lambda url: next(answers)):
            result = bl.launch_detached_browser(
                "/x/chrome", os.path.join(self.tmp.name, "prof"), 9555,
                popen=popen, sleep=sleep, monotonic=monotonic,
            )
        self.assertIs(result, process)
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "/x/chrome")
        self.assertTrue(os.path.isdir(os.path.join(self.tmp.name, "prof")))
        self.assertIn("start_new_session", popen.call_args.kwargs)

    def test_an_instant_exit_explains_the_profile_conflict(self):
        monotonic, sleep = self.clock()
        process = Mock()
        process.poll.return_value = 0
        with patch.object(bl, "endpoint_alive", return_value=False):
            with self.assertRaises(bl.BrowserLaunchError) as caught:
                bl.launch_detached_browser(
                    "/x/chrome", self.tmp.name, 9555,
                    popen=Mock(return_value=process), sleep=sleep, monotonic=monotonic,
                )
        self.assertIn("profile", str(caught.exception))

    def test_a_port_that_never_opens_times_out(self):
        monotonic, sleep = self.clock()
        process = Mock()
        process.poll.return_value = None
        with patch.object(bl, "endpoint_alive", return_value=False):
            with self.assertRaises(bl.BrowserLaunchError):
                bl.launch_detached_browser(
                    "/x/chrome", self.tmp.name, 9555, start_timeout=2,
                    popen=Mock(return_value=process), sleep=sleep, monotonic=monotonic,
                )

    def test_a_missing_executable_is_a_launch_error(self):
        with self.assertRaises(bl.BrowserLaunchError):
            bl.launch_detached_browser(
                "/x/chrome", self.tmp.name, 9555, popen=Mock(side_effect=OSError("nope"))
            )


if __name__ == "__main__":
    unittest.main()
