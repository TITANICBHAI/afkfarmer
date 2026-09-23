"""Non-destructive readiness check for the local automation repository.

This script never opens a browser, contacts a mailbox, starts ADB actions, or
launches the Android app. It only checks local files, syntax, dependencies, and
tool availability.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE_FILES = (
    "config.py",
    "email_provider.py",
    "email_providers.py",
    "pc_flow.py",
    "temp_mail.py",
    "android_automation.py",
    "pc_automation.py",
    "main.py",
)
REQUIRED_CONFIG = (
    "REPLIT_PASSWORD",
    "EMAIL_STRATEGY",
    "PRIMARY_EMAIL_API",
    "USER_CUSTOM_EMAIL",
    "REPLIT_PACKAGE_NAME",
    "ANDROID_DEVICE_ID",
    "EMAIL_CHECK_TIMEOUT",
    "ANDROID_STEP_DELAY",
    "PAGE_LOAD_TIMEOUT",
    "LOGIN_RETRY_DELAY",
    "ELEMENT_WAIT_TIMEOUT",
    "TEMP_MAIL_PROVIDER",
    "TEMP_MAIL_URL",
    "GITHUB_REPO_URL",
    "DEBUG_MODE",
)
REQUIRED_MODULES = ("patchright", "requests", "colorama")


def check_source_files() -> list[str]:
    problems: list[str] = []
    for name in SOURCE_FILES:
        path = ROOT / name
        if not path.is_file():
            problems.append(f"missing source file: {name}")
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            problems.append(f"syntax error in {name}: {exc.msg}")
    return problems


def check_config_contract() -> list[str]:
    path = ROOT / "config.py"
    if not path.is_file():
        return ["missing config.py"]
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"config.py cannot be parsed: {exc}"]

    assignments: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            assignments.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )
    return [
        f"config.py is missing expected key: {name}"
        for name in REQUIRED_CONFIG
        if name not in assignments
    ]


def check_dependencies() -> list[str]:
    return [
        f"missing Python dependency: {name}"
        for name in REQUIRED_MODULES
        if importlib.util.find_spec(name) is None
    ]


def check_external_tools() -> list[str]:
    problems = [] if shutil.which("adb") else ["missing external tool: adb"]
    configured_executable = os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH", "").strip()
    if configured_executable:
        if not Path(configured_executable).expanduser().is_file():
            problems.append(
                "configured browser executable does not exist: "
                f"{configured_executable}"
            )
    elif not os.environ.get("PLAYWRIGHT_CDP_URL", "").strip():
        browser_commands = (
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "microsoft-edge",
            "brave",
        )
        known_browser = any(shutil.which(command) for command in browser_commands)
        known_browser = known_browser or Path("/repl/tools/bin/chromium").is_file()
        if not known_browser:
            problems.append(
                "no Chromium-family browser executable found and "
                "PLAYWRIGHT_CDP_URL is not configured"
            )
    return problems


def check_adb_devices(require_device: bool = False) -> list[str]:
    """Query device readiness without launching an app or changing the device."""

    if not shutil.which("adb"):
        return []
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"could not query adb devices: {exc}"]
    if result.returncode != 0:
        return [f"adb devices failed: {result.stderr.strip() or 'unknown error'}"]
    authorized = [
        line
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith("List of devices")
        and line.split()[-1:] == ["device"]
    ]
    if require_device and len(authorized) != 1:
        return [
            "exactly one authorized Android device is required; "
            f"found {len(authorized)}"
        ]
    return []


def main() -> int:
    require_device = "--require-device" in sys.argv[1:]
    checks = (
        ("source and syntax", check_source_files()),
        ("configuration contract", check_config_contract()),
        ("Python dependencies", check_dependencies()),
        ("external tools", check_external_tools()),
        (
            "Android device readiness",
            check_adb_devices(require_device=require_device),
        ),
    )
    failed = False
    for label, problems in checks:
        if problems:
            failed = True
            print(f"[FAIL] {label}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"[PASS] {label}")

    if failed:
        print("\nRepository is not ready for a real automation run.")
        return 1
    print("\nRepository preflight passed. No browser or Android flow was started.")
    return 0


if __name__ == "__main__":
    sys.exit(main())