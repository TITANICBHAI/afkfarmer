# Kotlin Android Steps

## Scope

These steps cover the Android portion after the PC flow has completed email
verification. The PC mailbox, Replit web signup, visible email verification,
browser session synchronization, and GitHub import remain governed by the
existing Python flow.

The Kotlin app must be visible and operator-authorized. It must not attempt to
hide automation, bypass CAPTCHA, spoof identity, or evade website controls.

## Result types

Every step returns one of:

```text
SUCCESS
RETRYABLE
MANUAL_REQUIRED
FAILED
```

Only `SUCCESS` with an observed destination postcondition advances the
checkpoint.

## Step 0 — Prepare the verified run

### Preconditions

- The PC flow has reached explicit email-verification success.
- The operator has selected the intended Android device.
- No other automation controller is using the device.
- Accessibility permission is enabled for the Kotlin app.

### Actions

1. Open the Kotlin controller.
2. Import or select the verified-run handoff.
3. Confirm that the handoff says `verified = true`.
4. Review the redacted run summary.
5. Ask the operator to confirm starting the Android phase.

### Postcondition

The run is authorized and the Android checkpoint is `DEVICE_CHECK`.

### Failure behavior

Reject an absent, incomplete, or unverified handoff. Do not open Replit.

## Step 1 — Verify the device

### Actions

1. Read the device identity available to the app.
2. Confirm the accessibility service is active.
3. Confirm the Replit package is installed.
4. Confirm the configured target package is `com.replit.app`.
5. Confirm the current foreground package is expected.

### Postcondition

The intended device is confirmed and the app may proceed to launch Replit.

### Failure behavior

Stop with a clear manual action if the device is wrong, inaccessible, or the
service has lost permission.

## Step 2 — Reach the Android home screen

### Actions

1. Observe the current foreground package.
2. If necessary, ask the operator to return to the Android home screen.
3. Do not use a fixed coordinate as proof that the home screen is present.

### Postcondition

The launcher or home-screen state is observed, or the operator has explicitly
confirmed a safe launch starting point.

## Step 3 — Launch Replit

### Actions

1. Launch `com.replit.app` through the Android package manager/intent.
2. Wait for a foreground-package change.
3. Observe the initial Replit content.

### Postcondition

`com.replit.app` is foreground and a known Replit login/welcome signature is
visible.

### Failure behavior

Save evidence and stop if another package is foreground or the screen is
unknown.

## Step 4 — Continue to email login

### Actions

1. Locate the enabled `Continue` control by resource ID, exact text, or
   content description.
2. Activate the identified node.
3. Wait for the email choice/login state.
4. Select `Email` or `Continue with Email` when that exact state is visible.

### Postcondition

The email/username field is visible and ready for input.

### Failure behavior

Do not tap an approximate location when the intended node is not available.
Offer manual takeover with a current screenshot/UI snapshot.

## Step 5 — Enter the email

### Actions

1. Locate the email/username field.
2. Request focus if needed.
3. Observe whether the keyboard is visible.
4. Set or enter the email associated with the verified handoff.
5. Re-read the field state without logging the full value.

### Postcondition

The intended field contains a non-empty, matching email reference and the
current login state remains known.

### Failure behavior

If the keyboard is not visible, refetch the node after focusing it. Do not
infer keyboard state from a screen coordinate.

## Step 6 — Continue to password

### Actions

1. Locate the enabled `Continue` control in the email-login state.
2. Activate it.
3. Wait for the password field and login control.

### Postcondition

The password field and login control are visible.

## Step 7 — Enter the password

### Actions

1. Focus the password field.
2. Obtain the password through the approved operator-controlled mechanism.
3. Enter it without writing the value to logs, screenshots intended for normal
   display, checkpoints, or evidence summaries.
4. Confirm only that the password field is non-empty.

### Postcondition

The password field is populated and the login control is available.

### Failure behavior

If the field cannot be identified or focused, stop for manual takeover.

## Step 8 — Submit login and classify the result

### Actions

1. Activate the identified login/continue control.
2. Wait for the processing state using observed UI changes.
3. Classify the result as success, known invalid credentials, manual challenge,
   retryable loading, or unknown.

### Postcondition

The login resolves to a known successful state or the known invalid-credentials
state.

### Retry rule

If the known invalid-credentials state appears, one automatic retry is allowed.
The retry counter must be persisted. A second invalid result becomes
`MANUAL_REQUIRED` or `FAILED`.

CAPTCHA or anti-bot screens always become `MANUAL_REQUIRED`; they are never
automatically solved or bypassed.

## Step 9 — Complete the welcome flow

### Actions

1. Confirm the welcome text/state.
2. Activate `Continue`.
3. Wait for the name screen.
4. Enter the operator-approved first name if required.
5. Continue and wait for the username screen.
6. Confirm or complete the username according to the visible UI.
7. Continue and wait for source selection.

### Postconditions

- after welcome continue: name screen is visible;
- after name continue: username screen is visible;
- after username continue: source-selection screen is visible.

## Step 10 — Complete source and role selection

### Actions

1. Select `Google Search`.
2. Confirm the role-selection screen.
3. Select `Developer`.
4. Confirm the next subscription/onboarding/main-interface state.

### Postconditions

The selected option is observable and the next expected screen is visible
after each action.

## Step 11 — Handle the subscription/onboarding screen

### Actions

1. Locate the visible `Skip` control.
2. Confirm it is enabled and belongs to the current screen.
3. Activate it.
4. Wait for the next known onboarding or main-interface state.

### Postcondition

The expected post-skip state is visible.

### Failure behavior

Do not treat the absence of `Skip` as a successful skip. Save evidence and
offer manual takeover.

## Step 12 — Open the profile menu

### Actions

1. Locate the profile/avatar/menu control using semantic node data.
2. Activate it.
3. Wait for the profile menu.

### Postcondition

The profile menu is visible.

## Step 13 — Find Log Out

### Actions

1. Inspect the visible profile-menu nodes.
2. If `Log Out` is not visible, identify the current scrollable container.
3. Perform a bounded semantic scroll gesture.
4. Refresh the accessibility snapshot.
5. Repeat only within a bounded attempt count.

### Postcondition

`Log Out` is visible as an identified, enabled control.

### Failure behavior

Stop after the bounded scroll limit with evidence. Do not tap an arbitrary
coordinate.

## Step 14 — Confirm logout

### Actions

1. Activate the identified `Log Out` control.
2. Wait for the confirmation dialog.
3. Confirm the dialog contains the expected logout wording.
4. Activate the dialog's `Log Out` action.
5. Wait for the logged-out state.

### Postconditions

- the confirmation dialog was observed before confirmation;
- the logged-out state is visible after confirmation.

## Step 15 — Force-stop and verify closure

### Actions

1. Request the approved force-stop operation for `com.replit.app`.
2. Wait for the foreground package to change.
3. Re-read the foreground package.

### Postcondition

`com.replit.app` is not the foreground package.

### Failure behavior

If Replit remains foreground or the foreground state cannot be observed,
record evidence and keep the run incomplete.

## Step 16 — Finish the run

### Actions

1. Write the terminal checkpoint atomically.
2. Mark the run `COMPLETE`.
3. Stop the accessibility service's active-run observation.
4. Show a redacted summary to the operator.
5. Preserve protected evidence according to the retention setting.

### Postcondition

The Android stage is complete, the app is not foreground, and the PC
orchestrator may continue with its own explicitly verified postconditions.

## Resume rules

- `stage` means the next stage to run.
- `inProgressStage` identifies an interrupted transition.
- A crash during a transition requires a fresh snapshot before retrying.
- A later-stage resume must not repeat mailbox creation, account creation, or
  email verification.
- A manual takeover must be recorded separately from automated success.
- A cancelled run must not be resumed automatically.

## Evidence rules

On every failed transition, save:

- stage and transition name;
- source and observed destination signatures;
- action attempted;
- timestamp;
- redacted accessibility snapshot;
- screenshot/UI tree where the Android version and permissions support it;
- retry count and operator decision.

Visual analysis may supplement missing accessibility information only after
live evidence proves that the hierarchy is insufficient. It may never be the
sole proof of a transition.
