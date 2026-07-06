# afkfarmer

Minecraft AFK farm automation suite for **JartexNetwork OneBlock** server.

One command starts everything — the grinding loop and the AFK-popup solver run
together automatically. Run the same command again to stop both cleanly.

---

## One-time setup

```bash
# 1. System tools
sudo apt update
sudo apt install -y xdotool

# 2. Python dependencies
pip install mss opencv-python numpy pyautogui

# 3. Make the script executable
chmod +x mc_spam_2.sh
```

That's it. You never need to touch `mc_afk_solver.py` directly.

---

## Usage — single command

```bash
# START — launches the grinder + AFK solver together
bash mc_spam_2.sh

# STOP — run the same command again while it's active
bash mc_spam_2.sh
```

What happens when you start:

1. Dependency check runs (exits with a clear error if anything is missing).
2. `mc_afk_solver.py` is launched in the background — it begins watching
   the screen for the *"Afk Grinding"* popup immediately.
3. The grinding loop starts attacking, drifting the camera, and jumping.
4. When a popup appears the solver pauses the grinder, clicks the right slot,
   then resumes the grinder — you never see it.

What happens when you stop:

1. The grinding flag file is removed — the attack loop exits on its next tick.
2. The solver process is killed using the stored PID.
3. Both log files stay on disk for review.

---

## Logs

| File | Contents |
|---|---|
| `/tmp/mc_spam_2.log` | Grinder start/stop events and session timestamps |
| `/tmp/mc_afk_solver.log` | Solver detections, slot scan results, click confirmations |
| `/tmp/mc_afk_captures/` | Per-run tooltip crop images (archived after each solve) |

Watch both live:

```bash
tail -f /tmp/mc_spam_2.log /tmp/mc_afk_solver.log
```

---

## First-run verification (optional but recommended)

Before going fully live, you can verify that the solver is reading your screen
correctly without it clicking anything:

```bash
# Step 1 — print the auto-calibrated grid geometry
python3 mc_afk_solver.py --calibrate-only
```

This scans every screenshot in `attached_assets/` and prints the derived
screen coordinates for all 27 inventory slots. Compare them against what you
see on screen.

```bash
# Step 2 — dry run: wait for a real popup, scan all slots, print a report
python3 mc_afk_solver.py --dry-run --timeout 120
```

Sample report:

```
────────────────────  DRY-RUN SCAN REPORT  ────────────────────
  Slot   Green     Red   Conf  Verdict
──────────────────────────────────────────────────────────────
  [ 0]       0       0   0.00    empty
  [ 4]     148       1   0.99  ✓ CONFIRM
  [ 7]       1      72   0.01  ✗ DECOY
  ...
──────────────────────────────────────────────────────────────
  Scanned 27/27 slots — 1 confirm, 3 decoy, 23 empty
  Best confirm → slot [ 4]  conf=0.99
```

If the confirm slot shows low confidence, recheck your Minecraft GUI scale
setting and re-run `--calibrate-only`.

---

## How the AFK solver works

```
[ Monitoring ]
   mss polls a title-bar strip every 250 ms
   ↓  "Afk Grinding" dark background detected
[ Intercept ]
   Delete /tmp/mc_spamming → grinder halts within one tick
   ↓
[ Grid sweep ]  slots 0 → 26
   Glide cursor to slot (8-step, 80 ms) → 120 ms settle
   Grab 150×100 px tooltip crop → convert to HSV
   ┌─ green pixels (#55FF55) ≥ 30 AND green/red ≥ 2× → CONFIRM → click
   ├─ red pixels (#FF5555) dominant → DECOY → skip
   └─ 15 s total timeout → park cursor → abort
   ↓
[ Recovery ]
   Wait 800 ms → recreate /tmp/mc_spamming → relaunch grinder (grinder-only mode)
   Archive tooltip crops to /tmp/mc_afk_captures/run_<timestamp>/
   ↓
[ Back to monitoring ]
```

---

## Advanced options

These are only needed for debugging or non-standard setups — normal use only
requires `bash mc_spam_2.sh`.

| Command | What it does |
|---|---|
| `python3 mc_afk_solver.py` | Run the solver standalone (continuous) |
| `python3 mc_afk_solver.py --once` | Solve one popup then exit |
| `python3 mc_afk_solver.py --dry-run` | Scan all slots, print report, no click |
| `python3 mc_afk_solver.py --calibrate-only` | Print grid geometry and exit |
| `python3 mc_afk_solver.py --timeout 60` | Custom wait time for `--once`/`--dry-run` |
| `python3 mc_afk_solver.py --script /path/to/other.sh` | Use a different grinder script |

---

## Tunable constants

Edit the top of `mc_afk_solver.py` to adjust behaviour:

| Constant | Default | Effect |
|---|---|---|
| `MONITOR_POLL_SECONDS` | `0.25` | How often to check for the popup |
| `HOVER_SETTLE_MS` | `120` | Wait after cursor lands on each slot |
| `GLIDE_DURATION_MS` | `80` | Time to glide the cursor between slots |
| `GLIDE_STEPS` | `8` | Sub-steps per glide (higher = smoother) |
| `CLICK_RECOVERY_MS` | `800` | Wait after clicking for server to close UI |
| `SWEEP_TIMEOUT_SECONDS` | `15` | Abort sweep if this many seconds pass |
| `GREEN_PIXEL_THRESHOLD` | `30` | Min green pixels to call a slot CONFIRM |
| `RED_PIXEL_THRESHOLD` | `20` | Min red pixels to call a slot DECOY |
| `GREEN_RED_RATIO_MIN` | `2.0` | Min green÷red ratio required to confirm |

---

## Repository layout

| Path | Purpose |
|---|---|
| `mc_spam_2.sh` | **Main entry point** — grinder + solver launcher |
| `mc_afk_solver.py` | AFK popup monitor, grid sweeper, HSV classifier |
| `requirements_afk_solver.txt` | Python dependency list |
| `mc_farm.sh` | Legacy grinder (standalone, no solver integration) |
| `join_training_data.py` | Joins script logs with server ground-truth |
| `github_push.sh` | Push to GitHub via Contents API (no git needed) |
| `attached_assets/` | Reference screenshots for auto-calibration |
| `afk-fabric/` | Fabric mod — server-side verification GUI replica |
| `afk-forge/` | Forge mod — same |
| `afk-plugin/` | Spigot/Paper plugin |

---

## Push to GitHub

```bash
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_... bash github_push.sh
```

Fetches the full remote tree in one API call, compares SHA checksums, and
only uploads files that actually changed.

---

## Training data

Two logs are written during a farming session:

- **`attached_assets/attempts.jsonl`** — what the *script* saw per popup: which
  slots it inspected, what each detection backend voted, which slot it clicked.
- **`afkverify_events.jsonl`** — what the *server* saw: the real confirm slot
  and whether the click passed or failed (written by the Fabric/Forge mod).

Join them to measure accuracy:

```bash
python3 join_training_data.py --events /path/to/server/afkverify_events.jsonl
```

Writes `attached_assets/training_data.jsonl` and prints a per-backend accuracy
report. Use that to tune thresholds in `mc_afk_solver.py`.

```bash
# Tag sessions with your IGN (useful for multi-player servers)
MC_PLAYER_NAME="YourIGN" bash mc_spam_2.sh
```
