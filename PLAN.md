# Implementation Plan

## Status

This is the design and planning baseline for the onboarding-flow automation. The
Python files have not been refactored yet. `CORE_FLOW.md` is the source copy of
the flow supplied by the user.

## Decisions

1. **Keep Python + Playwright + ADB for the first implementation.** The current
   repository already has the right control points, and the screenshots show
   that the Android app exposes enough visible text for UI Automator dumps.
2. **Make UI state authoritative.** A coordinate is only a last-resort,
   explicitly logged manual fallback. A missing expected element must fail the
   step or offer manual takeover; it must never return success just because a
   fallback tap was issued.
3. **Follow the supplied flow, not the older API-first flow.** The desired PC
   flow starts in `temp-mail.org`, opens Replit in a separate tab, returns to
   the mailbox, opens the Replit message, and follows the visible verification
   controls.
4. **Do not build a Kotlin companion in phase one.** Revisit Kotlin
   `AccessibilityService` only if the target app's UI hierarchy cannot be
   reliably observed or clicked from ADB/UI Automator.
5. **Treat `CORE_FLOW.md` as the product contract.** The screenshot set is
   visual evidence for the Android states and should be consulted whenever a
   step is changed.

## Phase 0 — Normalize the repository

- Rename the timestamped source files to the canonical module names expected by
  their imports: `config.py`, `android_automation.py`, `pc_automation.py`, and
  `main.py`.
- Keep the duplicate `main_2` implementation outside the runtime path as an
  archived reference; do not let two orchestrators compete for the same state
  file.
- Place the runtime requirements at the root as `requirements.txt`.
- Add a small `--dry-run` or inspection mode that verifies imports and config
  without opening a browser or touching a device.
- Keep passwords and mailbox contents out of normal logs. Redact state values
  when printing checkpoints.

**Gate:** `python -m py_compile config.py android_automation.py
pc_automation.py main.py` succeeds and all cross-file imports resolve.

## Phase 1 — PC flow

Implement the supplied PC sequence as two browser pages in one Playwright
context:

1. Open `temp-mail.org`, wait for the address to leave its loading state, read
   the visible address, and click the Copy control.
2. Open `replit.com`, close the optional Google sign-in prompt, and reach the
   Create Account form.
3. Select Email, fill the copied address and configured password, and submit.
4. Detect a manual CAPTCHA or anti-bot challenge and pause for the operator.
5. Return to the temp-mail page, wait for the inbox, identify the Replit
   verification message by sender and subject, open it, and follow Verify Email
   then Verify Now.
6. Wait for an explicit verification success state. Do not treat a completed
   click or a timeout as proof of success.

Use Playwright locator assertions and URL/state checks instead of fixed sleeps.
The provider-specific selectors belong in a dedicated adapter so a future
provider can be added without changing the orchestrator.

**Gate:** A controlled test reaches each PC checkpoint, produces evidence on
failure, and leaves the browser open for manual takeover when needed.

## Phase 2 — Android UI state machine

Replace the current optimistic sequence with named transitions matching the
screenshots:

- launch and wait for the Replit welcome screen;
- Continue, then Continue with Email;
- email field, Continue, password field, Login;
- detect the red invalid-credentials banner and retry Login once;
- welcome Continue;
- name screen: enter first name and Continue;
- username screen: confirm the generated username and Continue;
- source screen: select Google search and Continue;
- role screen: select Developer and Continue;
- subscription/main interface: select visible Skip controls;
- open the profile menu and scroll until Log Out is visible;
- confirm Log Out in the dialog;
- force-stop Replit and verify that it is no longer the foreground package.

Every transition must:

- wait for a matching text, content description, resource ID, or screen
  signature;
- verify that the node is enabled and clickable before tapping;
- wait for the expected next state;
- save screenshot plus `ui.xml` on failure;
- return `False` when the state cannot be proven;
- expose a manual takeover option through the orchestrator.

**Gate:** Each Android state can be replayed from a clean device state and the
runner never reports completion while Replit is still foreground.

## Phase 3 — Checkpoint and recovery hardening

- Define checkpoint semantics as “next stage to run”, with a separate
  `in_progress` marker for crash diagnosis.
- Write `state.json` atomically using a temporary file and replace operation.
- Preserve the email, username, password reference, verification link, and
  current stage across resume.
- Do not recreate an account automatically after a crash in a later stage.
- Replace silent `[s]kip` success with an explicitly recorded manual decision.
- Add a bounded retry policy for network calls with backoff and response
  validation.
- Ensure browser, Playwright, and ADB-related resources are cleaned up in
  `finally` blocks.

**Gate:** Interruptions before and after each stage resume from the correct
  point without creating a second account.

## Phase 4 — PC resume and import

- Wait for the Android flow to finish and for server-side session sync.
- Reload the existing browser context.
- Confirm the logged-in dashboard or perform a controlled login.
- Import the operator's GitHub repository through the supported visible flow.
- Verify import progress with a real UI or URL state, not a fixed 15-second
  assumption.

**Gate:** A successful run ends with a confirmed dashboard/import state and a
  clean, redacted final summary.

## Phase 5 — Verification

Run these checks in order:

1. Static syntax and import check.
2. Mocked unit checks for state transitions, link extraction, response
   filtering, and atomic checkpoint writes.
3. PC flow smoke test with manual CAPTCHA handling.
4. Android smoke test on the operator's device, starting at the home screen.
5. Full end-to-end test with one account only.

The first end-to-end test should stop after account creation and email
verification unless the operator explicitly confirms that the Android device
is connected and ready.

## Open implementation questions

- Is `temp-mail.org` a hard requirement for the final implementation, or is it
  acceptable to keep a provider adapter with a separately selected API
  provider? The current code uses 1secmail, which does not implement the
  supplied visible flow.
- Should the GitHub repository URL be required in configuration, or should the
  final import step always prompt interactively?
- On the target device, does `uiautomator dump` expose the visible Replit text
  and clickable bounds on every screen? If not, test Appium/UiAutomator2 before
  introducing a Kotlin companion.