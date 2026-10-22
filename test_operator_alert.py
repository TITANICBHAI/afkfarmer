"""operator_alert: a sound and an ENTER poll, nothing else.

The module never touches a site or a challenge. These tests pin the two things
that matter on a real keyboard: it must never raise, and it must never block.
"""

import io
import os
import sys
import types
import unittest
from unittest.mock import patch

import operator_alert


def _quiet_env():
    """The environment as a person would have it: sound not switched off."""

    return patch.dict(os.environ, {}, clear=False)


class BeepTests(unittest.TestCase):
    def setUp(self):
        env = _quiet_env()
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(operator_alert.SILENT_ENV, None)
        self.stdout = io.StringIO()
        out = patch.object(sys, "stdout", self.stdout)
        out.start()
        self.addCleanup(out.stop)

    def fake_winsound(self, error=None):
        calls = []

        def beep(frequency, duration):
            calls.append((frequency, duration))
            if error is not None:
                raise error

        module = types.SimpleNamespace(Beep=beep)
        return module, calls

    def test_the_environment_switch_silences_it(self):
        with patch.dict(os.environ, {operator_alert.SILENT_ENV: "1"}):
            operator_alert.beep()

        self.assertEqual(self.stdout.getvalue(), "")

    def test_the_config_switch_silences_it(self):
        import config

        with patch.object(config, "ALERT_SOUND", False, create=True):
            operator_alert.beep()

        self.assertEqual(self.stdout.getvalue(), "")

    def test_a_config_without_the_setting_still_beeps(self):
        # An older config.py has no ALERT_SOUND: the default is on.
        import config

        had = hasattr(config, "ALERT_SOUND")
        saved = getattr(config, "ALERT_SOUND", None)
        if had:
            del config.ALERT_SOUND
        try:
            with patch.object(operator_alert, "_is_windows", return_value=False):
                operator_alert.beep()
        finally:
            if had:
                config.ALERT_SOUND = saved

        self.assertEqual(self.stdout.getvalue(), "\a")

    def test_other_systems_get_the_terminal_bell(self):
        with patch.object(operator_alert, "_is_windows", return_value=False):
            operator_alert.beep()

        self.assertEqual(self.stdout.getvalue(), "\a")

    def test_windows_gets_two_real_beeps(self):
        winsound, calls = self.fake_winsound()

        with patch.object(operator_alert, "_is_windows", return_value=True), patch.dict(
            sys.modules, {"winsound": winsound}
        ):
            operator_alert.beep()

        self.assertEqual(len(calls), 2)
        self.assertEqual(self.stdout.getvalue(), "")  # no bell on top of the beeps

    def test_windows_without_a_sound_device_falls_back_to_the_bell(self):
        winsound, calls = self.fake_winsound(error=RuntimeError("no sound device"))

        with patch.object(operator_alert, "_is_windows", return_value=True), patch.dict(
            sys.modules, {"winsound": winsound}
        ):
            operator_alert.beep()  # must not raise

        self.assertEqual(self.stdout.getvalue(), "\a")

    def test_a_closed_stdout_never_raises(self):
        class Closed:
            def write(self, _text):
                raise ValueError("I/O operation on closed file")

            def flush(self):
                raise ValueError("I/O operation on closed file")

        with patch.object(sys, "stdout", Closed()), patch.object(
            operator_alert, "_is_windows", return_value=False
        ):
            operator_alert.beep()


@unittest.skipIf(os.name == "nt", "the POSIX keyboard path needs a real file descriptor")
class PosixKeyboardTests(unittest.TestCase):
    """A pipe stands in for the terminal: select() and readline() work the same."""

    def setUp(self):
        read_fd, write_fd = os.pipe()
        self.reader = os.fdopen(read_fd, "r")
        self.writer = os.fdopen(write_fd, "w")
        self.addCleanup(self.reader.close)
        stdin = patch.object(sys, "stdin", self.reader)
        stdin.start()
        self.addCleanup(stdin.stop)
        windows = patch.object(operator_alert, "_is_windows", return_value=False)
        windows.start()
        self.addCleanup(windows.stop)

    def type_keys(self, text):
        self.writer.write(text)
        self.writer.flush()

    def close_writer(self):
        self.writer.close()

    def test_nothing_typed_means_not_pressed_and_it_never_waits(self):
        self.assertFalse(operator_alert.enter_pressed())

    def test_enter_is_seen_once(self):
        self.type_keys("\n")

        self.assertTrue(operator_alert.enter_pressed())
        self.assertFalse(operator_alert.enter_pressed())

    def test_other_text_with_enter_counts_and_is_consumed(self):
        self.type_keys("done\n")

        self.assertTrue(operator_alert.enter_pressed())
        self.assertFalse(operator_alert.enter_pressed())

    def test_end_of_input_is_not_an_enter(self):
        self.close_writer()

        self.assertFalse(operator_alert.enter_pressed())

    def test_a_pipe_is_not_a_terminal_so_discarding_does_nothing_and_never_raises(self):
        self.type_keys("\n")

        operator_alert.discard_pending_input()  # tcflush fails on a pipe: ignored

        self.assertTrue(operator_alert.enter_pressed())  # nothing was lost or raised


class WindowsKeyboardTests(unittest.TestCase):
    """msvcrt is replaced by a keyboard buffer so this runs on any system."""

    def keyboard(self, typed):
        keys = list(typed)

        module = types.SimpleNamespace(
            kbhit=lambda: bool(keys),
            getwch=lambda: keys.pop(0),
        )
        return module, keys

    def run_with(self, typed, function):
        module, keys = self.keyboard(typed)
        with patch.object(operator_alert, "_is_windows", return_value=True), patch.dict(
            sys.modules, {"msvcrt": module}
        ):
            return function(), keys

    def test_enter_among_other_keys_is_seen_and_the_buffer_is_drained(self):
        result, left = self.run_with("ab\rc", operator_alert.enter_pressed)

        self.assertTrue(result)
        self.assertEqual(left, [])

    def test_other_keys_alone_are_not_enter(self):
        result, left = self.run_with("abc", operator_alert.enter_pressed)

        self.assertFalse(result)
        self.assertEqual(left, [])

    def test_an_empty_buffer_is_not_enter(self):
        result, _left = self.run_with("", operator_alert.enter_pressed)

        self.assertFalse(result)

    def test_stale_keys_are_discarded(self):
        _result, left = self.run_with("\r\rx", operator_alert.discard_pending_input)

        self.assertEqual(left, [])

    def test_a_keyboard_that_cannot_be_polled_is_never_an_error(self):
        def broken_kbhit():
            raise OSError("no console")

        module = types.SimpleNamespace(kbhit=broken_kbhit, getwch=lambda: "")
        with patch.object(operator_alert, "_is_windows", return_value=True), patch.dict(
            sys.modules, {"msvcrt": module}
        ):
            self.assertFalse(operator_alert.enter_pressed())
            operator_alert.discard_pending_input()  # must not raise


if __name__ == "__main__":
    unittest.main()
