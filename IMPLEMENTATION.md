# Implementation Specification

## Purpose

Implement the exact flow in `CORE_FLOW.md` for one operator-owned account, one
operator-owned PC, and one operator-owned Android device. CAPTCHA and other
anti-bot challenges remain manual. This document describes the implementation
target; it does not authorize bulk account creation, CAPTCHA solving, rate-limit
evasion, or third-party mailbox interception.

## Canonical project layout

The active source files now use the canonical names expected by their imports:

```text
config.py
temp_mail.py
android_automation.py
pc_automation.py
main.py
requirements.txt
state.json                 # runtime, ignored from source control
auth_state.json            # runtime, ignored from source control
android_actions.log       # runtime
ui.xml                     # failure evidence
screenshots/               # failure evidence
```

Any duplicate orchestrator must remain outside the active import path.

## Module contracts

### `config.py`

Owns non-secret runtime configuration:

- browser and page timeouts;
- Android package and optional device ID;
- selected temporary-mail provider;
- GitHub repository URL;
- retry/backoff limits;
- debug/evidence settings.

The password should be supplied through a local configuration mechanism rather
than printed or embedded in logs. Existing values must be reviewed before the
first real run.

### `temp_mail.py`

Owns the browser-backed provider interface and the `temp-mail.org` adapter.
It waits for a real address, extracts it from likely address controls or page
text, finds a visible Copy control, and requires either matching clipboard text
or visible copy confirmation. Failures save a tagged screenshot.

The module does not contact the provider at import time.

### `pc_automation.py`

`PCAutomation` owns one Playwright instance, browser context, and page set.
Required operations:

- `setup_browser()`;
- `obtain_temp_email()`;
- `open_replit_signup()`;
- `create_account(email, password)`;
- `handle_captcha()` with a manual pause;
- `open_verification_message()`;
- `verify_email()`;
- `login_after_mobile()`;
- `import_github_repo(url)`;
- `save_state()` and `close_browser()`.

All methods return a result that distinguishes success, a recoverable timeout,
and manual takeover. A successful click is not the same as a successful state
transition.

### `android_automation.py`

`AndroidAutomation` owns ADB invocation, UI hierarchy dumps, screenshots, and
named state transitions. Prefer this order for every action:

1. resource ID;
2. exact visible text;
3. content description;
4. a constrained partial-text match;
5. percentage coordinates only when the preceding options are unavailable and
   the operator has approved the fallback.

The current `tap_text_or_pct()` is not safe as a final primitive because it
returns `True` after a fallback tap without verifying the next state. Replace
it with a transition helper that verifies both the source and destination
states.

### `main.py`

The orchestrator owns the stage machine:

```text
EMAIL
SIGNUP
VERIFY
ANDROID
PC_LOGIN
GITHUB
DONE
```

`state["stage"]` means the next stage to run. A stage is advanced only after
its postcondition is observed. A failed stage remains resumable and records
evidence. Manual takeover must be explicit and logged as a human decision, not
silently treated as automated success.

## PC implementation details

### Temporary mailbox

The supplied flow is visual and begins at `temp-mail.org`. The implementation
must wait for the visible address, capture it, and confirm that the Copy action
produced the same value. Do not continue from a loading placeholder.

The current `stage_email` now uses the browser-backed provider in `temp_mail.py`
and selects `temp-mail.org` explicitly. The later verification stage still
needs its own browser-mail implementation; until then, the legacy 1secmail API
path is refused instead of being used silently.

### Replit registration

Use locator-driven waits for:

- optional Google sign-in close control;
- Create Account;
- Email;
- email and password fields;
- form submit;
- any visible validation error.

Pause for a human if a CAPTCHA or anti-bot challenge is detected. Never retry
the challenge automatically.

### Email verification

The visible flow is:

```text
temp-mail inbox
  -> Replit verification message
  -> Verify Email
  -> Verify Now
  -> Verifying email
  -> explicit success/tick or verified dashboard state
```

Filter by sender and subject before opening a message. Do not accept the first
mail in the inbox. Verify that the destination URL is an expected Replit
verification URL before navigating to it.

## Android implementation details

The supplied screenshots are the visual reference set:

| State | Reference |
|---|---|
| Email field with keyboard and Google option visible | `attached_assets/7_1790062961791.jpg` |
| Email field with keyboard settled | `attached_assets/8_1790062961792.jpg` |
| Empty password field | `attached_assets/9_1790062961793.jpg` |
| Password entered | `attached_assets/10_1790062961795.jpg` |
| Password revealed | `attached_assets/11_1790062961796.jpg` |
| Login processing spinner | `attached_assets/12_1790062961797.jpg` |
| Invalid credentials banner | `attached_assets/13_1790062961798.jpg` |
| Welcome screen | `attached_assets/14_1790062961799.jpg` |
| Name screen | `attached_assets/15_1790062961800.jpg` |
| Username screen | `attached_assets/16_1790062961801.jpg` |
| Google search source | `attached_assets/17_1790062961802.jpg` |
| Developer role | `attached_assets/18_1790062961803.jpg` |
| Subscription screen with Skip | `attached_assets/19_1790062961803.jpg` |
| Main interface | `attached_assets/20_1790062961804.jpg` |
| Profile menu before scrolling | `attached_assets/21_1790062961805.jpg` |
| Profile menu with Log Out visible | `attached_assets/22_1790062961806.jpg` |
| Logout confirmation | `attached_assets/23_1790062961807.jpg` |
| Post-close home screen | `attached_assets/24_1790062961808.jpg` |

The earlier PNG references cover the PC registration and mailbox states.

### Android state transitions

| Transition | Required postcondition |
|---|---|
| Launch app | Replit package is foreground and welcome/login content is present |
| Continue | Email choice or email-login screen is visible |
| Continue with Email | Email/username field is visible |
| Enter email | Field value is present; keyboard state may vary |
| Continue | Password field and Login are visible |
| Enter password | Password field is non-empty |
| Login | Spinner resolves to success or known invalid-credentials banner |
| Retry Login | Banner disappears and welcome state becomes reachable |
| Welcome Continue | Name screen is visible |
| Name Continue | Username screen is visible |
| Username Continue | Source screen is visible |
| Google search Continue | Role screen is visible |
| Developer Continue | Main/subscription onboarding is visible |
| Skip | Expected next onboarding/main screen is visible |
| Open profile | Profile menu is visible |
| Scroll | Log Out is visible |
| Log Out | Confirmation dialog is visible |
| Confirm Log Out | Logged-out state is visible |
| Force-stop | `com.replit.android` is not foreground |

Keyboard visibility must be observed, not inferred from a fixed Y coordinate.
When the keyboard is open, use the current UI node bounds. Hide the keyboard
only when the next control is obscured and verify that the screen did not
change unexpectedly.

## Evidence and failure behavior

Every failed transition saves:

- a timestamped screenshot;
- the current UI hierarchy XML;
- the stage and transition name;
- the last observed screen signature;
- the command/action attempted.

No catch-all path may return `True`. Network errors, missing nodes, malformed
XML, ADB errors, and unexpected screens must remain failures.

### Deferred visual fallback

`VISUAL_FALLBACK.md` defines a future secondary evidence layer based on OCR,
computer-vision landmarks, and tolerant pixel/perceptual checks. It is
intentionally not implemented in this phase. If later device evidence shows
that UI Automator cannot observe a required state, visual checks may supplement
the XML classifier, but they must not replace destination-state verification or
make a missing node successful.

## Acceptance criteria

- Every step in `CORE_FLOW.md` has a named implementation transition.
- No stage uses a fixed sleep as its only readiness check.
- No coordinate fallback can silently report success.
- The invalid-credentials retry happens at most once automatically.
- The app is force-stopped and verified closed before Android is complete.
- Resume cannot create a second account after a later-stage failure.
- Normal logs do not print the password or complete verification URL.