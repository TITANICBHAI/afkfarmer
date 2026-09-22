# Onboarding Flow Automation

Local workflow automation for the operator's own Replit onboarding flow across
Microsoft Edge and an operator-owned Android device.

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

The visual references are stored in `attached_assets/`. The numbered Android
screenshots are especially important for the email-login, invalid-credentials,
onboarding, profile, logout, and app-close states.

## Current state

The uploaded Python files are present at the project root with timestamped
names. Their imports currently expect canonical names such as `config.py` and
`android_automation.py`; filename normalization is the first implementation
step and has not been applied yet.

The project is documentation-first at this point. The automation code has not
been refactored or executed as part of this planning pass.

## Intended runtime

- Python 3.8+
- Playwright controlling Microsoft Edge
- ADB in `PATH`
- An operator-owned Android device with USB debugging enabled
- `requests`
- `colorama`

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

## Runtime artifacts

The eventual implementation may produce:

- `state.json`
- `auth_state.json`
- `android_actions.log`
- `ui.xml`
- screenshots under `screenshots/`

These may contain account or session data and should remain local and out of
source control.