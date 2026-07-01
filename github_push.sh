#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# github_push.sh — sync local files to TITANICBHAI/afkfarmer via GitHub API
#
# Usage:
#   bash github_push.sh                          # uses $GITHUB_PERSONAL_ACCESS_TOKEN
#   GITHUB_PERSONAL_ACCESS_TOKEN=ghp_... bash github_push.sh
#
# No git binary required — uses curl + GitHub Contents API.
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

OWNER="TITANICBHAI"
REPO="afkfarmer"
BRANCH="main"
API="https://api.github.com/repos/${OWNER}/${REPO}/contents"
TOKEN="${GITHUB_PERSONAL_ACCESS_TOKEN:-}"

if [[ -z "$TOKEN" ]]; then
  echo "ERROR: GITHUB_PERSONAL_ACCESS_TOKEN is not set." >&2
  exit 1
fi

# ── Push a single file ───────────────────────────────────────────────────────
push_file() {
  local local_path="$1"   # path on disk (relative to script dir)
  local repo_path="$2"    # path inside the GitHub repo
  local message="$3"

  if [[ ! -f "$local_path" ]]; then
    echo "  skip: $local_path not found"
    return
  fi

  # base64-encode the content (cross-platform: use -w0 to suppress line wraps)
  local content
  content=$(base64 -w0 < "$local_path" 2>/dev/null || base64 < "$local_path")

  # Fetch current SHA (needed for updates; empty for new files)
  local sha=""
  local existing
  existing=$(curl -s -H "Authorization: token $TOKEN" \
                  "${API}/${repo_path}?ref=${BRANCH}")
  sha=$(echo "$existing" | grep -o '"sha":"[^"]*"' | head -1 | cut -d'"' -f4)

  # Build JSON body
  local body
  if [[ -n "$sha" ]]; then
    body=$(printf '{"message":"%s","content":"%s","sha":"%s","branch":"%s"}' \
                  "$message" "$content" "$sha" "$BRANCH")
  else
    body=$(printf '{"message":"%s","content":"%s","branch":"%s"}' \
                  "$message" "$content" "$BRANCH")
  fi

  local result
  result=$(curl -s -X PUT \
    -H "Authorization: token $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$body" \
    "${API}/${repo_path}")

  if echo "$result" | grep -q '"html_url"'; then
    echo "  ✓ $repo_path"
  else
    echo "  ✗ $repo_path — $(echo "$result" | grep -o '"message":"[^"]*"' | head -1)"
  fi
}

# ── Compute diff summary (shown before push) ─────────────────────────────────
diff_summary() {
  echo ""
  echo "── Local vs GitHub diff ─────────────────────────────────────────────"
  for f in mc_farm.sh github_push.sh README.md; do
    [[ -f "$f" ]] || continue
    remote=$(curl -s -H "Authorization: token $TOKEN" \
                  "${API}/${f}?ref=${BRANCH}" \
             | grep -o '"content":"[^"]*"' | head -1 | cut -d'"' -f4 \
             | tr -d '\n' | base64 -d 2>/dev/null || echo "")
    if [[ -z "$remote" ]]; then
      echo "  $f : NEW FILE"
    else
      local_md5=$(md5sum "$f" 2>/dev/null | cut -d' ' -f1)
      remote_md5=$(echo "$remote" | md5sum 2>/dev/null | cut -d' ' -f1)
      if [[ "$local_md5" == "$remote_md5" ]]; then
        echo "  $f : unchanged"
      else
        lines_added=$(diff <(echo "$remote") "$f" 2>/dev/null | grep -c '^>' || true)
        lines_removed=$(diff <(echo "$remote") "$f" 2>/dev/null | grep -c '^<' || true)
        echo "  $f : +${lines_added} / -${lines_removed} lines"
      fi
    fi
  done
  echo "──────────────────────────────────────────────────────────────────────"
  echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Pushing to https://github.com/${OWNER}/${REPO}"
echo ""

diff_summary

COMMIT_MSG="chore: sync $(date -u +%Y-%m-%dT%H:%M:%SZ)"

push_file "mc_farm.sh"      "mc_farm.sh"      "$COMMIT_MSG"
push_file "github_push.sh"  "github_push.sh"  "$COMMIT_MSG"
push_file "README.md"       "README.md"       "$COMMIT_MSG"

echo ""
echo "Done → https://github.com/${OWNER}/${REPO}"
