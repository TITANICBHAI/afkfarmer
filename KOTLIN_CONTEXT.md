# Kotlin Migration Context

## Purpose

This file is the quick-start context for anyone implementing or reviewing the
Kotlin Android migration. It explains what is being replaced, what is not
being replaced, and which rules must remain intact.

## Current project

The repository currently contains a Python control plane for an operator-driven
onboarding flow:

```text
PC:
  Python + Playwright
  temp-mail.org -> Replit web signup -> visible email verification
  -> PC session resume -> GitHub import

Android:
  current Python + ADB/UI Automator layer
  Replit login -> onboarding -> logout -> force-stop verification
```

The Kotlin proposal replaces the Android layer first. It does not immediately
replace the PC browser flow.

## Target Kotlin responsibility

The Kotlin Android app starts only after the PC flow has produced an explicit
successful email-verification checkpoint. It then:

1. receives or imports a verified-run handoff;
2. asks the operator to confirm the run;
3. verifies the intended device and installed Replit package;
4. observes the visible Replit app through a narrowly scoped accessibility
   service;
5. performs semantic, node-bound interactions;
6. verifies every destination state;
7. records checkpoints and failure evidence;
8. completes logout and confirms Replit is no longer foreground.

The current Python Android implementation remains the reference and rollback
path until Kotlin passes the real-device gates.

## Authoritative documents

Read these in order:

1. `CORE_FLOW.md` — canonical overall flow;
2. `STEPS.md` — supplied human-readable flow;
3. `IMPLEMENTATION.md` — current contracts and postconditions;
4. `ARCHITECTURE.md` — current system boundary;
5. `KOTLIN_ARCHITECTURE.md` — proposed Kotlin design;
6. `KOTLIN_PLAN.md` — migration milestones and gates;
7. `KOTLIN_STEPS.md` — operational Android runbook;
8. `VISUAL_FALLBACK.md` — deferred visual-evidence rules.

If an older attached note conflicts with these files, use the current
`CORE_FLOW.md`, implementation contracts, and verified tracker state.

## Non-negotiable boundaries

This is visible, operator-authorized automation for an operator-owned device
and account. The Kotlin app must not:

- bypass CAPTCHA or anti-bot challenges;
- spoof device or browser identity;
- attempt to avoid detection;
- bypass rate limits or access controls;
- create accounts in bulk;
- collect unrelated app content;
- store or print plaintext passwords;
- treat a tap, timeout, screenshot, or approximate match as proof of success.

An accessibility service is used for semantic observation and user-authorized
interaction, not as a stealth mechanism.

## Canonical Android package

The current target package is:

```text
com.replit.app
```

Older references to `com.replit.android` are stale and must not be restored
without live device evidence.

## Canonical Android stages

```text
HANDOFF
-> DEVICE_CHECK
-> LAUNCH_REPLIT
-> EMAIL_LOGIN
-> PASSWORD_LOGIN
-> LOGIN_RESULT
-> WELCOME
-> NAME
-> USERNAME
-> SOURCE
-> ROLE
-> SUBSCRIPTION_OR_MAIN
-> PROFILE
-> LOGOUT_CONFIRMATION
-> LOGGED_OUT
-> FORCE_STOP
-> COMPLETE
```

The checkpoint represents the next stage to run. An in-progress marker records
an interrupted transition. Resuming a later stage must never create a second
account or repeat the PC verification flow.

## Evidence model

Every failed transition records, where supported:

- run ID;
- stage and transition;
- source and observed destination signatures;
- action attempted;
- redacted accessibility snapshot;
- screenshot and UI tree evidence;
- operator decision;
- timestamp and error category.

Evidence may contain sensitive account or session information and must be
protected. Normal logs must not contain passwords or complete verification
URLs.

## Session-start checklist

Before editing Kotlin code:

- read `KOTLIN_ARCHITECTURE.md`;
- read the relevant section of `KOTLIN_PLAN.md`;
- read `KOTLIN_STEPS.md`;
- inspect the current Python Android behavior before porting it;
- confirm the package name and target device assumptions;
- check whether the change affects checkpoint compatibility;
- keep the Python path runnable for rollback.

Before any real-device run:

- confirm the operator explicitly authorized the run;
- confirm the intended device;
- confirm accessibility permission is visibly enabled;
- confirm the verified-run handoff is complete;
- confirm no other controller is attached to the device;
- confirm evidence storage is protected.

## Implementation rule

Build and test pure classifiers and transition logic before connecting the
service to the real Replit app. A live build must stop on unknown or
contradictory UI rather than guessing.
