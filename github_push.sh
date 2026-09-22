#!/usr/bin/env bash
#
# Safe-by-default GitHub workspace sync.
#
# Normal sync mirrors the current workspace's Git tree to a GitHub branch:
# tracked additions, edits, and deletions are pushed. Remote-only files are
# removed because the branch is updated to the local commit tree.
#
# Deleting the entire GitHub repository is a separate, explicit operation:
#   bash github_push.sh --delete-remote-repo \
#     --confirm-delete OWNER/REPOSITORY --yes
#
# This script never prints tokens or credential values.

set -Eeuo pipefail

ROOT="${WORKSPACE_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
REMOTE="${GITHUB_REMOTE:-origin}"
BRANCH="${GITHUB_BRANCH:-}"
COMMIT_MESSAGE="${GITHUB_COMMIT_MESSAGE:-chore: sync workspace to GitHub}"
YES=0
DRY_RUN=0
MODE=""
CONFIRM_DELETE=""

usage() {
  cat <<'EOF'
Usage:
  bash github_push.sh --sync [--yes] [--dry-run]
  bash github_push.sh --delete-remote-repo --confirm-delete OWNER/REPO --yes
  bash github_push.sh --help

Options:
  --sync                    Stage and push the workspace to the GitHub branch.
                            Remote-only files are deleted from that branch.
  --dry-run                 Show the local changes without changing Git state
                            or contacting GitHub.
  --delete-remote-repo      Permanently delete the entire GitHub repository.
                            This cannot be combined with --sync.
  --confirm-delete OWNER/REPO
                            Exact repository name required for repository
                            deletion. Must be used with --delete-remote-repo.
  --remote NAME             Git remote to use (default: origin).
  --branch NAME             Branch to replace (default: current branch).
  --message TEXT            Commit message for a sync commit.
  --yes                     Skip confirmation prompts. Required for deletion
                            modes and recommended for the manual workflow.
  --help                    Show this help.

Environment:
  WORKSPACE_DIR             Workspace root (default: script directory).
  GITHUB_REMOTE             Git remote (default: origin).
  GITHUB_BRANCH             Target branch (default: current branch).
  GITHUB_COMMIT_MESSAGE     Sync commit message.
  GITHUB_COMMIT_NAME        Optional commit author name.
  GITHUB_COMMIT_EMAIL       Optional commit author email.
  GITHUB_PERSONAL_ACCESS_TOKEN, GITHUB_TOKEN, or GH_TOKEN
                            Token used for HTTPS sync and repository deletion.
  gh auth token              Optional fallback for repository deletion.

Examples:
  bash github_push.sh --dry-run --sync
  bash github_push.sh --sync --yes
  bash github_push.sh --delete-remote-repo \
    --confirm-delete TITANICBHAI/afkfarmer --yes
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '%s\n' "$*"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

repo_root_check() {
  require_command git
  [[ -d "$ROOT/.git" ]] || die "Workspace is not a Git repository: $ROOT"
  git -C "$ROOT" rev-parse --show-toplevel >/dev/null 2>&1 \
    || die "Could not resolve the Git root for $ROOT"
}

current_branch() {
  git -C "$ROOT" branch --show-current
}

remote_url() {
  git -C "$ROOT" remote get-url "$REMOTE" 2>/dev/null \
    || die "Git remote '$REMOTE' does not exist."
}

github_repo_from_url() {
  local url="$1"
  local path=""

  case "$url" in
    https://github.com/*/*|http://github.com/*/*)
      path="${url#*github.com/}"
      ;;
    git@github.com:*/*)
      path="${url#git@github.com:}"
      ;;
    ssh://git@github.com/*/*)
      path="${url#ssh://git@github.com/}"
      ;;
    *)
      die "Remote '$REMOTE' is not a GitHub URL: $url"
      ;;
  esac

  path="${path%.git}"
  [[ "$path" == */* && "$path" != */*/* ]] \
    || die "Could not determine OWNER/REPO from GitHub remote."
  printf '%s\n' "$path"
}

confirm_text() {
  local expected="$1"
  local answer

  if (( YES )); then
    return 0
  fi

  printf 'Type exactly "%s" to continue: ' "$expected"
  read -r answer
  [[ "$answer" == "$expected" ]] \
    || die "Confirmation did not match; no remote action was taken."
}

show_sync_preview() {
  local target="$1"
  log "Workspace: $ROOT"
  log "Remote:   $REMOTE ($target)"
  log "Branch:   $BRANCH"
  log
  git -C "$ROOT" status --short
  log
  log "A normal sync stages all unignored workspace changes with git add -A."
  log "The target branch is then replaced with the resulting local commit tree."
  log "Files absent from this workspace are removed from the target branch."
}

github_token() {
  if [[ -n "${GITHUB_PERSONAL_ACCESS_TOKEN:-}" ]]; then
    printf '%s\n' "$GITHUB_PERSONAL_ACCESS_TOKEN"
    return 0
  fi
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    printf '%s\n' "$GITHUB_TOKEN"
    return 0
  fi
  if [[ -n "${GH_TOKEN:-}" ]]; then
    printf '%s\n' "$GH_TOKEN"
    return 0
  fi
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh auth token
    return 0
  fi
  return 1
}

commit_with_identity() {
  local target="$1"
  local configured_name configured_email name email

  configured_name="$(git -C "$ROOT" config --get user.name 2>/dev/null || true)"
  configured_email="$(git -C "$ROOT" config --get user.email 2>/dev/null || true)"
  name="${configured_name:-${GITHUB_COMMIT_NAME:-${target%%/*}}}"
  email="${configured_email:-${GITHUB_COMMIT_EMAIL:-${target%%/*}@users.noreply.github.com}}"

  [[ -n "$name" ]] || die "Could not determine a Git commit author name."
  [[ -n "$email" ]] || die "Could not determine a Git commit author email."

  log "Creating commit as $name <$email>..."
  # Use command-local config so the workflow does not depend on global Git
  # identity and does not rewrite the operator's Git configuration.
  git -C "$ROOT" \
    -c "user.name=$name" \
    -c "user.email=$email" \
    commit -m "$COMMIT_MESSAGE"
}

git_remote_command() {
  local url token
  url="$(remote_url)"
  case "$url" in
    https://github.com/*|http://github.com/*)
      token="$(github_token)" \
        || die "No GitHub token available for HTTPS sync. Set GITHUB_PERSONAL_ACCESS_TOKEN, GITHUB_TOKEN, GH_TOKEN, or authenticate gh."
      # The header is passed only to this Git process and is never printed.
      git -C "$ROOT" -c "http.extraHeader=Authorization: Bearer $token" "$@"
      ;;
    *)
      git -C "$ROOT" "$@"
      ;;
  esac
}

sync_workspace() {
  local url target remote_sha
  url="$(remote_url)"
  target="$(github_repo_from_url "$url")"

  if [[ -z "$BRANCH" ]]; then
    BRANCH="$(current_branch)"
  fi
  [[ -n "$BRANCH" ]] || die "Detached HEAD: pass --branch NAME explicitly."

  show_sync_preview "$target"
  if (( DRY_RUN )); then
    log
    log "Dry run complete. No files were staged, committed, or pushed."
    return 0
  fi

  case "$url" in
    https://github.com/*|http://github.com/*)
      github_token >/dev/null \
        || die "No GitHub token available for HTTPS sync. Set GITHUB_PERSONAL_ACCESS_TOKEN, GITHUB_TOKEN, GH_TOKEN, or authenticate gh."
      ;;
  esac

  confirm_text "SYNC $target/$BRANCH"

  log
  log "Staging the workspace..."
  git -C "$ROOT" add -A -- .

  if git -C "$ROOT" diff --cached --quiet; then
    log "No local file changes need a commit."
  else
    commit_with_identity "$target"
  fi

  log "Refreshing the remote branch lease..."
  git_remote_command fetch --prune "$REMOTE" "$BRANCH"

  if remote_sha="$(git -C "$ROOT" rev-parse --verify "$REMOTE/$BRANCH" 2>/dev/null)"; then
    log "Pushing with force-with-lease; remote-only files will be removed."
    git_remote_command push \
      "--force-with-lease=refs/heads/$BRANCH:$remote_sha" \
      "$REMOTE" "HEAD:refs/heads/$BRANCH"
  else
    log "Remote branch does not exist; creating it from the workspace."
    git_remote_command push "$REMOTE" "HEAD:refs/heads/$BRANCH"
  fi

  log
  log "Workspace sync completed: https://github.com/$target/tree/$BRANCH"
}

delete_remote_repository() {
  local url target token http_code response_file
  [[ -n "$CONFIRM_DELETE" ]] \
    || die "--confirm-delete OWNER/REPO is required for repository deletion."
  [[ "$CONFIRM_DELETE" != */* || "$CONFIRM_DELETE" == */*/* ]] \
    && die "--confirm-delete must be exactly OWNER/REPO."

  url="$(remote_url)"
  target="$(github_repo_from_url "$url")"
  [[ "$CONFIRM_DELETE" == "$target" ]] \
    || die "Confirmation '$CONFIRM_DELETE' does not match remote repository '$target'."

  confirm_text "DELETE $target"
  require_command curl
  token="$(github_token)" \
    || die "No GitHub token available. Set GITHUB_TOKEN/GH_TOKEN or authenticate gh."

  response_file="$(mktemp)"
  trap 'rm -f "$response_file"' RETURN

  log "Deleting GitHub repository $target..."
  http_code="$(
    curl --silent --show-error \
      --output "$response_file" \
      --write-out '%{http_code}' \
      --request DELETE \
      --header "Accept: application/vnd.github+json" \
      --header "Authorization: Bearer $token" \
      --header "X-GitHub-Api-Version: 2022-11-28" \
      "https://api.github.com/repos/$target"
  )"

  if [[ "$http_code" != "204" ]]; then
    log "GitHub returned HTTP $http_code:"
    sed -n '1,8p' "$response_file" >&2
    return 1
  fi
  log "GitHub repository deleted: $target"
}

while (($#)); do
  case "$1" in
    --sync)
      [[ -z "$MODE" ]] || die "Choose only one operation."
      MODE="sync"
      ;;
    --delete-remote-repo)
      [[ -z "$MODE" ]] || die "Choose only one operation."
      MODE="delete"
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    --yes)
      YES=1
      ;;
    --remote)
      (($# >= 2)) || die "--remote requires a value."
      REMOTE="$2"
      shift
      ;;
    --branch)
      (($# >= 2)) || die "--branch requires a value."
      BRANCH="$2"
      shift
      ;;
    --message)
      (($# >= 2)) || die "--message requires a value."
      COMMIT_MESSAGE="$2"
      shift
      ;;
    --confirm-delete)
      (($# >= 2)) || die "--confirm-delete requires OWNER/REPO."
      CONFIRM_DELETE="$2"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1 (use --help)"
      ;;
  esac
  shift
done

[[ -n "$MODE" ]] || {
  usage
  exit 2
}

if (( DRY_RUN )) && [[ "$MODE" != "sync" ]]; then
  die "--dry-run is only available with --sync."
fi

if [[ "$MODE" == "delete" ]] && (( ! YES )); then
  die "Repository deletion requires --yes in addition to exact confirmation."
fi

repo_root_check

case "$MODE" in
  sync)
    sync_workspace
    ;;
  delete)
    delete_remote_repository
    ;;
esac