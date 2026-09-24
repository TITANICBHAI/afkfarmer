# Test Report

## Scope

This report covers the offline validation and consistency cleanup for the PC
browser, mailbox-provider, Android classification, checkpoint recovery, and
GitHub-sync layers. No live Replit account, mailbox, browser session, Android
device, CAPTCHA/security challenge, or external email-provider flow was
claimed as completed.

## Results

### PASS

- `python -m py_compile ...` passed for the application and focused test
  modules.
- `python preflight.py` passed in offline mode.
- Full offline suite passed: **56 tests**.
- `git diff --check` passed.
- Checkpoint persistence removes passwords and verification URLs from
  `state.json`.
- Completion state distinguishes automated success, manual takeover, skipped
  stages, and failed/interrupted runs.

### MOCK

- PC signup, authenticated-session sync, GitHub import, browser shutdown, and
  import-timeout behavior were exercised with mocked browser pages.
- Android UI state classification, ADB command construction, device parsing,
  force-stop verification, and text encoding were exercised with mocked
  device/UI data.
- Mailbox parsing, provider selection, URL validation, and verification
  success handling were exercised with mocked provider/page data.
- Preflight mode selection was exercised with mocked tool-check functions.

### SIMULATED

- Checkpoint resume from the Android stage through the configured post-Android
  stages was simulated.
- Retry, manual takeover, skip, and quit recovery paths were simulated.
- A closed verification page without observed success was simulated and
  correctly rejected.

### NOT RUN

- Live Replit signup, login, verification, and GitHub import.
- Live temp-mail.org or 1secmail mailbox access.
- Live Patchright browser interaction.
- Live Android onboarding or app launch.
- Live CAPTCHA, reCAPTCHA, suspicious-login, rate-limit, account-restriction,
  or device-integrity challenge handling.

### BLOCKED

- `python preflight.py --require-device` reports no authorized Android device:
  `adb devices` returned zero authorized devices.
- Live end-to-end validation remains blocked until the operator provides an
  authorized Android device, an approved browser session, and the required
  manual-intervention points.

## Commands

The complete suite was run with the GCC-provided C++ runtime library path
required by Patchright:

```bash
LD_LIBRARY_PATH=/nix/store/bmi5znnqk4kg2grkrhk6py0irc8phf6l-gcc-14.2.1.20250322-lib/lib \
  python -m unittest -v
```

The following checks were also run:

```bash
python preflight.py
python preflight.py --integration
python preflight.py --require-device
git diff --check
```

The strict device command is expected to return non-zero in the current
environment because no authorized device is connected.