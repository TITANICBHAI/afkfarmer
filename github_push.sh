#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# github_push.sh — sync local files to TITANICBHAI/afkfarmer via GitHub API
#
# No git binary, no python3 — uses curl + bash + awk + sed + sha1sum only.
# Speed: fetches the entire remote tree in ONE call, skips unchanged files.
# ──────────────────────────────────────────────────────────────────────────────

set -uo pipefail

OWNER="TITANICBHAI"
REPO="afkfarmer"
BRANCH="main"
API="https://api.github.com/repos/${OWNER}/${REPO}"
TOKEN="${GITHUB_PERSONAL_ACCESS_TOKEN:-}"

if [[ -z "$TOKEN" ]]; then
  echo "ERROR: GITHUB_PERSONAL_ACCESS_TOKEN is not set." >&2
  exit 1
fi

# ── Git blob SHA (same algorithm GitHub uses for file SHAs) ──────────────────
# sha1("blob <size>\0<content>")
blob_sha() {
  local f="$1"
  local size
  size=$(wc -c < "$f")
  { printf "blob %d\0" "$size"; cat "$f"; } | sha1sum | cut -d' ' -f1
}

# ── Extract a JSON string field ───────────────────────────────────────────────
json_str() {
  local field="$1"
  sed -n "s/.*\"${field}\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -1
}

# ── Build JSON PUT body into a temp file ─────────────────────────────────────
make_body() {
  local msg="$1" content="$2" sha="$3" branch="$4"
  local tmp; tmp=$(mktemp /tmp/gh_body_XXXXXX.json)
  {
    printf '{"message":"%s","content":"%s","branch":"%s"' "$msg" "$content" "$branch"
    [[ -n "$sha" ]] && printf ',"sha":"%s"' "$sha"
    printf '}'
  } > "$tmp"
  echo "$tmp"
}

# ── Push a single file — returns 0 on success, 1 on API error ────────────────
push_file() {
  local local_path="$1" repo_path="$2" message="$3" remote_sha="$4"
  local content body_file result errmsg

  content=$(base64 -w0 < "$local_path" 2>/dev/null || base64 < "$local_path")
  body_file=$(make_body "$message" "$content" "$remote_sha" "$BRANCH")

  result=$(curl -s -X PUT \
    -H "Authorization: token $TOKEN" \
    -H "Content-Type: application/json" \
    --data-binary "@${body_file}" \
    "${API}/contents/${repo_path}" 2>/dev/null || true)

  rm -f "$body_file"

  if echo "$result" | grep -q '"html_url"'; then
    echo "  ✓ $repo_path"
    return 0
  else
    errmsg=$(echo "$result" | json_str message || true)
    echo "  ✗ $repo_path — ${errmsg:-unknown error}"
    return 1
  fi
}

# ── All files to sync ─────────────────────────────────────────────────────────
# Format: "local_path" "remote_path_in_repo"
declare -a SYNC_FILES=(
  "mc_farm.sh"                                                                        "mc_farm.sh"
  "github_push.sh"                                                                    "github_push.sh"
  "join_training_data.py"                                                             "join_training_data.py"
  "README.md"                                                                         "README.md"

  # ── Spigot/Paper plugin ───────────────────────────────────────────────────────
  "afk-plugin/src/main/java/com/afkverify/AFKVerifyPlugin.java"   "afk-plugin/src/main/java/com/afkverify/AFKVerifyPlugin.java"
  "afk-plugin/src/main/resources/plugin.yml"                      "afk-plugin/src/main/resources/plugin.yml"
  "afk-plugin/src/main/resources/config.yml"                      "afk-plugin/src/main/resources/config.yml"
  "afk-plugin/build.sh"                                           "afk-plugin/build.sh"
  "afk-plugin/README.md"                                          "afk-plugin/README.md"
  "afk-plugin/.github/workflows/build.yml"                        ".github/workflows/afkverify-build.yml"
  ".github/workflows/build-fabric.yml"                            ".github/workflows/build-fabric.yml"
  ".github/workflows/build-forge.yml"                             ".github/workflows/build-forge.yml"
  ".github/workflows/release.yml"                                 ".github/workflows/release.yml"

  # ── Fabric mod (1.20.6) ───────────────────────────────────────────────────────
  "afk-fabric/build.gradle"                                        "afk-fabric/build.gradle"
  "afk-fabric/gradle.properties"                                   "afk-fabric/gradle.properties"
  "afk-fabric/settings.gradle"                                     "afk-fabric/settings.gradle"
  "afk-fabric/build.sh"                                           "afk-fabric/build.sh"
  "afk-fabric/src/main/resources/fabric.mod.json"                 "afk-fabric/src/main/resources/fabric.mod.json"
  "afk-fabric/src/main/java/com/afkverify/AFKVerifyCommand.java"  "afk-fabric/src/main/java/com/afkverify/AFKVerifyCommand.java"
  "afk-fabric/src/main/java/com/afkverify/AFKScreenHandler.java"  "afk-fabric/src/main/java/com/afkverify/AFKScreenHandler.java"
  "afk-fabric/src/main/java/com/afkverify/AFKPlayerTracker.java"  "afk-fabric/src/main/java/com/afkverify/AFKPlayerTracker.java"
  "afk-fabric/src/main/java/com/afkverify/AFKVerifyMod.java"      "afk-fabric/src/main/java/com/afkverify/AFKVerifyMod.java"

  # ── Forge mod (1.16.5) ────────────────────────────────────────────────────────
  "afk-forge/build.gradle"                                         "afk-forge/build.gradle"
  "afk-forge/gradle.properties"                                    "afk-forge/gradle.properties"
  "afk-forge/settings.gradle"                                      "afk-forge/settings.gradle"
  "afk-forge/build.sh"                                            "afk-forge/build.sh"
  "afk-forge/src/main/resources/META-INF/mods.toml"               "afk-forge/src/main/resources/META-INF/mods.toml"
  "afk-forge/src/main/resources/pack.mcmeta"                      "afk-forge/src/main/resources/pack.mcmeta"
  "afk-forge/src/main/java/com/afkverify/AFKVerifyCommand.java"   "afk-forge/src/main/java/com/afkverify/AFKVerifyCommand.java"
  "afk-forge/src/main/java/com/afkverify/AFKContainer.java"       "afk-forge/src/main/java/com/afkverify/AFKContainer.java"
  "afk-forge/src/main/java/com/afkverify/AFKPlayerTracker.java"   "afk-forge/src/main/java/com/afkverify/AFKPlayerTracker.java"
  "afk-forge/src/main/java/com/afkverify/AFKVerifyMod.java"       "afk-forge/src/main/java/com/afkverify/AFKVerifyMod.java"

  "replit.md"                                                                         "replit.md"
  "SHELL_SOLVER.md"                                                                   "SHELL_SOLVER.md"
)

# ── Main ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "→ https://github.com/${OWNER}/${REPO}"

# Fetch entire remote tree in ONE call (avoids N separate SHA lookups)
TREE_JSON=$(curl -s \
  -H "Authorization: token $TOKEN" \
  "${API}/git/trees/${BRANCH}?recursive=1" 2>/dev/null || true)

# Build a lookup: path→sha from the flat tree array.
# Uses python3 (guaranteed present) to parse the GitHub JSON response.
# Previous gawk 3-arg match() approach failed on systems with mawk/nawk
# (Ubuntu default) — those lack the GNU awk extension.
declare -A REMOTE_SHA
while IFS=' ' read -r _path _sha; do
  REMOTE_SHA["$_path"]="$_sha"
done < <(printf '%s' "$TREE_JSON" | python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    for item in data.get('tree', []):
        if item.get('type') == 'blob':
            p = item.get('path', '')
            s = item.get('sha',  '')
            if p and s:
                print(p, s)
except Exception as e:
    import sys as _s; print('# tree_parse_error:', e, file=_s.stderr)
")

# Sanity check: if the map is empty the tree fetch or parse failed entirely.
# Fall back to pushing everything rather than silently skipping all files.
_tree_count=${#REMOTE_SHA[@]}
if [[ $_tree_count -eq 0 ]]; then
  echo "  ⚠ tree parse returned 0 blobs — SHA skipping disabled (will push all)"
fi

COMMIT_MSG="chore: sync $(date -u +%Y-%m-%dT%H:%M:%SZ)"
pushed=0 failed=0 skipped=0

# ── helper: push one path pair, update counters ───────────────────────────────
_sync_one() {
  local lf="$1" rf="$2"
  [[ -f "$lf" ]] || return

  local local_sha remote
  local_sha=$(blob_sha "$lf")
  remote="${REMOTE_SHA[$rf]:-}"

  # Skip only when tree parse succeeded (map non-empty) and SHAs match
  if [[ $_tree_count -gt 0 && "$local_sha" == "$remote" ]]; then
    (( skipped++ )) || true
    return
  fi

  if push_file "$lf" "$rf" "$COMMIT_MSG" "$remote"; then
    (( pushed++ )) || true
  else
    (( failed++ )) || true
  fi
}

# ── Sync declared files ───────────────────────────────────────────────────────
for ((i=0; i<${#SYNC_FILES[@]}; i+=2)); do
  _sync_one "${SYNC_FILES[i]}" "${SYNC_FILES[i+1]}"
done

# ── Sync attached_assets/ ─────────────────────────────────────────────────────
for _f in attached_assets/*.png attached_assets/*.jsonl attached_assets/*.json; do
  _sync_one "$_f" "$_f"
done

echo "Done — pushed $pushed, failed $failed, skipped $skipped unchanged"
