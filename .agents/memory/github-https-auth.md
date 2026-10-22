---
name: GitHub HTTPS authentication
description: Environment-specific behavior for authenticated GitHub Git operations from this workspace
---

GitHub Git HTTPS operations in this workspace must use the Replit secret as a
non-interactive Basic-auth header, with `x-access-token` as the username and
the token as the password. Disable Git credential helpers and `core.askPass`
for the command so a rejected header fails directly instead of opening the
Replit askpass prompt.

**Why:** The previous Bearer-header transport passed the token-availability
check, but GitHub rejected it for the Git endpoint and Git then attempted the
interactive `replit-git-askpass` flow. A read-only API check confirmed the
configured token is valid and has push permission for the target repository.

**How to apply:** Keep `--check-auth` read-only and use it before a deliberate
branch-replacing sync. Never print the token or its encoded form, and do not
assume a successful token lookup proves that a remote push is authorized.

**Mirror policy:** The user wants the GitHub repository to mirror the local
project, including project additions, edits, deletions, and reference assets.
Do not let system-wide ignore rules silently omit project-owned assets; keep
runtime caches, dependencies, and secrets excluded.

**Why:** The user explicitly said the GitHub repository is a mirror of the
workspace, and the project references files in `attached_assets/`.

**How to apply:** Force-stage `attached_assets/` during sync when it exists,
while continuing to respect ignore rules for generated and private runtime data.