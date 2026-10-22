# Agent Start Prompt

Copy and give this prompt to any new coding agent before it starts work on this
repository.

---

You are joining an existing repository with zero conversation context. Do not
start editing from assumptions. Your first responsibility is to understand the
project and keep its progress record accurate.

## Mandatory startup sequence

Before changing code:

1. Inspect the repository root and identify the active source files.
2. Read `README.md`.
3. Read `PROGRESS_TRACKER.md` completely. This file is mandatory and is the
   source of truth for completed work, remaining work, blockers, and next
   actions.
4. Read `CORE_FLOW.md` completely. It defines the required user flow.
5. Read `PLAN.md`, `IMPLEMENTATION.md`, and `ARCHITECTURE.md`.
6. Read `AUTOMATION_PROMPT.md` when implementing the automation itself.
7. Review the relevant files in `attached_assets/`, especially the numbered
   screenshots, when working on a screen or step.
8. Search the codebase before assuming a file, function, dependency, or
   convention exists.

Do not begin implementation until you can state:

- what the product does;
- which phase the tracker says is active;
- what the next unchecked item is;
- what files are relevant;
- what evidence will prove the item complete.

## Mandatory tracker protocol

`PROGRESS_TRACKER.md` must be updated during the work, not only at the end.

- Read every existing checkbox before starting.
- Add or refine checklist items if the work reveals missing scope.
- Mark an item `[x]` only after the implementation and its verification are
  complete.
- Never mark work complete because code was written alone.
- Record the command, test, screenshot, log, or other evidence beside the
  completed item when useful.
- If blocked, leave the item unchecked and record the exact blocker and next
  action.
- Update `Current phase`, `Next action`, `Blockers`, and `Last updated`.
- Do not delete unfinished work or rewrite history to make progress look better.
- If the user changes the scope, update the tracker before implementing the
  new scope.

At the end of every work session, the tracker must tell the next agent exactly
what is done, what is not done, and what to do next.

## Project goal

Build a local, operator-driven workflow for one account on the operator's own
PC and Android device:

1. Obtain the temporary email through the requested `temp-mail.org` flow.
2. Register on Replit through Microsoft Edge.
3. Complete email verification through the visible mailbox flow.
4. Complete the Replit Android login and onboarding flow through verified UI
   states.
5. Log out and force-stop the Android app.
6. Resume the PC session and import the operator's GitHub repository.

The exact sequence is in `CORE_FLOW.md`. The screenshot assets are visual
references, not optional decoration.

## Non-negotiable boundaries

- One account per run.
- Operator-owned devices only.
- CAPTCHA and anti-bot challenges are always manual.
- Never solve, bypass, or evade a CAPTCHA, rate limit, or anti-bot system.
- Never harvest credentials or interact with unrelated accounts.
- Never print passwords, session cookies, or complete verification URLs in
  ordinary logs.
- Never report success without observing the expected postcondition.
- Do not start a real external-account or device run without the operator's
  explicit confirmation that the environment is ready.

## Engineering expectations

- Preserve the existing structure unless the tracker and architecture justify
  a change.
- Use the canonical Python modules: `config.py`,
  `android_automation.py`, `pc_automation.py`, and `main.py`.
- Use explicit Playwright waits and postconditions instead of fixed sleeps as
  readiness checks.
- Use Android UI Automator text, content descriptions, resource IDs, and live
  node bounds before any coordinate fallback.
- A coordinate fallback must be explicit, logged, bounded, and followed by
  destination-state verification.
- A missing element or malformed UI dump is a failure, not a successful tap.
- Save screenshot and UI XML evidence for Android failures.
- Make checkpoints atomic and resumable.
- Guarantee browser, Playwright, and subprocess cleanup in `finally` blocks.
- Keep the Kotlin companion out of scope unless device evidence proves the
  Python ADB/UI Automator layer cannot observe or operate a required state.

## Work protocol

For each implementation item:

1. Explain the intended change briefly in the tracker.
2. Inspect only the relevant code and assets.
3. Make the smallest coherent change.
4. Run the cheapest useful verification immediately.
5. Update the tracker with the result and evidence.
6. Continue to the next item only when the current item is genuinely complete.

If requirements conflict, stop and record the conflict in `PROGRESS_TRACKER.md`
instead of silently choosing a behavior. If a safer interpretation is obvious,
document the decision before proceeding.

## Final response requirements

Before declaring work complete:

- Re-read the user's request.
- Confirm every requested behavior against the code and tracker.
- Run the appropriate syntax, type, unit, or smoke checks.
- Update every affected tracker item.
- Report completed work, remaining unchecked items, blockers, verification
  evidence, and the safest next action.

Never claim that the full automation works merely because the files compile.