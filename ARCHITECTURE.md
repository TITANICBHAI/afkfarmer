# Architecture Decision Record

## System boundary

This is a local, operator-driven workflow tool. The control plane runs on the
PC. It controls:

```text
                  +----------------------+
                  |  Python orchestrator |
                  |  stage/checkpoint    |
                  +----------+-----------+
                             |
          +------------------+------------------+
          |                                     |
          v                                     v
 +-------------------+                 +-------------------+
 | Playwright / Edge |                 | ADB / UIAutomator|
 | temp-mail + web   |                 | operator Android |
 +-------------------+                 +-------------------+
          |                                     |
          v                                     v
  mailbox + Replit web              Replit Android app
```

The orchestrator is the only component allowed to advance a stage. Adapters
report observed facts; they do not silently infer success.

## Components

### Orchestrator

Owns the ordered stages, checkpoint file, recovery menu, manual takeover
decisions, and final cleanup. It should be deterministic with respect to the
saved state and should never create a new mailbox or account while resuming a
later stage.

### Temporary-mail adapter

Provides:

```text
create_or_read_address()
wait_for_message(sender, subject)
open_message(message)
extract_verification_action()
```

The first implementation should support the user-specified visible
`temp-mail.org` flow. An API-backed adapter may exist as an explicit alternate
provider, but it must not silently replace the requested flow.

### Web adapter

Owns Playwright and Edge. It works with a single browser context so the
temporary mailbox and Replit tabs share the operator's session. It uses
locator waits, explicit postconditions, URL allowlists, and manual CAPTCHA
pauses.

### Android adapter

Owns ADB subprocess calls, UI hierarchy dumps, node matching, node-bound
clicks, keyboard handling, swipe gestures, screenshots, and force-stop
verification. ADB is the transport; UI Automator data is the source of truth.

### Evidence store

Stores runtime artifacts outside source control:

```text
state.json
auth_state.json
android_actions.log
screenshots/
ui.xml
```

State and evidence must be redacted or protected because they can contain
account identifiers, session cookies, and verification URLs.

## State machine

```text
EMAIL -> SIGNUP -> VERIFY -> ANDROID -> PC_LOGIN -> GITHUB -> DONE
```

The checkpoint stores the next stage to run. Each stage has:

```text
precondition
action
postcondition
failure evidence
resume policy
```

The `DONE` state is terminal for that run. Starting another run requires an
explicit operator decision and a fresh state record.

## Locator strategy

For both web and Android, use semantic evidence first:

1. stable resource ID or accessible label;
2. exact visible text;
3. constrained partial text;
4. current node bounds for a click;
5. percentage coordinate fallback only as an operator-approved exception.

Hardcoded pixel coordinates are prohibited. Percentage coordinates are not
fully semantic and must not be presented as resolution-independent proof of
correctness: keyboard, system bars, scrolling, and layout changes can still
move the target.

### Deferred visual evidence fallback

The screenshot set may later support a secondary OCR/computer-vision/pixel
fallback when a device build exposes incomplete UI Automator nodes. The planned
order is UI XML first, screenshot capture second, and optional visual analysis
third. OCR may check required labels; computer vision may identify approximate
dialogs, banners, or selected controls; tolerant region/perceptual checks may
confirm distinctive visual cues.

Visual analysis must never be the sole proof of a transition. A visual match
cannot authorize a tap, advance a stage, or override a missing or contradictory
UI node. Low confidence or disagreement remains a failure/manual-takeover
state. The detailed deferred scope is in `VISUAL_FALLBACK.md`; no visual
analysis dependency is part of the current implementation.

## Error model

Each adapter operation returns one of:

- `SUCCESS`: the expected destination state was observed;
- `RETRYABLE`: a bounded retry may be attempted;
- `MANUAL_REQUIRED`: the operator must intervene;
- `FAILED`: evidence has been saved and the stage cannot proceed.

The orchestrator maps these outcomes to retry, manual takeover, or abort. A
missing element must never be converted to `SUCCESS` after a blind fallback.

## Security and compliance boundaries

- One account per run.
- Operator-owned PC and Android device only.
- CAPTCHA and anti-bot challenges are manual.
- No rate-limit bypass or challenge circumvention.
- No credential harvesting.
- Do not expose passwords, session state, or complete verification links in
  ordinary output.

## Kotlin companion assessment

### Recommendation

Do not start with a Kotlin companion. First harden the existing Python
ADB/UI-automator layer and measure the actual hierarchy on the target phone.

### Why not now

- The current workflow already has a PC control plane and ADB transport.
- A Kotlin `AccessibilityService` would require a separate Android project,
  installation, accessibility permission, service lifecycle handling, and a
  second process to coordinate with the PC.
- A service controlling another app can still receive incomplete or unstable
  nodes when the target uses custom rendering or WebViews.
- It would add deployment and permission complexity before proving that the
  current UI dumps are insufficient.

### Escalation trigger

Move to a Kotlin or Appium/UiAutomator2 approach only if repeated device tests
show that one or more required screens cannot expose stable text, content
descriptions, resource IDs, or clickable bounds through ADB UI dumps.

### If escalation is needed

Prefer this progression:

1. Appium with UiAutomator2 from the PC;
2. a Kotlin `AccessibilityService` only when the target requires custom
   accessibility observation or event-driven interaction.

The Kotlin design would expose a small local command protocol, such as:

```text
PC -> device: wait_for(text="Continue")
device -> PC: observed(screen="welcome", bounds=...)
PC -> device: click(node_id=...)
device -> PC: observed(screen="email_login")
```

Use ADB reverse port forwarding or a local WebSocket only after a threat and
permission review. The companion must remain limited to the operator's device
and must not implement CAPTCHA solving or anti-bot evasion.

## Key risks

1. Replit and temp-mail UI text/selectors may change.
2. Android UI nodes may differ with app version, keyboard, font scale, and
   device navigation mode.
3. Email delivery and verification can be delayed or rate-limited.
4. Browser session state can become stale between PC and Android phases.
5. Verification links and storage state are sensitive runtime artifacts.