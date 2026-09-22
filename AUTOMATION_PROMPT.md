# Implementation Prompt

Use this prompt when handing the implementation to another coding agent.

---

You are implementing a local, operator-driven onboarding-flow automation.
Read `CORE_FLOW.md`, `PLAN.md`, `IMPLEMENTATION.md`, and `ARCHITECTURE.md`
before editing code. Use the screenshot assets in `attached_assets/` as the
visual reference for the PC and Android states.

## Goal

Implement the exact flow in `CORE_FLOW.md`:

1. Open `temp-mail.org`, wait for a real temporary address, and copy it.
2. Open Replit in Edge, dismiss the optional Google popup, create the account
   through the Email flow, and pause for any CAPTCHA manually.
3. Return to the mailbox, open the Replit verification email, follow Verify
   Email and Verify Now, and wait for an explicit success state.
4. Open the operator's Replit Android app through ADB, complete the visible
   email login and onboarding screens, retry once if the invalid-credentials
   banner appears, skip the plan/main prompts, open the profile menu, log out,
   confirm logout, and force-stop the app.
5. Reload the PC session, confirm login or log in, and import the operator's
   GitHub repository.

## Non-negotiable boundaries

- One account per run.
- Operator-owned devices only.
- CAPTCHA and anti-bot challenges are manual.
- Never solve, bypass, or evade a CAPTCHA, rate limit, or anti-bot system.
- Never print passwords, session cookies, or complete verification URLs.
- Never report success without observing the expected next state.

## Required engineering work

1. Normalize the timestamped Python files to the canonical imports:
   `config.py`, `android_automation.py`, `pc_automation.py`, and `main.py`.
2. Keep the duplicate `main_2` implementation out of the runtime path.
3. Use Playwright locator waits and explicit postconditions instead of fixed
   sleeps as readiness checks.
4. Use Android UI Automator text, content descriptions, resource IDs, and live
   node bounds. Do not use hardcoded pixel coordinates.
5. Treat percentage taps as an exceptional, operator-approved fallback only.
   A fallback must be followed by destination-state verification.
6. Implement named Android transitions for email, password, login retry,
   welcome, name, username, source, role, skip, profile, logout, confirmation,
   and app-close states.
7. Save a screenshot and UI XML on every failed Android transition.
8. Make checkpoints atomic and resumable. A later-stage failure must not
   create a new account when resumed.
9. Ensure all browser, Playwright, and subprocess resources are cleaned up in
   `finally` blocks.
10. Add focused tests for link extraction, state transitions, retry limits,
    response validation, and checkpoint recovery.

## Do not do

- Do not rewrite the entire project unnecessarily.
- Do not silently switch the requested `temp-mail.org` flow back to 1secmail.
- Do not add a Kotlin app unless UI-dump evidence proves the Python adapter
  cannot complete the required screens.
- Do not skip a failed stage as if it were successful without recording an
  explicit human takeover decision.

## Acceptance

Run the syntax/import check first. Then perform a manual, one-account smoke
test. Stop before the Android phase unless the operator confirms that the
phone is connected, unlocked, and ready. Report exact failed stage,
transition, evidence path, and observed state.