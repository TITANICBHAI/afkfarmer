# Kotlin Agent Prompts

Use one prompt at a time. Each prompt assumes the agent has no conversation
context. The prompts are intentionally scoped so that a future agent does not
rewrite the PC flow, start a real device run too early, or treat an unverified
UI action as success.

## 1. Establish Kotlin scope and scaffold

```text
Read AGENT_START_PROMPT.md, PROGRESS_TRACKER.md, CORE_FLOW.md, STEPS.md,
ARCHITECTURE.md, IMPLEMENTATION.md, KOTLIN_CONTEXT.md,
KOTLIN_ARCHITECTURE.md, KOTLIN_PLAN.md, and KOTLIN_STEPS.md before editing.
Inspect the current Python Android implementation, but do not replace it.

Create the smallest installable Kotlin Android project for the Android
controller. The first scope is the Android portion after the PC flow has
completed email verification. Keep the Python PC flow and Python Android path
available as the reference and rollback path.

Add an operator UI that explains accessibility permission, shows run status,
requires an explicit start confirmation, and provides a visible stop/cancel
action. Define a versioned verified-run handoff without plaintext passwords,
session cookies, or complete verification URLs.

Do not implement stealth behavior, browser/device fingerprint spoofing,
CAPTCHA solving, rate-limit bypass, bulk account creation, or hidden
background control. Do not run a real browser, mailbox, account, or Android
device action. Add or update documentation only when the implementation
requires it. Run local static checks and record evidence in
PROGRESS_TRACKER.md.
```

## 2. Implement the accessibility observation layer

```text
Read AGENT_START_PROMPT.md, PROGRESS_TRACKER.md, KOTLIN_CONTEXT.md,
KOTLIN_ARCHITECTURE.md, KOTLIN_PLAN.md, and KOTLIN_STEPS.md completely before
editing. Inspect the Kotlin scaffold and the current Python Android adapter.

Implement only the accessibility observation layer:

1. Declare and explain the accessibility service.
2. Filter observations to com.replit.app and required system surfaces.
3. Capture immutable, redacted accessibility snapshots.
4. Track foreground package, window changes, content changes, and keyboard
   visibility.
5. Handle service disablement, lifecycle restart, cancellation, and
   permission loss as explicit failures.
6. Keep unrelated application content out of snapshots and logs.

Do not implement optimistic taps or the full onboarding flow yet. Do not use
accessibility as a stealth mechanism. Do not start a real device run without
explicit operator authorization. Add fixture/unit coverage for package
filtering, snapshot redaction, and keyboard-state observation. Run local checks
and update PROGRESS_TRACKER.md with exact evidence.
```

## 3. Implement semantic state classification and transitions

```text
Read AGENT_START_PROMPT.md, PROGRESS_TRACKER.md, CORE_FLOW.md, STEPS.md,
IMPLEMENTATION.md, KOTLIN_CONTEXT.md, KOTLIN_ARCHITECTURE.md,
KOTLIN_PLAN.md, and KOTLIN_STEPS.md before editing.

Implement only the Kotlin Android state classifier and safe transition
primitives. Use the existing Android screenshots and sanitized accessibility
fixtures as references.

Use this matching order:

1. stable resource ID;
2. exact visible text;
3. content description;
4. constrained partial text;
5. current identified-node bounds only after semantic identification.

Every action must verify its destination state. A click, text entry, timeout,
coordinate, screenshot, or approximate visual match is not success by itself.
Unknown or contradictory UI must become MANUAL_REQUIRED or FAILED.

Cover the named states from KOTLIN_ARCHITECTURE.md, keyboard-visible and
keyboard-hidden email entry, password-field confirmation without logging the
value, one bounded invalid-login retry, and controlled scrolling. Do not add
OCR or computer vision as a sole proof mechanism. Do not run against a real
device without explicit authorization. Add fixture tests and update the
tracker with evidence.
```

## 4. Implement the Android flow and recovery model

```text
Read AGENT_START_PROMPT.md, PROGRESS_TRACKER.md, CORE_FLOW.md, STEPS.md,
IMPLEMENTATION.md, KOTLIN_CONTEXT.md, KOTLIN_ARCHITECTURE.md,
KOTLIN_PLAN.md, and KOTLIN_STEPS.md before editing.

Implement the named Android flow only:

1. verified-run handoff and device check;
2. launch com.replit.app;
3. Continue and Continue with Email;
4. email and password entry;
5. login result and one invalid-credentials retry;
6. Welcome, name, username, source, and Developer onboarding;
7. visible Skip handling;
8. profile menu and bounded scroll to Log Out;
9. logout confirmation;
10. force-stop and not-foreground verification.

Use a checkpoint whose stage means the next stage to run, plus an explicit
in-progress marker. Write checkpoints atomically. On every failed transition,
save protected/redacted evidence. Distinguish retry, manual takeover,
operator skip, cancellation, and terminal failure.

Do not repeat account creation or email verification when resuming a later
stage. Do not store or print plaintext passwords or complete verification
URLs. Do not solve CAPTCHA, bypass anti-bot controls, or attempt to conceal
automation. Do not run a real device action without explicit authorization.
Add unit, fixture, and interruption/recovery tests. Update
PROGRESS_TRACKER.md with exact evidence.
```

## 5. Validate the Kotlin controller

```text
Read AGENT_START_PROMPT.md, PROGRESS_TRACKER.md, KOTLIN_CONTEXT.md,
KOTLIN_ARCHITECTURE.md, KOTLIN_PLAN.md, and KOTLIN_STEPS.md before testing.
Do not claim end-to-end success from compilation or fixture tests alone.

Run validation in this order:

1. Kotlin static checks and unit tests.
2. Accessibility snapshot and classifier fixture tests.
3. Transition postcondition tests.
4. Checkpoint interruption and resume tests.
5. Emulator smoke test from the launcher.
6. Only after explicit operator authorization, an Android smoke test from the
   home screen on the intended device.
7. Only after the smoke test passes, one complete operator-authorized run.

For the real-device run, verify package identity, accessibility permission,
keyboard behavior, every onboarding destination, logout confirmation, and
force-stop foreground state. Stop on unknown UI, missing nodes, permission
loss, CAPTCHA, anti-bot challenge, or contradictory evidence.

Record observed postconditions, protected evidence references, blockers, and
the final verdict in PROGRESS_TRACKER.md. Keep the Python path available until
Kotlin has passed the controlled cutover gate.
```

## 6. Review the migration before cutover

```text
Read all Kotlin documents and inspect both the Kotlin implementation and the
current Python Android implementation. This is a migration review, not a
permission to run a live flow.

Check that:

- the PC flow remains unchanged and usable;
- Kotlin starts only from an explicit verified-run handoff;
- accessibility is scoped to the intended package and active run;
- every action verifies a destination state;
- unknown UI fails closed;
- CAPTCHA and anti-bot states remain manual;
- no password, session cookie, or complete verification URL is logged;
- checkpoints are atomic and later-stage resume cannot create another account;
- logout and force-stop are proven;
- the Python implementation remains a rollback path;
- the tracker contains real evidence rather than assumptions.

Report gaps with file references. Do not silently fix unrelated issues, remove
the Python path, or claim parity without an authorized device result.
```

## Prompt usage rules

- Give an agent one prompt at a time.
- Require the agent to read the listed documents before editing.
- Keep real-device actions separate from offline implementation work.
- Require exact evidence before checking off tracker items.
- Never treat a cancelled or blocked live run as a successful run.
- Keep the current Python implementation until the Kotlin cutover is
  deliberately approved.
