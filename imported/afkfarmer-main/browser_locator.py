"""Find, start and reach the browser window the PC automation works in.

Standard library only, so ``preflight.py`` can use it without Patchright.

Why the automation starts its own *detached* browser instead of letting
Playwright launch one:

* A browser that Playwright launches is a child of the script. It runs in a
  throw-away context and is closed when the script ends, so the account that was
  just created and its login vanish with it.
* A browser started here is an ordinary operating-system process with its own
  persistent profile folder. It keeps running when the script exits, keeps its
  cookies, and is attached to over the Chrome DevTools Protocol only while the
  script needs it.

What this cannot do: drive the browser profile you use every day. Since Chrome
136 the ``--remote-debugging-port`` switch is ignored for the default Chrome
profile (https://developer.chrome.com/blog/remote-debugging-port), so the
automation uses a separate profile folder. That is also why the account is kept
between runs. (Whether Edge applies the same rule was not verified.)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, List, Mapping, Optional, Sequence
from urllib.request import urlopen

DEFAULT_PROFILE_DIR = "browser_profile"
DEFAULT_DEBUG_PORT = 9333
START_TIMEOUT_S = 25.0

# Chromium throttles background and covered windows hard: timers slow down and a
# window hidden behind another one stops painting, which stalls every action
# that waits for a frame. The automation window is often behind the person's
# other windows, so these switches keep it running at full speed.
UNTHROTTLED_FLAGS = (
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
)
QUIET_FLAGS = ("--no-first-run", "--no-default-browser-check")

POSIX_COMMANDS = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "brave",
)
WINDOWS_RELATIVE_PATHS = (
    ("Microsoft", "Edge", "Application", "msedge.exe"),
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
    ("Chromium", "Application", "chromium.exe"),
)
MAC_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


class BrowserLaunchError(RuntimeError):
    """The detached browser could not be started or did not open its debug port."""


def find_browser_executables(
    environ: Optional[Mapping[str, str]] = None,
    *,
    windows: Optional[bool] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
    is_file: Callable[[str], bool] = os.path.isfile,
) -> List[str]:
    """Chromium-family browsers installed here, best first, without assuming Edge.

    ``PLAYWRIGHT_EXECUTABLE_PATH`` is an explicit choice and is the only entry
    returned when it is set and exists.
    """

    env = os.environ if environ is None else environ
    windows = (os.name == "nt") if windows is None else windows
    found: List[str] = []

    def add(path: Optional[str]) -> None:
        if not path:
            return
        path = os.path.expandvars(os.path.expanduser(path))
        if path not in found and is_file(path):
            found.append(path)

    configured = (env.get("PLAYWRIGHT_EXECUTABLE_PATH") or "").strip()
    if configured:
        add(configured)
        # An operator choice: never silently substitute another browser.
        return found

    if windows:
        program_files = env.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local_app_data = env.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
        for root in (program_files, program_files_x86, local_app_data):
            for parts in WINDOWS_RELATIVE_PATHS:
                add(os.path.join(root, *parts))
    else:
        for command in POSIX_COMMANDS:
            add(which(command))
        for path in MAC_APPS:
            add(path)
        add("/repl/tools/bin/chromium")
    return found


def endpoint_alive(url: str, timeout: float = 0.5) -> bool:
    """Whether a Chromium DevTools endpoint answers at ``url``."""

    try:
        with urlopen(f"{url.rstrip('/')}/json/version", timeout=timeout) as response:
            body = response.read(8192)
        return b"webSocketDebuggerUrl" in body
    except Exception:
        return False


def endpoint_version(url: str, timeout: float = 0.5) -> Optional[dict]:
    try:
        with urlopen(f"{url.rstrip('/')}/json/version", timeout=timeout) as response:
            return json.loads(response.read(8192))
    except Exception:
        return None


def browser_arguments(
    profile_dir: str,
    port: int,
    *,
    extra: Iterable[str] = (),
    sandbox: Optional[bool] = None,
) -> List[str]:
    """Command-line switches for the detached browser (the executable is not included)."""

    if sandbox is None:
        sandbox = os.name == "nt"
    args = [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        *QUIET_FLAGS,
        *UNTHROTTLED_FLAGS,
    ]
    if not sandbox:
        args.append("--no-sandbox")
    args.extend(extra)
    args.append("about:blank")
    return args


def detach_kwargs(windows: Optional[bool] = None) -> dict:
    """Popen options that make the child independent of this script."""

    windows = (os.name == "nt") if windows is None else windows
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if windows:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no shared console, and a
        # Ctrl+C in this terminal is not delivered to the browser.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    return kwargs


def launch_detached_browser(
    executable: str,
    profile_dir: str,
    port: int = DEFAULT_DEBUG_PORT,
    *,
    extra_args: Sequence[str] = (),
    start_timeout: float = START_TIMEOUT_S,
    popen: Callable[..., "subprocess.Popen"] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> "subprocess.Popen":
    """Start ``executable`` with its own profile and a DevTools port, and wait for the port.

    The child is detached from this process. Raises ``BrowserLaunchError`` when
    the process exits at once (typically: the profile folder is already open in
    another browser, which then hands over and quits) or never opens the port.
    """

    profile = str(Path(profile_dir).expanduser().resolve())
    os.makedirs(profile, exist_ok=True)
    command = [executable, *browser_arguments(profile, port, extra=extra_args)]
    try:
        process = popen(command, **detach_kwargs())
    except OSError as exc:
        raise BrowserLaunchError(f"could not start {executable}: {exc}") from exc

    url = f"http://127.0.0.1:{port}"
    deadline = monotonic() + start_timeout
    while True:
        if endpoint_alive(url):
            return process
        exit_code = process.poll()
        if exit_code is not None:
            raise BrowserLaunchError(
                f"{os.path.basename(executable)} exited with code {exit_code} before "
                f"opening its debug port {port}. Another browser may already be "
                f"using the profile folder {profile}; close it, or pick another "
                "folder with BROWSER_PROFILE_DIR."
            )
        if monotonic() >= deadline:
            raise BrowserLaunchError(
                f"{os.path.basename(executable)} did not open its debug port {port} "
                f"within {start_timeout:.0f}s."
            )
        sleep(0.25)
