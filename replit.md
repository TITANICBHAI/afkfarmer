# Onboarding Flow Automation

Python Playwright and ADB automation for the operator-owned Replit web and Android onboarding flow.

## Run & Operate

- `bash run_pc_automation.sh --check-runtime` — verify the Playwright runtime
- `python preflight.py` — run the non-destructive readiness check
- `python -m unittest -v` — run the offline test suite
- `bash run_pc_automation.sh` — start the full operator flow

## Stack

- Python 3.8+, Playwright, requests, colorama
- PC browser flow: Playwright with an optional CDP attachment
- Android flow: ADB and UI hierarchy inspection

## Where things live

- `pc_automation.py` — browser setup, tab reuse, registration, verification, and import
- `temp_mail.py` — visible temporary-mail tab flow
- `android_automation.py` — Android UI transitions and evidence
- `main.py` — checkpointed stage orchestration
- `STEPS.md` and `attached_assets/` — supplied flow and visual references

## Architecture decisions

- Attach to `PLAYWRIGHT_CDP_URL` first so an operator's existing browser and tabs can be reused.
- Never close a browser attached over CDP; only managed browser instances are closed.
- Registration submission is scoped to the modal/form and waits for an enabled primary button.
- CAPTCHA and provider blocks remain manual failure/takeover states.

## Product

- Runs the supplied one-account Replit web/Android onboarding sequence with checkpoints and failure evidence.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

- Treat the screenshot assets in `attached_assets/` as visual references for future automation-step descriptions, even when the user does not repeat that instruction.

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

- Run the PC browser flow with `bash run_pc_automation.sh`; the wrapper adds
  the Nix C++ library path required by Playwright's Python runtime.
- Start an existing Chromium-family browser with remote debugging to reuse its
  Replit and temp-mail tabs. `PLAYWRIGHT_CDP_URL` is supported explicitly, and
  local CDP ports 9222-9225 are discovered automatically when it is omitted.
  Without CDP, Playwright cannot attach to a normal already-running browser
  process.
- Browser setup detects configured or installed Edge, Chrome, Brave, and
  Chromium executables before trying a managed Playwright browser. Temporary-
  mail provider Cloudflare blocks are reported and are not bypassed.

## GitHub workspace sync

- `bash github_push.sh --dry-run --sync` — inspect the workspace sync without
  staging, committing, or contacting GitHub.
- `bash github_push.sh --sync --yes` — commit local changes and mirror the
  workspace to the configured GitHub branch. Remote-only files on that branch
  are removed.
- If the workflow has no Git identity configured, the script uses
  `GITHUB_COMMIT_NAME`/`GITHUB_COMMIT_EMAIL` when supplied, otherwise a
  repository-owner `users.noreply.github.com` identity for that commit only.
  It does not modify global Git configuration.
- `bash github_push.sh --delete-remote-repo --confirm-delete OWNER/REPO --yes`
  — permanently delete the entire GitHub repository. Use only when that
  destructive action is intentional.

The sync uses the existing `origin` remote and current branch by default.
HTTPS sync and repository deletion use `GITHUB_PERSONAL_ACCESS_TOKEN`,
`GITHUB_TOKEN`, `GH_TOKEN`, or an authenticated `gh` CLI session. The workflow
is manual and is not auto-started.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
