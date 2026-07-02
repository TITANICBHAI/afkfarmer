# afkfarmer

A Minecraft AFK farm automation suite for JartexNetwork OneBlock server.

## Project Overview

This project automates detection and solving of periodic "Afk Grinding" verification popups in Minecraft. It includes:

- **`mc_farm.sh`** — Main automation script (Bash + embedded Python). Requires a running Minecraft window, `xdotool`, and `scrot` on a Linux desktop.
- **`join_training_data.py`** — Log analyzer that correlates script attempts with server ground-truth to calibrate detection accuracy.
- **`afk-fabric/`** — Fabric mod (Java 21, Gradle) that replicates the verification GUI server-side.
- **`afk-forge/`** — Forge mod (Java 8, Gradle) — same purpose.
- **`afk-plugin/`** — Spigot/Paper plugin (Java, manual build via `build.sh`).
- **`github_push.sh`** — Push changes to GitHub via the Contents API (no git binary needed).

## Usage

The main script is designed to run on a Linux desktop with Minecraft open:

```bash
# Optional: set Anthropic API key for AI tooltip detection
export ANTHROPIC_API_KEY="sk-ant-..."

# Run the farmer (second run while active will stop it)
bash mc_farm.sh
```

## Detection Backends (in order)

1. **AI (Claude via Anthropic API)** — most accurate; set `ANTHROPIC_API_KEY`
2. **Tesseract OCR** — local fallback
3. **Color/HSV pixel scan** — stdlib-only fallback, no dependencies

## System Requirements

- Linux with a display (X11)
- `xdotool` — mouse/keyboard automation
- `scrot` — screenshot tool
- `tesseract-ocr` — optional OCR fallback
- Java 21 (Fabric mod) / Java 8 (Forge mod)

## Note on Replit Environment

This project is designed to run on a Linux desktop alongside Minecraft. The automation script (`mc_farm.sh`) requires an X11 display and a running Minecraft game window, which are not available in the Replit cloud environment. Use Replit for code editing, version control, and running the data analysis script (`join_training_data.py`).

## User preferences

- Follow existing project conventions (Bash + Python stdlib, no unnecessary dependencies)
