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
- [`AGENT_START_PROMPT.md`](AGENT_START_PROMPT.md) — zero-context startup
  prompt for any new agent.
- [`PROGRESS_TRACKER.md`](PROGRESS_TRACKER.md) — mandatory checklist and
  session handoff record.

The visual references are stored in `attached_assets/`. The numbered Android
screenshots are especially important for the email-login, invalid-credentials,
onboarding, profile, logout, and app-close states.

## Current state

The active Python files are now present at the project root with canonical
names: `config.py`, `android_automation.py`, `pc_automation.py`, and `main.py`.
The code behavior still needs the state-driven refactor described in the
implementation documents.

The first PC provider implementation is now in `temp_mail.py`. It is wired to
`PCAutomation` and requires proof that the visible mailbox address was copied.
Live selector verification is still pending because no external browser run has
been started.

The repository setup is complete, but the automation code has not been
refactored or executed against a real browser or Android device. The pinned
dependencies are now available at the root as `requirements.txt`.

Every new agent must read `PROGRESS_TRACKER.md` before editing and tick items
only after completing and verifying them.

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

## Runtime artifacts

The eventual implementation may produce:

- `state.json`
- `auth_state.json`
- `android_actions.log`
- `ui.xml`
- screenshots under `screenshots/`

These may contain account or session data and should remain local and out of
source control.