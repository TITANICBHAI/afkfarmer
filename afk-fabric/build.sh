#!/usr/bin/env bash
# ── AFKVerify Fabric 1.20.6 build script ─────────────────────────────────
# Requires: Java 21+, internet access (downloads Gradle + MC dependencies)
# Output:   build/libs/afkverify-fabric-1.0.0.jar
# Usage:
#   cd afk-fabric
#   bash build.sh
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "── Checking Java ────────────────────────────────────────────────────"
java -version 2>&1 | head -1

GRADLE_VERSION="8.8"
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
echo "── Building AFKVerify Fabric mod ────────────────────────────────────"
echo "  (First run downloads MC + Fabric deps — may take 2-5 minutes)"
"$GRADLE_BIN" build --stacktrace

echo ""
echo "── Done! ────────────────────────────────────────────────────────────"
JAR=$(ls "$ROOT/build/libs/"*-1.0.0.jar 2>/dev/null | grep -v sources | head -1)
if [ -n "$JAR" ]; then
    echo "  Output: $JAR"
    echo "  Drop it into your server's mods/ folder (Fabric 1.20.6)."
else
    echo "  Build succeeded but JAR location unexpected — check build/libs/"
fi
