# Prompts for the Next Agents

Each prompt below assumes the agent is joining this repository without
conversation context. Give the agent one prompt at a time.

## 1. Complete visible mailbox verification

```text
Read AGENT_START_PROMPT.md, README.md, PROGRESS_TRACKER.md, CORE_FLOW.md,
PLAN.md, IMPLEMENTATION.md, ARCHITECTURE.md, and AUTOMATION_PROMPT.md before
editing. Inspect the relevant mailbox screenshots in attached_assets/.

Continue Phase 2 only. Implement browser-based verification in the existing
Playwright context:

1. Return to the temp-mail.org page.
2. Wait for the inbox to leave its loading state.
3. Find the Replit message by sender "Replit <verify@replit.com>" and subject
   "Replit verify email"; do not open the first arbitrary message.
4. Open the message and wait for its body.
5. Click Verify Email, validate the destination URL as an expected Replit
   verification URL, and click Verify Now when present.
6. Wait for an explicit "Verifying email" success/tick or verified dashboard
   state.

Do not use the legacy 1secmail API as a fallback. Do not solve CAPTCHA or
anti-bot challenges. Save failure evidence, keep manual takeover explicit,
update PROGRESS_TRACKER.md during the work, and run local tests without starting
a real external flow unless the operator explicitly authorizes it.
```

## 2. Harden the Android state machine

```text
Read AGENT_START_PROMPT.md and PROGRESS_TRACKER.md completely before editing,
then read CORE_FLOW.md, IMPLEMENTATION.md, ARCHITECTURE.md, and the supplied
Android screenshots.

Implement only Phase 3. Replace optimistic coordinate-driven success paths in
android_automation.py with named transitions based on UI Automator text,
content descriptions, resource IDs, and live node bounds. Each transition must
verify its destination state. Handle both keyboard-visible and keyboard-hidden
email states, the one allowed invalid-credentials retry, onboarding choices,
logout confirmation, force-stop, and foreground-package verification.

A missing node, malformed UI dump, failed tap, or missing postcondition is a
failure. Save screenshot and UI XML evidence for every failed transition.
Never run against a real phone without explicit operator authorization. Update
the tracker with evidence and run local checks first.
```

## 3. Implement checkpoint and recovery safety

```text
Read AGENT_START_PROMPT.md and PROGRESS_TRACKER.md completely before editing,
then inspect main.py, config.py, IMPLEMENTATION.md, and ARCHITECTURE.md.

Implement only Phase 4. Define state["stage"] as the next stage to run, add a
separate in-progress marker, write state atomically through a temporary file
and replacement, redact passwords/session data/complete verification URLs from
normal logs, and distinguish retry, manual takeover, skip, quit, and failure.

Add recovery tests for interruption before and after each stage. Ensure a
later-stage resume cannot create another account. Do not run any real browser,
mailbox, or Android action. Update PROGRESS_TRACKER.md with exact test evidence.
```

## 4. Run the validation phase

```text
Read AGENT_START_PROMPT.md and PROGRESS_TRACKER.md completely before editing.
Do not claim end-to-end success from compilation alone.

Run the local syntax/import checks and all unit checks first. Review every
unchecked item in PROGRESS_TRACKER.md. Only after the operator explicitly
confirms the browser, manual CAPTCHA handling, and operator-owned Android
device are ready should you run the authorized PC smoke test, Android smoke
test, and one-account end-to-end test in that order.

Record observed postconditions, screenshots/logs for failures, remaining
blockers, and the final verdict in PROGRESS_TRACKER.md. Never solve CAPTCHAs,
bypass rate limits, print passwords, or report success without observing the
destination state.
```