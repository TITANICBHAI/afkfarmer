# afkfarmer

Minecraft AFK farm script for **JartexNetwork OneBlock** server.

Automatically detects the periodic *"Afk Grinding"* popup, scans which slots contain items, hovers each item to read its tooltip, and left-clicks the one that says **"Click to Confirm"** — all while keeping the mouse moving to prevent the server's anti-AFK kick.

## Features

- **Prescan** — one screenshot of the full strip identifies item slots without hovering, skipping ~20 empty slots instantly
- **Three detection backends** (tried in order):
  1. **AI (Anthropic Claude)** — most accurate, ~24 MB RAM (model runs remotely). Set `ANTHROPIC_API_KEY`.
  2. **Tesseract OCR** — local fallback if tesseract is installed.
  3. **Color pixel scan** — pure stdlib fallback, no deps.
- **Tooltip pre-check** — dark-purple tooltip background detected before any AI/OCR call; empty slots short-circuit in < 5 ms.
- **RAM** — stays well under 50 MB (no PIL, no numpy, only stdlib + scrot + xdotool).
- **Spam/anti-AFK loop** — random mouse wiggles and clicks while mining.

## Linux Mint Setup (run once before first use)

```bash
# 1. Update package list
sudo apt update

# 2. Required — mouse/keyboard automation
sudo apt install -y xdotool

# 3. Required — screenshot tool
sudo apt install -y scrot

# 4. Optional but recommended — OCR fallback (faster than AI for offline use)
sudo apt install -y tesseract-ocr

# 5. Verify
xdotool --version && scrot --version && echo "Ready!"
```

## Usage

```bash
# Make executable (first time only)
chmod +x mc_farm.sh

# Optional: set Anthropic key for AI tooltip reading (most accurate)
export ANTHROPIC_API_KEY="sk-ant-..."

# Start farming (spam loop + AFK solver both run in background)
bash mc_farm.sh

# Stop everything (run again while active)
bash mc_farm.sh
```

Running the script a **second time** while it is active stops all background processes.

## How it works

1. Background spam loop wiggles mouse & occasionally left-clicks to look busy.
2. AFK solver polls the screen every ~0.35 s for the gray MC GUI popup.
3. When the popup appears, it waits a short human-like delay (0.28–0.95 s).
4. **Prescan**: screenshots the AFK strip once, counts colorful vs gray pixels per slot → builds list of only the slots that have items.
5. Moves mouse to each item slot, waits 140 ms for tooltip to render.
6. Reads tooltip (AI → OCR → color), clicks on "Click to Confirm".

## Sync script

`github_push.sh` — push local changes back to this repo via the GitHub Contents API (no git binary required).

```bash
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_... bash github_push.sh
```

## Training data — joining the script's log with the mod's log

Two logs get written while farming, and neither one alone tells you whether
the script is actually clicking the right slot:

- **`attached_assets/attempts.jsonl`** (written by `mc_farm.sh`) — what the
  *script* saw: every slot it inspected, what each backend (color/AI/OCR)
  voted, which slot it clicked. It never learns the real answer from the
  server, only whether its click made the popup close.
- **`afkverify_events.jsonl`** (written by the Fabric/Forge mod on the
  server) — what the *server* saw: the real confirm slot (ground truth) and
  whether the player's click passed, failed, or timed out.

Each popup on the mod side carries a `popup_id` (a UUID assigned when the
popup opens and echoed back on the outcome event), and both logs record
`confirm_row`/`confirm_col`/`clicked_row`/`clicked_col` using the same
row/col addressing the script uses internally, so the two logs can be joined
precisely instead of only by "these happened around the same time."

Optionally set `MC_PLAYER_NAME` before running `mc_farm.sh` so each
`attempts.jsonl` record is tagged with the player's name — useful for
telling attempts apart on a server where more than one person is farming:

```bash
MC_PLAYER_NAME="YourIGN" bash mc_farm.sh
```

Run the join tool (needs `afkverify_events.jsonl` from the server's run
directory, and `python3` which is not otherwise required by `mc_farm.sh`):

```bash
python3 join_training_data.py --events /path/to/server/afkverify_events.jsonl
```

It writes `attached_assets/training_data.jsonl` (one labeled record per
popup: what the script guessed + what was actually correct) and prints a
per-backend accuracy report to stdout — use that to decide whether
color/AI/OCR actually deserves the most trust and retune the backend order
or weighting in `mc_farm.sh` accordingly. If a popup_id isn't available
(older mod build), it automatically falls back to timestamp-proximity
matching (±1s, configurable via `--tolerance`).
