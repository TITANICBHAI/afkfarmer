# Onboarding Flow Automation

Local workflow automation for the operator's own Replit onboarding flow across
a Chromium-family browser and an operator-owned Android device.

The flow is intentionally manual at CAPTCHA or anti-bot checkpoints. It is
not a bulk-account tool, a rate-limit bypass, or a credential-harvesting tool.

## Source of truth

- [`CORE_FLOW.md`](CORE_FLOW.md) — exact supplied PC and Android flow.
- [`PLAN.md`](PLAN.md) — staged implementation plan and open decisions.
- [`IMPLEMENTATION.md`](IMPLEMENTATION.md) — module contracts, state
  transitions, screenshot mapping, and acceptance criteria.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system design and Kotlin decision.
- [`AUTOMATION_PROMPT.md`](AUTOMATION_PROMPT.md) — implementation handoff
  prompt for a future coding pass.
- [`AGENT_START_PROMPT.md`](AGENT_START_PROMPT.md) — zero-context startup
  prompt for any new agent.
- [`PROGRESS_TRACKER.md`](PROGRESS_TRACKER.md) — mandatory checklist and
  session handoff record.

The visual references are stored in `attached_assets/`. The numbered Android
screenshots are especially important for the email-login, invalid-credentials,
onboarding, profile, logout, and app-close states. `VISUAL_FALLBACK.md` records
a deferred OCR/computer-vision/pixel evidence fallback; UI Automator remains
the current source of truth.

## Current state

The active Python files are present at the project root with canonical names:
`config.py`, `temp_mail.py`, `android_automation.py`, `pc_automation.py`, and
`main.py`.

The state-driven offline implementation is verified, including provider
selection and fallback, atomic checkpoints, PC registration classification,
Android UI-state transitions, safe text input, session synchronization after
Android completion, and safe GitHub-import failure handling. The combined
offline test suite passes 48 tests.

Live mailbox, Replit registration, Android-device, PC-resume, GitHub-import,
and complete end-to-end evidence remain open. The workspace browser previously
reached `temp-mail.org`, but the provider returned a Cloudflare block page
before an address was available. The automation does not bypass provider,
CAPTCHA, or anti-bot protections.

Every new agent must read `PROGRESS_TRACKER.md` before editing and tick items
only after completing and verifying them.

## Intended runtime

- Python 3.8+
- Patchright controlling the configured Chromium-family browser, otherwise
  workspace Chromium
- ADB in `PATH`
- An operator-owned Android device with USB debugging enabled
- `requests`
- `colorama`

Email selection is configured in `config.py`:

- `USER_CUSTOM_EMAIL` takes priority when non-empty.
- `EMAIL_STRATEGY = "api"` uses `PRIMARY_EMAIL_API`.
- `EMAIL_STRATEGY = "hybrid"` tries the API, then temp-mail.org if address
  acquisition fails.
- `EMAIL_STRATEGY = "temp-mail.org"` uses the browser mailbox directly.

Install the supplied requirements only after reviewing them and the provider
choice in `PLAN.md`.

## Verification

Initial static check:

```bash
python -m py_compile config.py android_automation.py pc_automation.py main.py
adb devices
```

Do not run a real end-to-end flow until the operator confirms the connected
device, the browser session, and the manual-intervention points.

For a non-destructive local readiness check, run:

```bash
python preflight.py
```

This checks source syntax, configuration keys, Python dependencies, and `adb`.
It does not open a browser, contact the mailbox, or launch the Android app.

The provider parsing checks can be run locally with:

```bash
python -m unittest -v test_temp_mail.py
```

For the PC browser flow in this Replit workspace, use the runtime wrapper so
Patchright can load the Nix C++ library:

```bash
bash run_pc_automation.sh --check-runtime
bash run_pc_automation.sh
```

The wrapper uses the configured browser or workspace Chromium. Provider-side
Cloudflare blocks are not bypassed.

## Windows 10 usage

The Python automation can be run from Windows 10, but the Linux shell wrappers
are not Windows commands:

- `run_pc_automation.sh` is for the Replit/Linux environment.
- `github_push.sh` requires Git Bash or WSL on Windows and is separate from the
  main onboarding flow.

Use **Command Prompt** or PowerShell from the project directory. The commands
below use Command Prompt syntax.

### 1. Check Python

Use Python 3.9 or newer; Python 3.11 is recommended:

```cmd
python --version
```

If `python` is not available, use `py` in the commands below instead.

### 2. Install Python dependencies

Run this once:

```cmd
python -m pip install -r requirements.txt
```

### 3. Configure the browser

The automation first tries to attach to an already-open Chromium-family
browser. It uses `PLAYWRIGHT_CDP_URL` when set; otherwise it checks common local
CDP ports (`9222` through `9225`) automatically. This reuses the existing
browser context and tabs, and the automation will not close that browser when
it finishes. A regular browser process cannot be attached to after launch, so
the browser must have been started with remote debugging enabled. For example,
on Windows:

```cmd
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222
set "PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222"
```

The same approach works with Chrome, Chromium, or another Chromium-family
browser. Keep the `temp-mail.org` and `replit.com` tabs open if you want those
exact tabs reused. If the browser uses another debugging port, either set it
explicitly:

```cmd
set "PLAYWRIGHT_CDP_PORTS=9333"
```

or set the complete endpoint with `PLAYWRIGHT_CDP_URL`.

#### Executable path versus existing-browser attachment

These settings have different purposes:

- `PLAYWRIGHT_EXECUTABLE_PATH` tells Patchright which browser executable to
  launch. It does **not** attach to an already-running browser.
- `PLAYWRIGHT_CDP_URL` tells Patchright to attach to the existing browser
  process, preserving its context and open tabs.
- When `PLAYWRIGHT_CDP_URL` is omitted, the script automatically probes local
  CDP ports `9222` through `9225`.

If the log only says:

```text
Using detected browser executable: C:\...\msedge.exe
```

or:

```text
Using configured browser executable: C:\...\msedge.exe
```

the script is launching its own isolated browser context. A successful
existing-browser attachment must show:

```text
Detected a browser remote-debugging endpoint at http://127.0.0.1:9222.
Attached to the existing browser at http://127.0.0.1:9222; reusing its open tabs.
```

To check whether a Windows browser is exposing CDP, use one of these commands
from the same machine:

```powershell
Invoke-RestMethod http://127.0.0.1:9222/json/version
```

```cmd
curl http://127.0.0.1:9222/json/version
```

If the check cannot connect, the existing browser was probably started without
remote debugging. Close the normal browser process once, start it with
debugging enabled, and then run the automation:

```cmd
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222
set "PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222"
python main.py
```

Opening the Replit and `temp-mail.org` tabs in that browser lets the script
reuse those exact tabs. A normal browser process cannot be retrofitted with
remote debugging after it has started.

If `PLAYWRIGHT_CDP_URL` is not set or the endpoint is unavailable, the script
automatically looks for installed Edge, Chrome, Brave, and Chromium
executables. It does not force Microsoft Edge. You can still prioritize a
specific executable:

```cmd
set "PLAYWRIGHT_EXECUTABLE_PATH=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
```

If Edge is installed under `C:\Program Files`, use:

```cmd
set "PLAYWRIGHT_EXECUTABLE_PATH=C:\Program Files\Microsoft\Edge\Application\msedge.exe"
```

Or configure a Patchright browser channel such as `chrome`, `msedge`, or
`chromium`:

```cmd
set "PLAYWRIGHT_BROWSER_CHANNEL=chrome"
```

If no existing browser can be controlled and no installed executable can be
launched, the script makes one final attempt to use Patchright's bundled
Chromium.

Check the configured path:

```cmd
if exist "%PLAYWRIGHT_EXECUTABLE_PATH%" (echo Edge found) else (echo Edge NOT found)
```

The `set` command applies only to the current Command Prompt window. To save
the value for future terminals:

```cmd
setx PLAYWRIGHT_EXECUTABLE_PATH "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
```

After using `setx`, close and reopen Command Prompt.

PowerShell uses different syntax. If you are in PowerShell, use:

```powershell
$env:PLAYWRIGHT_EXECUTABLE_PATH = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
```

Do not use the `$env:` form in Command Prompt. A prompt such as
`C:\Users\YourName\project>` indicates Command Prompt.

### 4. Add ADB to PATH

Android Platform Tools must be installed and the folder containing `adb.exe`
must be on PATH. A typical Android SDK path is:

```text
C:\Users\<your-user>\Documents\Android\SDK\platform-tools
```

For the current Command Prompt window:

```cmd
set "PATH=C:\Users\<your-user>\Documents\Android\SDK\platform-tools;%PATH%"
```

Replace `<your-user>` with the Windows account name. Then verify:

```cmd
where adb
adb version
adb devices
```

The phone should appear in the `adb devices` output. If it shows
`unauthorized`, unlock the phone and accept the USB debugging authorization
prompt.

To add the folder permanently, use:

1. Press `Win + R`.
2. Enter `sysdm.cpl`.
3. Open **Advanced** → **Environment Variables**.
4. Under the user variables, select `Path` → **Edit** → **New**.
5. Add the folder containing `adb.exe`.
6. Confirm all dialogs and open a new terminal.

### 5. Review configuration

Open `config.py` and review:

```python
REPLIT_PASSWORD = "12345678"
ANDROID_DEVICE_ID = None
GITHUB_REPO_URL = ""
```

Change the sample password before a real run. Leave
`ANDROID_DEVICE_ID = None` when one Android device is connected. If multiple
devices are connected, set it to the serial returned by `adb devices`.

Leave `GITHUB_REPO_URL` empty to be prompted during the run, or set it to the
operator's repository URL.

### 6. Run the safe preflight check

This checks source syntax, configuration, Python dependencies, and ADB without
opening the browser or launching the Android app:

```cmd
python preflight.py
```

### 7. Start the automation

Keep the browser and ADB environment variables configured in the same terminal,
then run:

```cmd
python main.py
```

The process opens a visible browser, obtains the mailbox address, opens the
Replit registration flow, pauses for manual CAPTCHA handling when necessary,
verifies the mailbox message, controls the Replit Android app, resumes the PC
session, and attempts the visible GitHub import flow.

There is no separate desktop GUI. The user interface is the visible browser
window, the real Android app, and the interactive terminal prompts.

### 8. Recovery and terminal choices

When a stage fails, the terminal offers:

```text
[r]etry / [m]anual takeover / [s]kip / [q]uit
```

- `r` retries the failed stage.
- `m` records that the operator completed the step manually and advances.
- `s` records an intentional skip and advances; use sparingly.
- `q` stops without advancing the failed stage.

Press `Ctrl+C` to stop while preserving the checkpoint. Rerun:

```cmd
python main.py
```

and choose to resume when prompted. The checkpoint means the next stage to
run, so a later-stage resume does not recreate the account.

To deliberately start a fresh run, remove the saved state and browser session:

```cmd
del state.json 2>nul
del auth_state.json 2>nul
```

Only do this intentionally; it removes the ability to resume the saved run.

### Windows safety and readiness

Before a real run, confirm that the configured browser is ready, `adb devices` shows the
operator-owned unlocked phone, and manual CAPTCHA handling is available. Do not
run the Android or external-account flow without explicit operator approval.

The current code is ready for local setup and offline checks, but the complete
live flow is not yet proven. Provider-side Cloudflare blocks, UI changes, login
errors, device-specific UI hierarchy differences, and delayed verification
mail remain possible failure points. The automation must stop and record
evidence instead of bypassing those protections.

## Runtime artifacts

The eventual implementation may produce:

- `state.json`
- `auth_state.json`
- `android_actions.log`
- `ui.xml`
- screenshots under `screenshots/`

These may contain account or session data and should remain local and out of
source control.
