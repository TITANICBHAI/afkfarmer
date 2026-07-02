#!/usr/bin/env bash
# ── AFKVerify Forge 1.16.5 build script ──────────────────────────────────
# Requires: Java 8 or 11, internet access (downloads Gradle + Forge deps)
# Output:   build/libs/afkverify-forge-1.0.0.jar  (reobfuscated)
# Usage:
#   cd afk-forge
#   bash build.sh
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "── Checking Java ────────────────────────────────────────────────────"
java -version 2>&1 | head -1

GRADLE_VERSION="7.6.4"
GRADLE_DIR="$ROOT/.gradle-wrapper"
GRADLE_BIN="$GRADLE_DIR/gradle-${GRADLE_VERSION}/bin/gradle"

if [ ! -f "$GRADLE_BIN" ]; then
    echo ""
    echo "── Downloading Gradle ${GRADLE_VERSION} ─────────────────────────────────────"
    mkdir -p "$GRADLE_DIR"
    GRADLE_ZIP="$GRADLE_DIR/gradle-${GRADLE_VERSION}-bin.zip"
    curl -sSL \
        "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" \
        -o "$GRADLE_ZIP"
    unzip -q "$GRADLE_ZIP" -d "$GRADLE_DIR"
    echo "  Gradle extracted."
fi

echo ""
echo "── Building AFKVerify Forge mod ────────────────────────────────────"
echo "  (First run downloads Forge + MC — may take 5-10 minutes)"
"$GRADLE_BIN" build --stacktrace

echo ""
echo "── Done! ────────────────────────────────────────────────────────────"
JAR=$(ls "$ROOT/build/libs/"afkverify-forge-*.jar 2>/dev/null | grep -v sources | head -1)
if [ -n "$JAR" ]; then
    echo "  Output: $JAR"
    echo "  Drop it into your server's mods/ folder (Forge 1.16.5)."
else
    echo "  Build finished — check build/libs/"
fi
