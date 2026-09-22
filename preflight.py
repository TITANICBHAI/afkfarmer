"""Non-destructive readiness check for the local automation repository.

This script never opens a browser, contacts a mailbox, starts ADB actions, or
launches the Android app. It only checks local files, syntax, dependencies, and
tool availability.
"""

from __future__ import annotations

import ast
import importlib.util
import py_compile
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE_FILES = (
    "config.py",
    "pc_flow.py",
    "temp_mail.py",
    "android_automation.py",
    "pc_automation.py",
    "main.py",
)
REQUIRED_CONFIG = (
    "REPLIT_PASSWORD",
    "REPLIT_PACKAGE_NAME",
    "ANDROID_DEVICE_ID",
    "EMAIL_CHECK_TIMEOUT",
    "ANDROID_STEP_DELAY",
    "PAGE_LOAD_TIMEOUT",
    "LOGIN_RETRY_DELAY",
    "ELEMENT_WAIT_TIMEOUT",
    "TEMP_MAIL_PROVIDER",
    "GITHUB_REPO_URL",
    "DEBUG_MODE",
)
REQUIRED_MODULES = ("playwright", "requests", "colorama")


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
    return [] if shutil.which("adb") else ["missing external tool: adb"]


def main() -> int:
    checks = (
        ("source and syntax", check_source_files()),
        ("configuration contract", check_config_contract()),
        ("Python dependencies", check_dependencies()),
        ("external tools", check_external_tools()),
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