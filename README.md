# afkfarmer

Minecraft AFK farm automation suite for **JartexNetwork OneBlock** server.

**One command starts and stops everything.**

---

## Setup (one time only)

```bash
# System tools
sudo apt update
sudo apt install -y xdotool

# Python libraries
pip install mss opencv-python numpy pyautogui

# Make the script executable
chmod +x mc_spam_2.sh
```

---

## Usage

```bash
# Start — launches the grinding loop AND the AFK solver together
bash mc_spam_2.sh

# Stop — run the same command again while it is running
bash mc_spam_2.sh
```

That's it. Nothing else to run. One terminal, one command.

---

## What happens when you start

1. Dependency check — exits immediately with a clear message if `xdotool` or the Python libraries are missing.
2. `mc_afk_solver.py` is launched in the background. It calibrates the grid geometry from `attached_assets/` and begins watching the screen.
3. The grinding loop starts: left-click attacks, occasional camera drift, micro-vibration, and jumps.
4. When the *Afk Grinding* popup appears the solver pauses the grinder, sweeps all 27 slots, clicks the green "Click to Confirm" item, then resumes the grinder automatically.

## What happens when you stop

1. The flag file `/tmp/mc_spamming` is deleted — the attack loop exits on its next tick.
2. The solver process is killed via the stored PID in `/tmp/mc_solver.pid`.
3. Both logs remain on disk for review.

---

## Hardware profile — Dell Inspiron 3521, GUI scale 2

The solver is pixel-calibrated against this exact setup (verified across 68 reference screenshots):

| Property | Value |
|---|---|
| Minecraft window | 1366 × 694 px (windowed) |
| GUI scale | 2 |
| Chest panel | cols 510–853, rows 182–507 |
| Slot size | 36 px (32 px inner + 2 px border) |
| Slot col centres | 538, 574, 610, 646, 682, 718, 754, 790, 826 |
| Slot row centres | 230, 266, 302 |

**Popup detection** — four grey-panel probe pixels are sampled at coordinates
`(680,193)`, `(600,193)`, `(750,193)`, `(680,205)`.  
When the chest GUI is open these pixels read `#C6C6C6` (BGR ≈ 198,198,198).  
If ≥ 3 of the 4 probes match, the solver triggers.

This profile loads automatically. If you ever change resolution or GUI scale,
run `--calibrate-only` to see what the dynamic calibration finds:

```bash
python3 mc_afk_solver.py --calibrate-only
```

---

## Logs

| File | Contents |
|---|---|
| `/tmp/mc_spam_2.log` | Grinder start / stop events |
| `/tmp/mc_afk_solver.log` | Solver detections, slot scan results, click confirmations |
| `/tmp/mc_afk_captures/run_<ts>/` | Per-run tooltip crops (auto-archived after each solve) |

Watch both logs live:
```bash
tail -f /tmp/mc_spam_2.log /tmp/mc_afk_solver.log
```

---

## How the solver works

```
[ Monitoring — every 250 ms ]
  Sample 4 probe pixels in the grey panel header strip
  ≥ 3 match BGR ≈ 198,198,198  →  GUI is open
  ↓
[ Intercept ]
  Delete /tmp/mc_spamming → grinder halts within one tick
  ↓
[ Grid sweep — slots 0 to 26 ]
  Glide cursor to slot centre (8 steps, 80 ms)
  Wait 120 ms for tooltip to render
  Grab 150 × 100 px crop at tooltip position
  Convert to HSV → apply mask for #55FF55 green text
    green pixels ≥ 30  →  ✓ CONFIRM → left-click → break
    green pixels < 30  →  skip to next slot
  All 27 slots empty / timeout 15 s  →  failsafe abort
  ↓
[ Recovery ]
  Wait 800 ms → recreate /tmp/mc_spamming → relaunch grinder
  Archive tooltip crops to /tmp/mc_afk_captures/run_<timestamp>/
  ↓
[ Back to monitoring ]
```

---

## Solver CLI flags

Only needed for debugging — normal use only requires `bash mc_spam_2.sh`.

| Command | What it does |
|---|---|
| `python3 mc_afk_solver.py` | Standalone continuous mode |
| `python3 mc_afk_solver.py --once` | Solve one popup then exit |
| `python3 mc_afk_solver.py --dry-run` | Sweep all slots and print report — no click |
| `python3 mc_afk_solver.py --calibrate-only` | Print grid geometry and exit |
| `python3 mc_afk_solver.py --timeout 60` | Custom wait timeout for `--once` / `--dry-run` |
| `python3 mc_afk_solver.py --script /path/to/other.sh` | Use a different grinder script |

### Dry-run output example

```
────────────────────  DRY-RUN SCAN REPORT  ────────────────────
  Slot   Green px   Verdict
──────────────────────────────────────────────────────────────
  [ 0]          0     empty
  [ 4]        148   ✓ CONFIRM
  [ 7]          3     empty
  ...
──────────────────────────────────────────────────────────────
  Scanned 27/27 slots — 1 confirm
  Best confirm → slot [ 4]  green_px=148
```

---

## Tunable constants (top of `mc_afk_solver.py`)

| Constant | Default | Effect |
|---|---|---|
| `MONITOR_POLL_SECONDS` | `0.25` | How often to probe for the popup |
| `HOVER_SETTLE_MS` | `120` | Wait after cursor lands on each slot |
| `GLIDE_DURATION_MS` | `80` | Time to glide cursor between slots |
| `GLIDE_STEPS` | `8` | Sub-steps per glide (higher = smoother) |
| `CLICK_RECOVERY_MS` | `800` | Wait after click for server to close UI |
| `SWEEP_TIMEOUT_SECONDS` | `15` | Abort sweep if no slot found in time |
| `GREEN_PIXEL_THRESHOLD` | `30` | Min #55FF55 pixels to confirm a slot |
| `POPUP_GREY_MIN` / `MAX` | `160` / `220` | BGR channel range for panel detection |

---

## Grinder actions (`mc_spam_2.sh`)

| Action | Probability | Notes |
|---|---|---|
| Left-click attack | ~90% | Hold 28–66 ms, cooldown 620–750 ms + fatigue |
| Smooth camera drift | ~3% | 3–6 micro-steps over 90–220 ms |
| Micro-vibration | ~5% | 2–4 shake cycles |
| Space jump | ~2% | Throttled to once every 45–105 s |

Fatigue delay grows ~12 ms / min (capped at 75 ms) to imitate a tiring player.
Every action re-checks the flag file before executing — halts within one tick
of the solver deleting `/tmp/mc_spamming`.

---

## Files in this repository

| File | Purpose |
|---|---|
| **`mc_spam_2.sh`** | Main entry point — start/stop the whole suite |
| **`mc_afk_solver.py`** | AFK popup monitor, grid sweeper, HSV classifier |
| `requirements_afk_solver.txt` | Python dependency list |
| `join_training_data.py` | Joins script logs with server ground-truth |
| `github_push.sh` | Push to GitHub via Contents API (no git binary needed) |
| `attached_assets/` | 68 reference screenshots used for auto-calibration |
| `afk-fabric/` | Fabric mod — server-side verification GUI replica |
| `afk-forge/` | Forge mod — same |
| `afk-plugin/` | Spigot/Paper plugin |

> `mc_farm.sh` is a legacy standalone grinder. It is not used by this suite.

---

## Push changes to GitHub

```bash
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_... bash github_push.sh
```

Fetches the full remote tree, compares SHA checksums, and only uploads files
that actually changed.

---

## Training data

Two logs are written during a farming session:

- **`attached_assets/attempts.jsonl`** — what the script saw: each slot inspected, the pixel counts, which slot was clicked.
- **`afkverify_events.jsonl`** — what the server saw: the real confirm slot and whether the click passed.

Join them to measure accuracy:

```bash
python3 join_training_data.py --events /path/to/server/afkverify_events.jsonl
```

Writes `attached_assets/training_data.jsonl` and prints a per-backend accuracy
report you can use to retune thresholds in `mc_afk_solver.py`.
