"""Tell the person at the keyboard that the run is waiting for them.

Everything here only gets a human's attention or listens for their ENTER key.
Nothing in this module touches a website, solves a challenge or works around
one: a CAPTCHA or any other security challenge is always completed by a person.

Every function swallows its own errors. A missing sound card, a closed stdout
or an IDE console without a real keyboard must never stop a run.

Turn the sound off with ``ALERT_SOUND = False`` in config.py, or for one run
with the environment variable ``AFK_NO_SOUND=1`` (the test suite does this).
"""

import os
import sys

SILENT_ENV = "AFK_NO_SOUND"


def _is_windows() -> bool:
    return os.name == "nt"


def sound_enabled() -> bool:
    """False when AFK_NO_SOUND=1 or config.ALERT_SOUND is False (default: True)."""

    if os.environ.get(SILENT_ENV) == "1":
        return False
    try:
        import config

        return bool(getattr(config, "ALERT_SOUND", True))
    except Exception:
        return True


def beep() -> None:
    """Make a short sound: two beeps on Windows, the terminal bell elsewhere."""

    if not sound_enabled():
        return
    if _is_windows():
        try:
            import winsound

            winsound.Beep(880, 150)
            winsound.Beep(1175, 200)
            return
        except Exception:
            pass  # no sound device: fall back to the terminal bell
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:
        pass


def discard_pending_input() -> None:
    """Forget keys typed before now, so an old ENTER is not taken for a new one."""

    try:
        if _is_windows():
            import msvcrt

            while msvcrt.kbhit():
                msvcrt.getwch()
            return
        import termios

        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass  # not a terminal (pipe, IDE console): there is nothing to flush


def enter_pressed() -> bool:
    """True once the person has pressed ENTER since the last call. Never waits.

    Other keys typed meanwhile are read and dropped. Returns False wherever the
    keyboard cannot be polled (a pipe at end-of-input, an IDE console); the
    caller must not depend on this being able to say yes.
    """

    try:
        if _is_windows():
            import msvcrt

            pressed = False
            while msvcrt.kbhit():
                if msvcrt.getwch() in ("\r", "\n"):
                    pressed = True
            return pressed
        import select

        if not select.select([sys.stdin], [], [], 0)[0]:
            return False
        return sys.stdin.readline() != ""  # "" is end-of-input, not ENTER
    except Exception:
        return False
