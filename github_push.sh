#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# github_push.sh — sync local files to TITANICBHAI/afkfarmer via GitHub API
#
# No git binary, no python3 — uses curl + bash + awk + sed only.
# ──────────────────────────────────────────────────────────────────────────────

set -uo pipefail
# NOTE: deliberately no -e so grep "no match" exit codes don't kill the script.

OWNER="TITANICBHAI"
REPO="afkfarmer"
BRANCH="main"
API="https://api.github.com/repos/${OWNER}/${REPO}/contents"
TOKEN="${GITHUB_PERSONAL_ACCESS_TOKEN:-}"

if [[ -z "$TOKEN" ]]; then
  echo "ERROR: GITHUB_PERSONAL_ACCESS_TOKEN is not set." >&2
  exit 1
fi

# ── Extract a JSON string field (handles spaces around colon, multi-line) ────
# Usage: json_str FIELD <<< "$json"
json_str() {
  local field="$1"
  # Match: "field" ... : ... "value"  (value may have backslash-escaped chars)
  sed -n "s/.*\"${field}\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" \
    | head -1
}

# ── Build a JSON PUT body without printf (handles huge strings) ───────────────
# Writes the body to a temp file to avoid shell arg-length limits.
make_body() {
  local msg="$1" content="$2" sha="$3" branch="$4"
  local tmp; tmp=$(mktemp /tmp/gh_body_XXXXXX.json)
  {
    printf '{"message":"%s","content":"%s","branch":"%s"' \
           "$msg" "$content" "$branch"
    if [[ -n "$sha" ]]; then
      printf ',"sha":"%s"' "$sha"
    fi
    printf '}'
  } > "$tmp"
  echo "$tmp"
}

# ── Push a single file ───────────────────────────────────────────────────────
push_file() {
  local local_path="$1"
  local repo_path="$2"
  local message="$3"

  if [[ ! -f "$local_path" ]]; then
    echo "  skip: $local_path not found"
    return 0
  fi

  local content
  content=$(base64 -w0 < "$local_path" 2>/dev/null || base64 < "$local_path")

  # Fetch current SHA (needed for updates)
  local existing sha
  existing=$(curl -s -H "Authorization: token $TOKEN" \
                  "${API}/${repo_path}?ref=${BRANCH}" 2>/dev/null || true)
  sha=$(echo "$existing" | json_str sha || true)

  # Write body to temp file (avoids shell limits for large files)
  local body_file
  body_file=$(make_body "$message" "$content" "$sha" "$BRANCH")

  local result
  result=$(curl -s -X PUT \
    -H "Authorization: token $TOKEN" \
    -H "Content-Type: application/json" \
    --data-binary "@${body_file}" \
    "${API}/${repo_path}" 2>/dev/null || true)

  rm -f "$body_file"

  if echo "$result" | grep -q '"html_url"'; then
    echo "  ✓ $repo_path"
  else
    local errmsg
    errmsg=$(echo "$result" | json_str message || true)
    echo "  ✗ $repo_path — ${errmsg:-unknown error}"
  fi
}

# ── Quick remote SHA lookup for diff summary ──────────────────────────────────
remote_sha() {
  local f="$1"
  curl -s -H "Authorization: token $TOKEN" \
       "${API}/${f}?ref=${BRANCH}" 2>/dev/null \
  | json_str sha || true
}

# ── Diff summary ─────────────────────────────────────────────────────────────
diff_summary() {
  echo ""
  echo "── Local vs GitHub diff ─────────────────────────────────────────────"
  for f in mc_farm.sh github_push.sh README.md; do
    [[ -f "$f" ]] || continue
    local rsha
    rsha=$(remote_sha "$f" || true)
    if [[ -z "$rsha" ]]; then
      echo "  $f : NEW FILE"
    else
      echo "  $f : exists on remote (sha=${rsha:0:8}…)"
    fi
  done
  echo "──────────────────────────────────────────────────────────────────────"
  echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Pushing to https://github.com/${OWNER}/${REPO}"

diff_summary

COMMIT_MSG="chore: sync $(date -u +%Y-%m-%dT%H:%M:%SZ)"

push_file "mc_farm.sh"      "mc_farm.sh"      "$COMMIT_MSG"
push_file "github_push.sh"  "github_push.sh"  "$COMMIT_MSG"
push_file "README.md"       "README.md"       "$COMMIT_MSG"

echo ""
echo "Done → https://github.com/${OWNER}/${REPO}"
