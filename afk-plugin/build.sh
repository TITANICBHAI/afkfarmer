#!/usr/bin/env bash
# ── AFKVerify plugin build script ────────────────────────────────────────
# Downloads Paper API (compile-time only), compiles the plugin, packages .jar
# No Maven/Gradle needed — pure javac + jar.
#
# Usage:
#   cd afk-plugin
#   bash build.sh
#
# Output: AFKVerify.jar  (drop into server/plugins/)
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC="$ROOT/src/main/java"
RES="$ROOT/src/main/resources"
BUILD="$ROOT/build"
LIBS="$ROOT/libs"

API_JAR="$LIBS/paper-api.jar"
API_URL="https://repo.papermc.io/repository/maven-public/io/papermc/paper/paper-api/1.20.4-R0.1-SNAPSHOT/paper-api-1.20.4-R0.1-SNAPSHOT-20231217.192524-23.jar"

mkdir -p "$BUILD/classes" "$LIBS"

echo "── Checking Java ────────────────────────────────────────────────────"
java -version 2>&1 | head -1
javac -version 2>&1

echo ""
echo "── Fetching Paper API (compile-time only, ~2 MB) ────────────────────"
if [ ! -f "$API_JAR" ]; then
    echo "  Downloading from PaperMC repo..."
    # Try Paper API first, then fall back to Spigot API
    if ! curl -sSL --fail "$API_URL" -o "$API_JAR"; then
        echo "  Paper API unavailable, trying Spigot..."
        SPIGOT_URL="https://hub.spigotmc.org/nexus/content/repositories/snapshots/org/spigotmc/spigot-api/1.20.4-R0.1-SNAPSHOT/spigot-api-1.20.4-R0.1-SNAPSHOT.jar"
        curl -sSL "$SPIGOT_URL" -o "$API_JAR"
    fi
    echo "  Downloaded: $(du -sh "$API_JAR" | cut -f1)"
else
    echo "  Cached: $API_JAR"
fi

echo ""
echo "── Compiling Java source ────────────────────────────────────────────"
javac -encoding UTF-8 -source 8 -target 8 \
      -cp "$API_JAR" \
      -d "$BUILD/classes" \
      $(find "$SRC" -name "*.java")
echo "  Compiled OK"

echo ""
echo "── Copying resources ────────────────────────────────────────────────"
cp "$RES/plugin.yml" "$BUILD/classes/"
cp "$RES/config.yml" "$BUILD/classes/"

echo ""
echo "── Packaging JAR ────────────────────────────────────────────────────"
JAR_OUT="$ROOT/AFKVerify.jar"
jar cf "$JAR_OUT" -C "$BUILD/classes" .
echo "  Created: $JAR_OUT  ($(du -sh "$JAR_OUT" | cut -f1))"

echo ""
echo "── Done! ────────────────────────────────────────────────────────────"
echo "  Drop AFKVerify.jar into your server's plugins/ folder."
echo "  Requires: Spigot or Paper 1.13+"
echo "  Use /afkverify test in-game to trigger the popup immediately."
