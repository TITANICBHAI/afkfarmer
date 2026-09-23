# Kotlin Android Migration Plan

## Objective

Build a Kotlin Android controller that can take over the Android portion of the
existing onboarding flow after the PC flow has completed email verification.
The controller will use visible, operator-authorized accessibility interaction
with the Replit Android app, checkpoint every stage, save failure evidence, and
prove each destination state before advancing.

This is a staged migration, not an immediate rewrite. The current Python,
Playwright, and ADB implementation remains the reference and rollback path.

## Source of truth

Use these documents in this order:

1. `CORE_FLOW.md` — canonical product flow;
2. `STEPS.md` — supplied step-by-step behavior;
3. `IMPLEMENTATION.md` — current postconditions and evidence rules;
4. `ARCHITECTURE.md` — current PC/Android boundaries;
5. `KOTLIN_ARCHITECTURE.md` — Android-native design proposed here;
6. `VISUAL_FALLBACK.md` — deferred visual evidence rules.

Older attached notes that describe a different mailbox provider, package name,
or Android ordering must not override the current flow.

## Scope

### Included

- Kotlin Android project setup;
- explicit accessibility-service enablement;
- intended-device and installed-package checks;
- Replit app launch and foreground verification;
- semantic accessibility snapshots;
- named Android state classification;
- node-bound actions;
- keyboard-visible and keyboard-hidden email states;
- password entry without logging the value;
- one bounded invalid-login retry;
- welcome/onboarding selections;
- profile navigation and logout confirmation;
- force-stop and not-foreground verification;
- atomic checkpoints and resume decisions;
- screenshots/UI evidence on failure where Android permits it;
- explicit manual takeover states;
- handoff from the completed PC verification stage;
- unit, fixture, emulator, and authorized device-smoke gates.

### Excluded

- CAPTCHA solving;
- anti-bot or rate-limit circumvention;
- browser/device fingerprint spoofing;
- hidden or unattended automation;
- bulk account creation;
- credential harvesting;
- automatic substitution of a blocked mailbox provider;
- replacing the PC browser flow in the first Kotlin milestone;
- OCR/computer vision as sole transition proof;
- removing the Python implementation before parity is demonstrated.

## Milestones

### Milestone 0 — Confirm the migration boundary

**Deliverables**

- confirm that Kotlin owns the Android phase only;
- confirm the PC flow remains Python/Playwright;
- define the verified-run handoff format;
- decide the minimum supported Android version;
- identify the target device and Replit package as `com.replit.app`.

**Gate**

The handoff contains a run ID and explicit verified status but no plaintext
password, session cookie, or complete verification URL. The operator agrees
that accessibility is visible and authorized.

### Milestone 1 — Scaffold the Kotlin app

**Deliverables**

- Kotlin Android project;
- Compose or equivalent operator UI;
- build variants for debug and release;
- application settings screen;
- run-status screen;
- accessibility-permission screen;
- stop/cancel control;
- local persistence layer;
- redacted logging foundation.

**Gate**

The app installs, starts without the accessibility service enabled, explains
why the permission is needed, and does not attempt to control another app until
the operator enables the service and starts a run.

### Milestone 2 — Build the accessibility observation layer

**Deliverables**

- `ReplitAccessibilityService`;
- package filtering;
- window/content-change event handling;
- immutable redacted snapshots;
- node tree traversal;
- keyboard-state observation;
- foreground-package verification;
- service lifecycle and permission-loss handling.

**Gate**

On a sanitized fixture and emulator, the app can distinguish Replit, launcher,
keyboard, and unexpected-package windows without collecting unrelated app
content.

### Milestone 3 — Implement the state classifier

**Deliverables**

- screen signatures for all Android states in
  `KOTLIN_ARCHITECTURE.md`;
- exact text/content-description/resource-ID matching;
- `UNKNOWN` state handling;
- fixture snapshots derived from the existing screenshot/state references;
- state-diff diagnostics for missing or changed UI nodes.

**Gate**

Every supported fixture is classified correctly, and an incomplete or
contradictory fixture is classified as `UNKNOWN` or manual takeover rather
than as a successful state.

### Milestone 4 — Implement safe transition primitives

**Deliverables**

- node-bound click action;
- semantic text entry;
- focused-field verification;
- keyboard-aware transitions;
- bounded waits based on observed state changes;
- controlled scroll action with post-scroll verification;
- one-time retry counter for invalid credentials;
- typed action results:
  `SUCCESS`, `RETRYABLE`, `MANUAL_REQUIRED`, `FAILED`.

**Gate**

Unit and fixture tests prove that:

- a click without a destination state is not success;
- a missing node fails closed;
- coordinate fallback cannot report success;
- invalid login is retried at most once;
- password text never appears in logs or evidence summaries.

### Milestone 5 — Implement the Android flow

Implement the transitions in the order required by the current flow:

1. verify the device and launch Replit;
2. wait for the initial Replit state;
3. select Continue and Continue with Email;
4. handle keyboard-visible and keyboard-hidden email states;
5. enter the email and verify the password screen;
6. enter the password and verify the login processing state;
7. classify invalid credentials and retry once if required;
8. verify the Welcome screen;
9. continue through name and username states;
10. select Google Search;
11. select Developer;
12. handle the visible Skip control;
13. open the profile menu;
14. scroll until Log Out is visible;
15. confirm the logout dialog;
16. verify the logged-out state;
17. force-stop Replit and verify it is not foreground.

**Gate**

Each transition has a named precondition, action, destination postcondition,
failure evidence path, and resume policy. No fixed delay is the only readiness
check.

### Milestone 6 — Add checkpoint and evidence behavior

**Deliverables**

- versioned checkpoint schema;
- atomic writes;
- in-progress transition marker;
- resume/retry/manual-takeover/abort menu;
- protected evidence directory;
- redacted UI tree;
- screenshot capture where supported;
- evidence IDs linked to failed transitions;
- cleanup on cancellation and completion.

**Gate**

Interrupting before, during, and after each Android stage resumes from the
correct stage without starting a new account or repeating a completed
verification step.

### Milestone 7 — Add the verified-run handoff

**Deliverables**

- import or receive the verified-run record;
- display the run summary to the operator;
- require explicit confirmation before opening Replit;
- reject incomplete or unverified handoffs;
- keep credentials out of the handoff;
- record the source checkpoint and handoff version.

**Gate**

The Android app cannot start from a fabricated or incomplete `verified=false`
record, and the operator can cancel before any Replit interaction begins.

### Milestone 8 — Validate against the real device

Run in this order:

1. static Kotlin checks and unit tests;
2. fixture and transition tests;
3. emulator test from the launcher;
4. authorized device smoke test from the home screen;
5. Android flow smoke test stopping on any unexpected state;
6. one complete operator-authorized run;
7. compare Kotlin evidence and outcomes with the Python baseline.

**Gate**

The device smoke test confirms the real accessibility hierarchy, package name,
keyboard behavior, onboarding labels, profile scrolling, logout dialog, and
force-stop result. A passing build or fixture suite alone is not enough.

### Milestone 9 — Controlled cutover

Only after the previous gate passes:

- make Kotlin the selected Android controller for new runs;
- keep Python Android support available behind an explicit rollback option;
- document the Kotlin run procedure;
- update `PROGRESS_TRACKER.md` with actual evidence;
- run one additional authorized smoke test;
- retire the Python Android layer in a separate change only if rollback is no
  longer needed.

## Data and security rules

- Never log plaintext passwords.
- Never log complete verification URLs.
- Store only password references or operator-selected secure references.
- Redact email addresses in ordinary logs where practical.
- Protect screenshots and UI trees because they can contain account and session
  data.
- Delete or expire runtime evidence according to the operator's retention
  setting.
- Do not send accessibility snapshots to a remote service.
- Keep the accessibility service limited to the active run and relevant
  package.

The password sample in `STEPS.md` is not a safe application default. Review
and replace any sample value before a real run.

## Acceptance checklist

### Flow correctness

- [ ] Kotlin starts only after an explicit verified-run handoff.
- [ ] Replit is launched only on the intended device.
- [ ] Every supported screen has a classifier signature.
- [ ] Every action verifies its destination state.
- [ ] Keyboard visibility is observed.
- [ ] Invalid credentials receive at most one automatic retry.
- [ ] Logout confirmation is observed before confirmation is tapped.
- [ ] Replit is confirmed not foreground after force-stop.

### Recovery

- [ ] Checkpoints are atomic.
- [ ] In-progress transitions are visible after a crash.
- [ ] Resume cannot recreate an account.
- [ ] Manual takeover is distinct from success.
- [ ] Cancellation cleans up the service and run state.

### Privacy and safety

- [ ] Accessibility permission is explicitly explained and enabled.
- [ ] A visible stop action is available.
- [ ] No stealth, fingerprint spoofing, CAPTCHA solving, or rate-limit bypass
      exists.
- [ ] Sensitive artifacts are redacted or protected.
- [ ] The app does not observe unrelated applications.

### Verification

- [ ] Unit tests pass.
- [ ] Sanitized accessibility fixtures pass.
- [ ] Emulator smoke test passes.
- [ ] Authorized real-device smoke test passes.
- [ ] One complete run is recorded with evidence.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Replit changes text or resource IDs | Keep selectors/configuration separate and fail closed on unknown states |
| Custom rendering exposes incomplete nodes | Record evidence; consider Appium/UiAutomator2 before adding visual fallback |
| Keyboard changes bounds | Observe keyboard state and refetch nodes after every keyboard transition |
| Accessibility service is disabled or killed | Show a resumable failure and require explicit re-enable |
| Browser/web flow differs on Android | Keep PC verification in Python for the first milestone |
| Sensitive evidence is leaked | Redaction, protected storage, retention controls, and no remote snapshot upload |
| Kotlin and Python use conflicting checkpoints | Version the handoff and keep ownership of each state file explicit |

## Open decisions

These should be answered before implementation begins:

1. Minimum supported Android API level.
2. Whether Compose is acceptable for the operator UI.
3. Whether screenshots can use `AccessibilityService.takeScreenshot` on the
   target device or require a separate user-approved capture path.
4. Whether the first handoff is an imported local file, an explicit operator
   entry, or a local PC-to-device bridge.
5. Which non-secret run metadata the operator wants displayed.
6. Whether the Android app should support only the installed Replit app or
   multiple explicitly configured package variants.

## Kotlin documentation set

The current planning and implementation-context documents are:

- `KOTLIN_CONTEXT.md` — quick-start context, boundaries, authoritative files,
  and session-start checklist;
- `KOTLIN_ARCHITECTURE.md` — system design and component responsibilities;
- `KOTLIN_PLAN.md` — migration milestones, gates, and acceptance criteria;
- `KOTLIN_STEPS.md` — operational Android runbook with preconditions,
  postconditions, failure behavior, and resume rules.

Do not add a setup guide or test matrix until the Kotlin project is scaffolded.
At that point, create them only if they contain information that cannot be
kept close to the code:

- `KOTLIN_SETUP.md` for device, Android Studio, accessibility permission, and
  build setup;
- `KOTLIN_TEST_MATRIX.md` for real-device state coverage and evidence IDs.
