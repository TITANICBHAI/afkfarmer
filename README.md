# afkfarmer

Minecraft AFK farm automation suite for **JartexNetwork OneBlock** server.

Automatically detects the periodic *"Afk Grinding"* verification popup, sweeps
the 27-slot inventory grid, identifies the **"Click to Confirm"** item by color,
and clicks it — all while keeping the mouse moving to satisfy the server's
anti-AFK system.

---

## Repository layout

| File | Purpose |
|---|---|
| `mc_spam_2.sh` | Primary grinding loop (attacks, camera drift, jumps) |
| `mc_afk_solver.py` | Python AFK-popup solver (monitor → sweep → click) |
| `mc_farm.sh` | Legacy grinding script (still functional) |
| `requirements_afk_solver.txt` | Python deps for the solver |
| `join_training_data.py` | Correlates script logs with server ground-truth |
| `github_push.sh` | Push changes to GitHub via Contents API (no git binary) |
| `attached_assets/` | Reference screenshots used for auto-calibration |
| `afk-fabric/` | Fabric mod replicating the verification GUI server-side |
| `afk-forge/` | Forge mod — same purpose |
| `afk-plugin/` | Spigot/Paper plugin |

---

## Linux Mint — one-time setup

```bash
# System tools (mouse/keyboard automation + screenshot)
sudo apt update
sudo apt install -y xdotool scrot

# Optional: OCR fallback (faster than AI when offline)
sudo apt install -y tesseract-ocr

# Python deps for mc_afk_solver.py
pip install mss opencv-python numpy pyautogui
# or: pip install -r requirements_afk_solver.txt
```

---

## Quick start

```bash
# Terminal 1 — start the grinder
bash mc_spam_2.sh

# Terminal 2 — start the solver (watches for the popup and handles it)
python3 mc_afk_solver.py

# Stop the grinder (run again while active)
bash mc_spam_2.sh
```

The grinder and solver are fully independent processes that communicate through
a single flag file (`/tmp/mc_spamming`).  The solver deletes the flag the
instant the popup appears (halting the grinder), then recreates it and
relaunches `mc_spam_2.sh` after clicking.

---

## mc_afk_solver.py — full reference

### How it works

```
[ Idle ]
   │  mss polls title strip every 250 ms
   ▼
[ Popup detected ]  →  delete /tmp/mc_spamming  (grinder halts)
   │
   ▼
[ Grid sweep ]  →  glide cursor to slots 0–26
   │                 120 ms settle → grab 150×100 tooltip crop
   │                 HSV mask for green (#55FF55) and red (#FF5555)
   │                 confidence = green / (green + red + 1)
   │
   ├─ DECOY slot (red dominant) → skip
   ├─ CONFIRM slot (green ≥ 30 px, ratio ≥ 2×) → left-click → break
   └─ 15 s timeout → park cursor at (10,10) → abort
   │
   ▼
[ Recovery ]  →  wait 800 ms  →  touch /tmp/mc_spamming  →  relaunch mc_spam_2.sh
   │
   ▼
[ Archive ]  →  move slot_*.png crops to /tmp/mc_afk_captures/run_<timestamp>/
```

### Detection logic

- **Green channel** (`#55FF55` → HSV `[35,100,100]–[85,255,255]`): "Click to Confirm" title
- **Red channel** (`#FF5555` → HSV `[0,140,140]–[10,255,255]` + `[170,140,140]–[180,255,255]`): "Do not click" title
- A slot is confirmed only when `green_px ≥ 30` **and** `green/red ≥ 2.0`
- Smooth 8-step cursor glide (80 ms) between slots — no instant teleports

### CLI flags

| Flag | Description |
|---|---|
| *(no flags)* | Continuous mode — runs forever, handles every popup |
| `--once` | Single-shot: wait for one popup, solve it, exit |
| `--dry-run` | Sweep all 27 slots and print a scan report — **no click sent** |
| `--timeout SECONDS` | How long `--once` / `--dry-run` waits for the popup (default: 30 s) |
| `--script PATH` | Override which bash script to halt/resume (default: `mc_spam_2.sh`) |
| `--calibrate-only` | Print auto-derived grid geometry from `attached_assets/` and exit |

### --dry-run explained

`--dry-run` is the recommended first step when running the solver on a new
machine or after a resolution change.  It waits for the popup to appear
naturally, glides through every slot exactly as in live mode, captures and
classifies each tooltip — but stops short of clicking anything.

At the end it prints a table like this:

```
────────────────────  DRY-RUN SCAN REPORT  ────────────────────
  Slot   Green     Red   Conf  Verdict
──────────────────────────────────────────────────────────────
  [ 0]       0       0   0.00    empty
  [ 1]       2       0   0.67    empty
  [ 4]     148       1   0.99  ✓ CONFIRM
  [ 7]       1      72   0.01  ✗ DECOY
  ...
──────────────────────────────────────────────────────────────
  Scanned 27/27 slots — 1 confirm, 3 decoy, 23 empty
  Best confirm → slot [ 4]  conf=0.99
```

If the confirm slot shows low confidence or isn't detected at all, run
`--calibrate-only` and compare the printed slot centres against what you see
on screen.

```bash
# Recommended first-run sequence
python3 mc_afk_solver.py --calibrate-only
python3 mc_afk_solver.py --dry-run --timeout 60
# happy with the report? go live:
python3 mc_afk_solver.py
```

### Tunable constants

All timing and threshold values are defined at the top of `mc_afk_solver.py`:

| Constant | Default | Effect |
|---|---|---|
| `MONITOR_POLL_SECONDS` | 0.25 | Popup check interval |
| `HOVER_SETTLE_MS` | 120 | Wait after cursor lands on slot |
| `GLIDE_DURATION_MS` | 80 | Time to glide between slots |
| `GLIDE_STEPS` | 8 | Sub-steps per glide (higher = smoother arc) |
| `CLICK_RECOVERY_MS` | 800 | Wait after click for server packet to clear |
| `SWEEP_TIMEOUT_SECONDS` | 15 | Abort whole sweep if exceeded |
| `GREEN_PIXEL_THRESHOLD` | 30 | Min green pixels to confirm a slot |
| `RED_PIXEL_THRESHOLD` | 20 | Min red pixels to flag a slot as decoy |
| `GREEN_RED_RATIO_MIN` | 2.0 | Min green/red ratio to confirm |

---

## mc_spam_2.sh — grinder reference

Runs a continuous attack loop that mimics human behaviour using three
randomised actions:

| Action | Probability | Notes |
|---|---|---|
| Standard left-click attack | ~90% | Hold 28–66 ms, swing cooldown 620–750 ms + fatigue |
| Smooth camera drift | ~3% | 3–6 sub-steps over 90–220 ms |
| Micro-vibration | ~5% | 2–4 shake cycles |
| Jump (Space) | ~2% | Throttled to once every 45–105 s |

Fatigue delay grows ~12 ms/min (capped at 75 ms) to simulate a tiring player.
All actions check the flag file before each step — halts within one tick of the
solver deleting `/tmp/mc_spamming`.

```bash
# Start
bash mc_spam_2.sh

# Stop (toggle — run again while active)
bash mc_spam_2.sh

# Watch the log
tail -f /tmp/mc_spam_2.log
```

---

## Sync to GitHub

```bash
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_... bash github_push.sh
```

Fetches the full remote tree in one API call, skips unchanged files (SHA
comparison), and pushes only what changed.

---

## Training data

Two logs are written while farming:

- **`attached_assets/attempts.jsonl`** — what the *script* saw: each slot
  inspected, each backend's vote, which slot was clicked.
- **`afkverify_events.jsonl`** — what the *server* saw: the real confirm slot
  (ground truth) and whether the click passed or failed.

Join them to measure detection accuracy:

```bash
python3 join_training_data.py --events /path/to/server/afkverify_events.jsonl
```

Writes `attached_assets/training_data.jsonl` and prints a per-backend accuracy
report.  Use that to retune thresholds or backend order in `mc_afk_solver.py`.

```bash
# Tag attempts with your IGN for multi-player sessions
MC_PLAYER_NAME="YourIGN" bash mc_spam_2.sh
```
