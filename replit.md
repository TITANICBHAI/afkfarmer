# [Project name]

_Replace the heading above with the project's name, and this line with one sentence describing what this app does for users._

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

_Populate as you build — short repo map plus pointers to the source-of-truth file for DB schema, API contracts, theme files, etc._

## Architecture decisions

_Populate as you build — non-obvious choices a reader couldn't infer from the code (3-5 bullets)._

## Product

_Describe the high-level user-facing capabilities of this app once they exist._

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

- Treat the screenshot assets in `attached_assets/` as visual references for future automation-step descriptions, even when the user does not repeat that instruction.

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

- Run the PC browser flow with `bash run_pc_automation.sh`; the wrapper adds
  the Nix C++ library path required by Playwright's Python runtime.
- The workspace has Chromium rather than Microsoft Edge, so browser setup
  explicitly falls back to Chromium. Temporary-mail provider Cloudflare
  blocks are reported and are not bypassed.

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
