# Progress Tracker

## How to use this file

This tracker is mandatory for every agent working on this repository.

- Read it completely before editing.
- Work from the first relevant unchecked item.
- Tick `[x]` only after implementation and verification are both complete.
- Add evidence to completed items when possible.
- Leave blocked items unchecked and record the blocker.
- Update the status fields after every meaningful work session.
- Do not delete incomplete work or hide failed attempts.

## Current status

- **Current phase:** Phase 1 — PC temporary-mail flow
- **Overall verdict:** Not ready for a real end-to-end run
- **Next action:** Run an authorized live mailbox smoke test, then implement
  browser-based verification-message handling
- **Last updated:** 2026-09-22
- **Blockers:** No live browser run has been authorized or performed. The
  verification stage still refuses to use the legacy 1secmail API until its
  browser-mail flow is implemented.

## Phase 0 — Baseline and repository setup

- [x] Read the supplied PC and Android flow.
  - Evidence: `CORE_FLOW.md` is an exact copy of the supplied flow text.
- [x] Review all supplied Android screenshots.
  - Evidence: screenshots 7–24 are mapped in `IMPLEMENTATION.md`.
- [x] Create the planning, implementation, architecture, prompt, and README
  documents.
  - Evidence: `PLAN.md`, `IMPLEMENTATION.md`, `ARCHITECTURE.md`,
    `AUTOMATION_PROMPT.md`, and `README.md`.
- [x] Normalize the active Python filenames.
  - Evidence: `config.py`, `android_automation.py`, `pc_automation.py`, and
    `main.py` exist at the project root.
- [x] Confirm the active Python files compile.
  - Evidence: `python -m py_compile config.py android_automation.py
    pc_automation.py main.py`.
- [x] Move the supplied dependency list to the project root as
  `requirements.txt`.
  - Evidence: root `requirements.txt` matches the supplied three pinned
    dependencies.
- [x] Confirm required Python packages and external tools are available.
  - Evidence: `python preflight.py` passes source syntax, configuration,
    `playwright`, `requests`, `colorama`, and `adb` checks. `adb version`
    reports Android Debug Bridge 35.0.1.
- [x] Add runtime artifacts to ignore/protection rules.
  - Targets: `state.json`, `auth_state.json`, logs, screenshots, and `ui.xml`.
- [x] Add a non-destructive import/configuration smoke check.
  - Evidence: `preflight.py` passes syntax/configuration checks and exits
    without opening a browser or starting Android actions.

## Phase 1 — PC temporary-mail flow

- [x] Define a temporary-mail provider interface.
  - Evidence: `temp_mail.py` defines `TempMailProvider` and
    `TempMailOrgProvider`; syntax and preflight checks pass.
- [x] Implement the requested `temp-mail.org` browser flow.
  - Evidence: `TempMailOrgProvider.obtain_address()` opens the configured
    page, reads the address, clicks Copy, and requires proof of the copy.
    Live browser verification remains pending.
- [ ] Wait for the real mailbox address instead of accepting a loading state.
  - Implementation is present; live DOM verification is still pending.
- [ ] Click Copy and verify that the copied value matches the visible address.
  - Implementation is present; live clipboard/feedback verification is still
    pending.
- [x] Keep the mailbox page and Replit page in the same Playwright context.
  - Evidence: `PCAutomation` creates `TempMailOrgProvider` from its shared
    browser context.
- [x] Make the provider choice explicit; do not silently substitute 1secmail.
  - Evidence: `config.py` selects `temp-mail.org`; unsupported providers raise
    instead of falling back.
- [x] Add failure evidence and manual takeover for provider/UI changes.
  - Evidence: provider failures save a tagged screenshot; the existing stage
    recovery menu handles retry/manual takeover. Live behavior remains pending.

## Phase 2 — PC Replit registration and verification

- [ ] Open Replit and handle the optional Google sign-in popup.
- [ ] Select Email and fill the address and configured password.
- [ ] Detect validation errors before submitting.
- [ ] Detect CAPTCHA or anti-bot challenges and pause for manual resolution.
- [ ] Submit the account form and wait for an observed processing/result state.
- [ ] Find the Replit verification message by sender and subject.
- [ ] Open the message and follow Verify Email.
- [ ] Follow Verify Now when present.
- [ ] Confirm an explicit verification success state.
- [ ] Replace readiness sleeps with Playwright locator/state waits.
- [ ] Add focused tests for message filtering and verification-link validation.

## Phase 3 — Android login and onboarding

- [ ] Verify the intended ADB device before launching the app.
- [ ] Launch Replit and wait for the expected initial screen.
- [ ] Implement verified Continue and Continue with Email transitions.
- [ ] Handle both keyboard-visible and keyboard-hidden email states.
- [ ] Enter the email and verify the password screen appears.
- [ ] Enter the password and verify the login action state.
- [ ] Detect the invalid-credentials banner and retry Login at most once.
- [ ] Verify the Welcome screen and Continue transition.
- [ ] Verify the name screen, enter the first name, and continue.
- [ ] Verify the username screen and continue.
- [ ] Select Google Search and verify the next screen.
- [ ] Select Developer and verify the next screen.
- [ ] Handle the Skip controls using visible UI state.
- [ ] Open the profile menu and scroll until Log Out is visible.
- [ ] Open and verify the logout confirmation dialog.
- [ ] Confirm Log Out.
- [ ] Force-stop Replit and verify it is no longer foreground.
- [ ] Remove any fallback that reports success without a destination-state
  check.
- [ ] Save screenshot and UI XML evidence for every failed transition.

## Phase 4 — Checkpoint and recovery

- [ ] Define checkpoint semantics as “next stage to run”.
- [ ] Add an explicit in-progress marker for crash diagnosis.
- [ ] Write state atomically through a temporary file and replacement.
- [ ] Preserve the mailbox, username, verification link, and stage safely.
- [ ] Redact secrets from logs and normal final output.
- [ ] Make retry, manual takeover, skip, and quit outcomes distinct.
- [ ] Prevent later-stage resume from creating another account.
- [ ] Add recovery tests for interruption before and after every stage.

## Phase 5 — PC resume and GitHub import

- [ ] Wait for session synchronization after Android completion.
- [ ] Reload the browser context and verify the logged-in state.
- [ ] Log in only when required and verify the result.
- [ ] Require or safely collect the operator's GitHub repository URL.
- [ ] Submit the supported import flow.
- [ ] Verify import progress with a real UI or URL state.
- [ ] Treat a missing import control as failure/manual takeover, not success.

## Phase 6 — Validation

- [ ] Run Python syntax and import checks.
- [ ] Run unit checks for state transitions, link extraction, retries, and
  checkpoint recovery.
- [ ] Run a PC smoke test with manual CAPTCHA handling.
- [ ] Run an Android smoke test from the home screen on the operator's device.
- [ ] Run one complete end-to-end test only after the smoke tests pass.
- [ ] Confirm the final app-close and PC-resume postconditions.
- [ ] Record the final verdict with evidence.

## Architecture decisions

- [x] Use Python + Playwright + ADB/UI Automator for the first implementation.
- [x] Keep CAPTCHA and anti-bot handling manual.
- [x] Use UI state as the source of truth.
- [ ] Escalate to Appium/UiAutomator2 only if device evidence shows that ADB
  UI dumps are insufficient.
- [ ] Consider a Kotlin `AccessibilityService` only after the escalation
  criteria in `ARCHITECTURE.md` are met.

## Session log

Add one entry after each meaningful session:

```text
### YYYY-MM-DD — Agent/session name
- Completed:
- Evidence:
- Still open:
- Blockers:
- Next action:
```

### 2026-09-22 — Repository preparation
- Completed: moved `requirements.txt` to the root, added runtime ignore rules,
  and added `preflight.py`.
- Evidence: `python -m py_compile config.py android_automation.py
  pc_automation.py main.py preflight.py` passed; `python preflight.py`
  completed without launching browser or Android automation.
- Still open: dependency/tool availability and all implementation phases.
- Blockers: `playwright`, `requests`, `colorama`, and `adb` are missing.
- Next action: install/configure the local prerequisites, then implement the
  state-driven PC adapter.

### 2026-09-22 — Local prerequisites
- Completed: installed the managed Python toolchain, pinned Python
  dependencies, and `android-tools`.
- Evidence: `python preflight.py` passed; direct imports of `playwright`,
  `requests`, and `colorama` passed; `adb version` passed.
- Still open: all browser-flow, Android-flow, checkpoint, and validation items.
- Blockers: no real browser or device run has been authorized or performed;
  the current code still needs the state-driven refactor.
- Next action: define the temporary-mail provider interface and implement the
  requested `temp-mail.org` browser flow.

### 2026-09-22 — Temporary-mail provider
- Completed: added the browser-backed `temp_mail.py` provider, wired it into
  `PCAutomation` and `main.py`, made `temp-mail.org` explicit, and disabled the
  legacy API verification path for this provider.
- Evidence: `python -m py_compile ...` passed, `python -m unittest -v
  test_temp_mail.py` passed 2 tests, and `python preflight.py` passed.
- Still open: live verification of the address-loading and Copy selectors, then
  browser-based verification-message handling.
- Blockers: live external browser testing has not been authorized or performed.
- Next action: run an authorized mailbox smoke test, then implement the
  verification-message portion of the PC flow.

### 2026-09-22 — Provider hardening
- Completed: made address readiness inspect input values as well as visible
  body text, and added `temp_mail.py` to the preflight source set.
- Evidence: syntax compilation, 2 provider parsing tests, `python preflight.py`,
  and `git diff --check` all passed.
- Still open: live selector and clipboard verification, then browser-based
  verification-message handling.
- Blockers: no live external browser run has been authorized or performed.
- Next action: run the authorized mailbox smoke test when the operator is ready.