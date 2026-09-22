#!/usr/bin/env bash
#
# Launch the PC automation with the Nix C++ runtime required by Playwright's
# Python greenlet extension. This does not bypass provider-side protections.

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LIBSTDCPP="$(gcc -print-file-name=libstdc++.so.6 2>/dev/null || true)"

if [[ -z "$LIBSTDCPP" || ! -f "$LIBSTDCPP" ]]; then
  printf 'ERROR: libstdc++.so.6 is not available from the configured GCC runtime.\n' >&2
  exit 1
fi

export LD_LIBRARY_PATH="$(dirname "$LIBSTDCPP")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

if [[ "${1:-}" == "--check-runtime" ]]; then
  cd "$ROOT"
  python - <<'PY'
import greenlet
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        executable_path="/repl/tools/bin/chromium",
        headless=True,
        args=["--no-sandbox"],
    )
    browser.close()

print("PC browser runtime is ready.")
PY
  exit 0
fi

exec python "$ROOT/main.py" "$@"