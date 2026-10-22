# Kotlin Android Architecture Proposal

## Status

This document proposes an Android-native replacement for the current
`android_automation.py` and ADB/UI Automator layer. It is not yet the active
implementation.

The existing documents remain authoritative for the product flow:

- `CORE_FLOW.md` defines the intended sequence.
- `STEPS.md` is the supplied human-readable reference.
- `IMPLEMENTATION.md` defines the current Python contracts and postconditions.
- `ARCHITECTURE.md` defines the existing PC control-plane boundary.

The Kotlin implementation must reach parity with those documents before the
Python Android path is retired.

## Product boundary

This is a local, operator-driven tool for one operator-owned account, one
operator-owned Android device, and one explicitly authorized run.

The Kotlin app may observe and interact with the visible Replit Android app
through Android accessibility APIs. It must not:

- solve or bypass CAPTCHA or anti-bot challenges;
- spoof browser or device identity;
- hide automation from a website;
- bypass rate limits, access controls, or verification;
- create accounts in bulk;
- harvest credentials or session data;
- operate on an unapproved device;
- treat an approximate visual match as proof of success.

Accessibility is used for semantic observation and visible, authorized
interaction. It is not a stealth mechanism or a replacement for a normal user
confirmation when a service presents a challenge.

## Recommended system boundary

The first Kotlin version should replace only the Android portion:

```text
PC control plane
  Python + Playwright
  temp-mail.org + Replit web + email verification
              |
              | explicit verified-run handoff
              v
Android control plane
  Kotlin app + AccessibilityService
  Replit Android app
              |
              v
  verified logout + force-stop postcondition
```

The PC browser flow remains in Python initially because it already owns the
shared browser context, mailbox adapter, Replit web flow, GitHub import, and
checkpoint orchestration.

An Android-only browser flow is a separate future option, not a reason to
replace the current PC flow immediately. A WebView, Custom Tab, GeckoView, or
embedded Chromium instance can behave differently from ordinary Chrome and
may expose different cookies, authentication, rendering, and security
behavior. None of those choices guarantees that a website will treat the
session as a normal browser.

If the PC control plane must eventually be controlled from Android, add an
explicit local companion protocol after the Android-only flow is stable. Do
not silently make the Kotlin app responsible for the PC browser.

## Android application modules

The project should be a separate Kotlin Android application with a small
foreground UI and a narrowly scoped accessibility service.

```text
app/
  ui/
    RunSetupScreen
    AccessibilityPermissionScreen
    RunStatusScreen
    ManualTakeoverScreen
    EvidenceScreen
  flow/
    FlowCoordinator
    FlowStage
    FlowState
    StateClassifier
    TransitionExecutor
    RetryPolicy
  accessibility/
    ReplitAccessibilityService
    AccessibilitySnapshot
    NodeMatcher
    NodeActions
    KeyboardState
  device/
    AppLauncher
    ForegroundPackageReader
    DeviceIdentity
  handoff/
    VerifiedRunHandoff
    HandoffReader
  evidence/
    EvidenceStore
    ScreenshotCapture
    UiTreeRedactor
    ActionRecord
  security/
    SecretPolicy
    Redaction
    RunAuthorization
  persistence/
    RunStore
    CheckpointSerializer
```

The exact package names can change during implementation, but responsibilities
should remain separated. The accessibility service should not own the whole
state machine, and UI screens should not perform direct node tapping.

## Core components

### `FlowCoordinator`

Owns the ordered Android stages and is the only component allowed to advance
them. Each transition receives a current accessibility snapshot and returns a
typed result:

```text
SUCCESS
RETRYABLE
MANUAL_REQUIRED
FAILED
```

A tap or text-entry action is never itself a success. The coordinator advances
only after the destination state is observed.

### `ReplitAccessibilityService`

The service should:

- be explicitly enabled by the operator in Android Settings;
- filter observations to the Replit package and required system surfaces;
- receive window and content-change events;
- expose immutable snapshots to the coordinator;
- perform only node-bound actions requested by the coordinator;
- stop observing when the run is complete or cancelled;
- avoid collecting unrelated application content.

The service must handle lifecycle changes, service restarts, screen-off events,
and the operator revoking accessibility permission. Any of those conditions
must produce a resumable failure rather than silent success.

### `AccessibilitySnapshot`

Every snapshot should contain a redacted, structured representation of the
currently relevant window:

```text
packageName
windowClass
screenSignature
visibleText
contentDescriptions
resourceIds
enabled/clickable/focusable flags
node bounds
keyboard visibility
timestamp
```

Raw node trees should be retained only in protected failure evidence and
redacted before display. Password values, verification URLs, and session
tokens must not be written to ordinary logs.

### `StateClassifier`

Classifies a snapshot into a known state only when required evidence is
present. Example signatures include:

```text
HOME
REPLIT_LAUNCHING
REPLIT_INITIAL_CONTINUE
EMAIL_CHOICE
EMAIL_INPUT
PASSWORD_INPUT
LOGIN_PROCESSING
INVALID_CREDENTIALS
WELCOME
NAME_INPUT
USERNAME_INPUT
SOURCE_SELECTION
ROLE_SELECTION
SUBSCRIPTION_OR_MAIN
PROFILE_MENU
LOGOUT_CONFIRMATION
LOGGED_OUT
APP_CLOSED
UNKNOWN
```

An `UNKNOWN` state is not converted into the nearest known state. It enters a
manual-takeover or failed state with evidence.

### `NodeMatcher`

Use this matching order:

1. stable resource ID;
2. exact visible text;
3. content description;
4. constrained partial text;
5. current node bounds only after the node itself has been identified.

Hardcoded pixel coordinates are not an implementation strategy. Gesture
coordinates may be used only for an explicitly approved gesture, such as
scrolling, and the result must be checked through a new accessibility
snapshot.

### `NodeActions`

Actions should be node-bound and verified:

- click an enabled/clickable node;
- set text through `ACTION_SET_TEXT` where supported;
- request focus and observe the field;
- submit the IME action only when the intended field is focused;
- scroll the identified scrollable container;
- dismiss or show the keyboard only when the current state requires it.

After every action, the coordinator must verify the expected destination state.
If the target exposes no stable node, the operator must take over.

### `KeyboardState`

Keyboard visibility is observed from the current window and node state. It is
never inferred from a fixed screen coordinate.

The email and password transitions must support both:

- a field that is already focused with the keyboard visible;
- a login screen where the operator or the service must first select
  `Continue with Email`.

When the keyboard hides a required control, the app may request dismissal and
must confirm that the expected screen remains active afterward.

### `AppLauncher`

The launcher should:

- verify the intended device identity before a run;
- resolve the installed Replit package explicitly;
- launch `com.replit.app`;
- observe the foreground package;
- fail if the wrong package or an unexpected app is foreground;
- verify the package is not foreground after force-stop.

The current configured package is `com.replit.app`; older references to
`com.replit.android` are not authoritative.

### `RunStore` and checkpoints

Use DataStore for the small checkpoint record unless implementation evidence
shows that Room is needed. The state means the **next Android stage to run**,
not the last action attempted.

Recommended record:

```text
runId
stage
status
inProgressStage
verifiedHandoff
emailReference
usernameReference
passwordReference
retryCounters
lastScreenSignature
lastEvidenceId
createdAt
updatedAt
```

The record must not contain a plaintext password or a complete verification
URL. Writes must be atomic. A crash during a transition leaves an explicit
in-progress marker so the next launch offers resume, retry, manual takeover,
or abort.

The app must never recreate an account because a later Android stage failed.

## Verified-run handoff

The first integration should use an explicit handoff from the completed PC
verification stage. The handoff should contain only what the Android flow needs:

```text
runId
verified = true
emailReference or redacted email display
optional username/reference data
source checkpoint metadata
```

The handoff must not carry a plaintext password, session cookie, or complete
verification link. The operator can enter a password directly into the visible
Replit field, or a future secure credential mechanism can be added after a
separate security review.

The Android app must reject a handoff unless the operator confirms the run and
the source checkpoint says that email verification completed successfully.

## Android state and transition contract

The following mapping is based on `STEPS.md`, `CORE_FLOW.md`, and the
postconditions in `IMPLEMENTATION.md`:

| Transition | Required observation before advancing |
|---|---|
| Launch Replit | `com.replit.app` is foreground and Replit content is visible |
| Continue | email choice or email-login content is visible |
| Continue with Email | email/username field is visible |
| Enter email | the field contains the expected non-secret email reference |
| Continue | password field and login control are visible |
| Enter password | password field is non-empty; value is never logged |
| Login | processing resolves to success or the known invalid-credentials state |
| Retry Login | at most one automatic retry; success state becomes reachable |
| Welcome Continue | name screen is visible |
| Name Continue | username screen is visible |
| Username Continue | source-selection screen is visible |
| Select Google Search | role-selection screen is visible |
| Select Developer | subscription or main interface is visible |
| Skip | expected next onboarding/main state is visible |
| Open profile | profile menu is visible |
| Scroll profile | logout control is visible |
| Log Out | confirmation dialog is visible |
| Confirm Log Out | logged-out state is visible |
| Force-stop | `com.replit.app` is not foreground |

The exact text and resource IDs are configuration data where possible, so
minor Replit UI changes do not require rewriting the coordinator. Changes to
the state contract still require evidence and fixture updates.

## Evidence and manual takeover

Every failed transition must record:

- timestamp;
- run ID and stage;
- source and destination state signatures;
- action attempted;
- redacted accessibility snapshot;
- UI tree evidence when safe to retain;
- screenshot evidence when permission and Android version allow it;
- error category and operator decision.

For Android 11/API 30 and later, evaluate
`AccessibilityService.takeScreenshot` as the primary screenshot path. If the
device cannot provide an approved screenshot path, a screenshot is optional
evidence and the transition must still fail closed.

The operator-facing UI should clearly distinguish:

- retrying a transient wait;
- manual takeover required;
- an explicit operator skip;
- terminal failure;
- cancelled run.

Manual takeover must be recorded as a human decision. It must not be written as
automated success.

## Browser decision

The Kotlin app does not need an embedded browser for the Android Replit-app
portion. The recommended first build is:

1. Python/Playwright handles the PC mailbox and web verification flow.
2. Kotlin receives an explicit verified-run handoff.
3. Kotlin uses accessibility only for the visible Replit Android app.

If a future requirement moves the mailbox or Replit web flow into Android,
evaluate options in this order:

1. external Chrome with explicit operator interaction;
2. Chrome Custom Tab for a visible, browser-compatible surface;
3. WebView only if the target flow is confirmed to support it;
4. GeckoView or embedded Chromium only if a documented product requirement
   justifies the maintenance cost.

The choice must be based on compatibility and operator control, not on avoiding
website detection. No browser implementation may attempt to disguise
automation or bypass a challenge.

## Permissions and privacy

Request the minimum permissions:

- accessibility service permission, explicitly enabled by the operator;
- notification permission only if run-status notifications are needed;
- screenshot/MediaProjection capability only if required and clearly
  disclosed;
- no broad package enumeration;
- no overlay permission unless a later design proves it necessary.

The accessibility service must show an active-run indicator and provide a
visible stop action. It must stop on cancellation, completion, permission loss,
or unexpected package ownership.

## Testing architecture

The Kotlin project should have:

1. pure unit tests for classifiers, matcher ordering, retry rules, and
   checkpoint serialization;
2. fixture tests built from sanitized accessibility node trees;
3. transition tests proving source and destination checks;
4. emulator tests for service enablement, launch, keyboard states, scrolling,
   and force-stop verification;
5. an operator-authorized device smoke test;
6. one end-to-end run only after the smoke test gates pass.

The first Kotlin build must not depend on live Replit network behavior for its
unit tests.

## Migration rule

Keep the Python Android adapter runnable until all Kotlin acceptance gates are
complete. During migration:

- do not run both Android controllers against the same device;
- do not share mutable state files without a versioned schema;
- record which controller produced each evidence bundle;
- keep the Python path as rollback;
- remove the Python path only in a separate, deliberate change after a
  successful operator-authorized Kotlin run.
