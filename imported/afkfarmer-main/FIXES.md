# Fix log

Three passes. None of them changes how CAPTCHA, anti-bot, security-challenge or
rate-limit checkpoints are handled: they stay manual. The run pauses, a person
completes the challenge, and nothing here solves, hides or bypasses one.

## Pass 3 (2026-10-22): operator feedback

What changed, in the order it was asked for.

| Request | What changed | Proven how |
|---|---|---|
| CAPTCHA wait: sound, window to the front, carry on by itself | `operator_alert.py`, `handle_captcha` / `_wait_for_operator`. A sound and the window to the front when a challenge appears; the run resumes once it has been gone 2.5 s; ENTER still works; a reminder sound every 60 s; the failed-stage menu beeps. Android has no auto-continue (a challenge there stays terminal). Switch off with `ALERT_SOUND = False` or `AFK_NO_SOUND=1`. | Unit tests on a virtual clock; real Chromium simulation (a "person" solves it only after the sound). |
| Mailbox tab stalled (found by the browser simulation) | A tab that is not in front is throttled by the browser (about 1-2 frames/s measured in headless Chromium), so a click on an inbox row that redraws every second missed. The mailbox tab is brought to the front, a fresh element is clicked, and a mouse click is the fallback. | Measured; fixed; real-browser tests. |
| Android: it taps Profile / Edit Profile and does not scroll to Log Out | (1) The settings sheet is now recognised before the home screen (a modern `uiautomator dump` can include the home screen behind the sheet, which made the sheet look like the home screen), and also after it was scrolled. (2) Opening the profile never taps an "Edit Profile" element. (3) The swipe is drawn inside the sheet's scroll container and retried with other gestures when nothing moved. (4) A wrong screen is left with BACK, and the app is reopened when it left the foreground (bounded). (5) Failure evidence now includes the UI tree (`screenshots/android_*.xml`). | Simulated phone with a scrolling sheet, 17 tests. **Not seen on a real phone**: the cause above is my best explanation of your report, not something I observed. |
| The browser window was isolated, closed with the script, felt slow | The run now starts its own Chrome / Edge window as a normal operating-system process with a saved profile folder (`browser_profile/`), attaches over the DevTools port, and only disconnects at the end. The window stays open, keeps the login, and the next run reattaches to the same tabs. It is started with the switches that stop the browser from throttling a window that is behind other windows. `BROWSER_MODE = "managed"` restores the old behaviour. | Real Chromium: survives the script, survives the whole Python process ending, reattached by the next run, login cookie survives a browser restart. **Your everyday browser profile cannot be used**: since Chrome 136 the remote-debugging switch is ignored for the default profile ([Chrome blog](https://developer.chrome.com/blog/remote-debugging-port)); whether Edge does the same was not verified. Why it felt slow is a hypothesis (throttled / covered window, polling); not measured on your machine. |
| Detect the step and the point inside it | `stage_detect.py` plus page / phone observation. At start the saved checkpoint is compared with the Replit tab and the phone screen, and visible progress moves the run forward (never backwards). Inside sign-up the page is read first: "check your email" page means done, an open form or chooser is continued in place instead of reloading, an old Replit login is signed out (own profile) or asked about. After a manual takeover the run continues from where the screens are. `AUTO_DETECT_STAGE = False` turns it off. | Offline doubles and one real-browser test. The wording of the live Replit pages is assumed from the screenshots. |
| Delete the temp mailbox when it is no longer needed | After the Android part (default), or right after verification (`DELETE_TEMP_MAIL = "after_verify"`), or never. Clicks the page's Delete button only when the page shows this run's own address, confirms a fresh address replaced it, and never fails the run. | Real Chromium against the simulated page. The live Delete button's behaviour (confirmation dialog, new address) was **not observed**. |
| `preflight.py --integration` said "no browser found" on Windows | It searched PATH for `google-chrome`; on Windows browsers are in Program Files. It now uses the same finder as the run (`browser_locator.py`). | Unit tests. |

### Evidence status after pass 3

| Evidence | Status |
|---|---|
| Unit tests with scripted page / phone doubles | Run: 295 tests, 0 failures (282 without the real-browser tests, 13 skipped) |
| Real Chromium: simulated sites + browser lifecycle | Run, pass (`AFK_SIM_FULL=1`) |
| "Undo one fix" checks on the new code | 23 of 23 caught by a test (one more candidate, closing a CDP-attached browser, is harmless because Playwright only disconnects) |
| Live replit.com, temp-mail.org, 1secmail | **Never run** |
| Android device / the real Replit app | **Never run** |

Pass 3 could not be run live from the build sandbox (no route to the sites, no
phone). See the first-live-run checklist in `README.md`.

## Passes 1 and 2 (earlier)

## What has and has not been run

| Evidence | Status |
|---|---|
| Unit tests with scripted page/phone doubles | Run, 165 tests pass |
| Real headless Chromium against local pages that imitate Replit and temp-mail.org (`test_site_simulation.py`) | Run, pass |
| Live replit.com, temp-mail.org, 1secmail | **Never run** |
| Android device / the real Replit app | **Never run** |

Everything marked "fixed" below was reproduced first against a double or the
local imitation and then fixed. That proves the logic and the ordering of
actions. It does not prove that the live sites look like the imitation: the
imitation was built from the screenshots in `attached_assets/` and from
`CORE_FLOW.md`. The first live run is still the real test.

## Pass 2 (2026-10-19): end-to-end re-audit against the screenshots

`STEPS.md` is helpful but not complete, so the working step list was rebuilt
from every screenshot (the Replit sign-up modals, the temp-mail.org inbox and
message, and the 24 Android screens) and each step was compared with the code.

| # | Area | What was wrong | Fix |
|---|---|---|---|
| P1 | PC sign-up | Every Replit auth modal prints the permanent notice "This site is protected by reCAPTCHA Enterprise and the Google Privacy Policy and Terms of Service apply." The challenge detector counted it as a CAPTCHA, so a normal page paused or failed the run. | `strip_recaptcha_notice()` (`pc_flow.py`) runs before challenge detection in the PC flow and in the Android classifier. A real challenge (a challenge frame, or text such as "verify you are human") still pauses. |
| P2 | PC sign-up | The page hydrates late and validates the e-mail and password asynchronously ("Email is available", "Password is valid"). The old code clicked before the form existed or before validation finished, and gave up after a short fixed wait. | Observe-then-act loop (`_open_signup_form`): waits up to 45 s for the form, taps each control a bounded number of times, never presses the header "Create account" behind an open modal, falls back once to the deep link `/?authModal=signup`, reads every typed value back from its input, waits for both validation messages before submitting, then classifies the result (`_wait_for_signup_result`). Every failure saves evidence under its own tag. |
| P3 | PC sign-up | A challenge hosted in an iframe has no page text, so it never triggered the manual pause. A challenge that kept coming back could pause forever. Failing to close the optional Google pop-up aborted the sign-up. | Challenge frames are detected as well as text. At most 3 manual pauses, then a recorded failure (`signup_challenge_repeated`). A pop-up that cannot be closed is logged and ignored. |
| P4 | temp-mail.org | Rows were found with class-name selectors (`tr`, `[class*='mail']`, `[class*='message']`, ...) and the first match in page order was clicked. That is the outer wrapper, not the row; in the local imitation its centre is empty space, so the message never opened. Whether the real inbox is laid out like that is not known. A message shown inside an iframe was invisible to the code. The inbox was never refreshed while waiting. | The row is found by its content: the innermost element whose short text holds `verify@replit.com` and a subject mentioning replit, verify and mail; the visible subject text is a fallback click target. All frames are searched for the opened message. Refresh is pressed every 8 s while waiting. The sender is read from the row instead of being assumed. |
| P5 | Verification | The link in the mail is single-use. "Verify Now" could be pressed twice, which can consume it, and there was no fallback when the button's target was rejected. | One click. If the button's own link is not a Replit action-code URL, the plain-text link printed under the button is used instead, with the same validation. The legacy second control is only tried when nothing started. |
| P6 | temp-mail.org | A new mailbox session shows a different address; the code only timed out. | The error names the address that is shown and the one that was registered, and says the mailbox session was lost. |
| A1 | Android | The welcome screen's title uses a typographic apostrophe ("Let’s get started…", screenshot 14). The classifier only matched a plain one, so the screen was treated as the generic "Continue" screen and shared its tap budget with the first one. | Apostrophes are normalised before matching. |
| A2 | Android | Text typed with `adb shell input text` was assumed to arrive. After "Continue with Email" Replit can keep the login sheet over the e-mail form, and the first tap only closes it (screenshots 6 to 8; the flow note "tap once outside the login area"). The e-mail then went nowhere and Continue never enabled. | The e-mail and first name are read back from the UI and typed again when missing, replacing the text and never appending. The password is never read back (the UI dump hides it). A field that cannot be read is accepted after two tries, so an unreadable dump cannot fail a flow that works. |

Documentation: `STEPS.md` still said the post-Android stages run by default
(`config.py` and `CORE_FLOW.md` say they do not); it now agrees, and a section
of screenshot-derived behaviour was added at its end.

### How pass 2 was checked

- Unit tests: 165 tests in total. `AFK_SKIP_BROWSER_TESTS=1 python -m unittest`
  runs 158 and skips the 7 real-browser tests. With a Chromium-family browser
  and `AFK_SIM_FULL=1` all 165 run and pass (about 2 minutes in the sandbox
  used; machine dependent).
- "Undo one fix" checks: each fix was removed in a throw-away copy and the tests
  had to fail. 25 unit-level removals and 9 real-browser removals were all
  caught; none survived. The scripts that do this are not part of this archive.
- Before/after on the local imitation, measured with a throw-away runner that
  is not in this archive (`test_site_simulation.py` runs a subset of the same
  scenarios). 15 variants: early, default (1.5 s) and late (4 s) page render,
  inline or iframe message, div or table rows, visible reCAPTCHA badge, an extra
  e-mail box earlier on the page, an input that ignores a click made while
  validation is pending, text or iframe challenge, no legal notice, slow mail,
  mail only after pressing Refresh, and a lost mailbox session.
  - The code from before pass 2 fails at the sign-up step in all 15 variants.
    13 time out after 15 s waiting for the e-mail field: it looks for the
    controls once and does not wait for the page to render, and the imitation
    renders 1.5 s after load by default. One (the extra e-mail box earlier on
    the page) takes that box for the form's and then times out waiting for the
    password field. One (instant render) stops because the reCAPTCHA notice is
    read as a challenge.
  - Control runs on the old code remove its causes one at a time. With instant
    render and no legal notice, sign-up passes and the failure moves to the
    mailbox: with div rows the old search picks the page wrapper and clicks its
    centre, which is empty space below the single row (checked by probing the
    element it picked); with table rows the click does open the message, but if
    the message sits in an iframe its text is invisible to the old code and the
    wait ends after 32 s. Of the four row and message layouts measured this
    way, only table rows with an inline message let the old code finish the
    flow, and even then it follows the single-use link twice: the second
    attempt fails in the imitation and the run still reports success (P5).
  - The new code completes the PC flow (address, sign-up, open message, verify
    link followed exactly once) in 14 of the 15. In the 15th, the lost mailbox,
    no flow can finish: it stops at opening the message and says the page shows
    a different address than the account was created with. Variants that need a
    person (a challenge) are completed by the test the way a person would: the
    challenge is cleared first, then ENTER is pressed.
  - Limit: the imitation's render delay, markup and layout are assumptions taken
    from the screenshots. These runs show that the old logic breaks under those
    assumptions and the new logic does not; they do not show how the real sites
    behave.

### New test files

| File | What it is | What it is not |
|---|---|---|
| `fake_browser.py` | Scripted page, tab, frame and clock doubles. Lets the sign-up and mailbox code run through many timing and layout cases without real waiting: a virtual clock replaces sleeping, so a minute-long wait costs milliseconds. | A browser. It only does what its docstrings say. |
| `site_simulator.py` | Serves local imitations of replit.com, temp-mail.org and the reCAPTCHA frame to a real Chromium through request routing. All other web traffic is blocked. | The real sites. Layout, timing and anti-bot behaviour are those of the screenshots, not measured. |
| `test_site_simulation.py` | Runs the project's own PC flow in real headless Chromium against `site_simulator.py`. | Evidence about live behaviour. |
| `test_pc_automation.py`, `test_temp_mail.py`, `test_pc_flow.py`, `test_android_onboarding.py`, `test_android_automation.py` | Extended with the cases above. The simulated phone (`FakeReplitDevice`) now also reproduces the login sheet that stays over the e-mail form. | |

Run them:

```bash
python -m unittest                       # everything; browser tests skip themselves if no browser launches
AFK_SKIP_BROWSER_TESTS=1 python -m unittest          # force-skip the browser tests
PLAYWRIGHT_EXECUTABLE_PATH=/path/to/chrome AFK_SIM_FULL=1 python -m unittest test_site_simulation -v
```

On Windows PowerShell set the variables first, for example
`$env:AFK_SIM_FULL = "1"`. Without `PLAYWRIGHT_EXECUTABLE_PATH` the installed
Edge, Chrome, Brave or Chromium is found the same way the tool finds it.

### Not verified (needs a live run by the operator)

- How long the real Replit page takes to hydrate and validate, and whether it
  ignores an early click. The code now waits for the observable state instead of
  guessing.
- Whether the real temp-mail.org shows the message body in an iframe or inline,
  and its real row markup. Both layouts are handled; neither was observed live.
- The authenticated-page markers used by `wait_for_session_sync` and
  `login_after_mobile`, and the `import_github_repo` selectors (that stage is
  off by default).
- Anything on the phone: whether the dump shows typed text, whether the login
  sheet behaves as screenshots 6 to 8 suggest, the profile avatar label, and
  that `com.replit.app` (from `config.py`) is the installed package.
- 1secmail and the `hybrid` provider fallback against the live services.

### Decisions left to the operator (not changed on purpose)

1. `.replit` makes the Run button execute `bash github_push.sh --sync --yes`,
   which force-pushes and deletes remote-only files on the branch, while the
   docs describe that script as manual. Not touched; decide before pressing Run
   in Replit.
2. Android launch: the code starts the app with `monkey` (the launcher intent),
   the flow notes describe opening it from the app drawer. Documented in
   `STEPS.md`; if a device does not show the expected screen after the direct
   launch, use the manual takeover.
3. `RUN_POST_ANDROID_STAGES` is `False` (pass 1). Set it to `True` only if the PC
   resume and GitHub import should follow the phone stage.
4. `STEPS.md` 4.3 says to click "Verify Now" on the verification page.
   Screenshots 4.4 and 4.5 show a spinner and then a success message, with no
   button, so no second click is made.
5. `attached_assets/0.*` (Chrome on Android, the same sign-up in a mobile
   browser) are not referenced by any document or code.
6. There is no per-run event log. A failure keeps a screenshot in
   `screenshots/`: `pc_<tag>.png` on the PC (a later failure with the same tag
   overwrites it) and `android_<tag>_<time>.png` on the phone, plus the last UI
   dump of the phone as `ui.xml` in the working directory (overwritten by every
   dump). The console output is the only record of the order of events.

## Pass 1 (2026-10-15)

Scope: stop the flow stalling mid-way, fix broken behavior, harden weak spots.

| Area | Problem | Fix |
|---|---|---|
| Android | `force_stop_and_verify` could never pass: `pidof` exits 1 when the app is stopped, which was read as "unknown". The Android stage failed right after a successful logout. | `package_running` treats exit 1 + empty output as "not running"; falls back to `ps -A` if `pidof` is missing. |
| Android | Profile avatar tap was hard-coded to the text `"MI"` (only matched usernames starting "mi"). | Initials are derived from the first name that was typed (`Mipok` -> `MI`, per `attached_assets/20`, `21`). |
| Android | Fixed linear script: any retry, or an app already on a later screen (e.g. `main`), failed. | State-driven loop (`_drive_onboarding`): act on whatever screen is visible, per-control tap limits, bounded cycles, failure evidence on every dead end. |
| Android | Exact, case-sensitive label matching (`"LOG OUT"` vs `"Log out"`); non-clickable labels inside clickable rows were rejected. | Case/space-insensitive matching; tap the nearest actionable ancestor. |
| Android | Email step required one exact classified state although a field could be present in another. | Typing is keyed to the field, not the label; text is replaced, not appended, on retry. |
| Verify | "Verifying email success." (CORE_FLOW 4.5) was not recognised as success. | Added to `VERIFICATION_SUCCESS_PHRASES`. |
| Verify | 1secmail verification used an HTTP GET and searched the raw body for a success phrase. | `browser_verification.py` opens the link in the browser and observes the rendered page. |
| Email | `EMAIL_CHECK_TIMEOUT` only reached 1secmail; temp-mail used a hard-coded 30 s. | Passed to `TempMailOrgProvider`. |
| PC | `close_browser` exported **all cookies** of an attached (operator's own) browser to `auth_state.json`. | Only a browser this process launched is saved. |
| PC | Pop-up handler could click any dialog button (e.g. "Continue with Google"). | Only unlabeled (x) dialog buttons are considered. |
| Flow | `RUN_POST_ANDROID_STAGES` defaulted to True although CORE_FLOW ends after closing Replit. | Default False (opt-in). |
| MC scripts | `shuf -i -3-3` is invalid, so jitter/offset never worked; a stale lock file made the next toggle a no-op. | `$RANDOM`-based range; stale locks are cleaned and the script starts. |

### Known weak spots left alone

- `import_github_repo` (optional stage, off by default, not changed in either
  pass because it cannot be checked without the live site):
  - It looks for a prompt box by the placeholders `Start chatting`,
    `describe a task` and `Make an app`. The only screenshot of a signed-in
    replit.com home page (`attached_assets/0.5`, Chrome on Android) shows the
    placeholder "Describe your idea, Replit will bring it to life...", which
    none of the three matches. The desktop page may differ; not known.
  - `is_visible(timeout=5000)` returns at once (Playwright ignores the
    timeout), so a page that is still loading counts as "box not found".
  - The wait for progress ends on any page text containing "imported",
    "creating", "ready" or "project", and the URL check right after it needs
    `/~/`. A slow but successful import can therefore be reported as a failure.
  - The repository URL is typed into that prompt box as a sentence ("Import
    this GitHub repository: ..."). Judging by the placeholder, that is a
    request to Replit's builder, not a call to a GitHub import function; this
    is an inference from the screenshot and was not tested. Whether it does
    what the operator wants is for the operator to decide.
- Dead config keys: `ANDROID_STEP_DELAY`, `LOGIN_RETRY_DELAY`, `PAGE_LOAD_TIMEOUT`, `DEBUG_MODE`.
- Avatar initials assume Replit shows the first two letters of a one-word name (one reference account).
