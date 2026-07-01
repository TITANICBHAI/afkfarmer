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

## Requirements

```
xdotool  scrot  python3  xorg (running)
```

Optional (for better accuracy):
```
ANTHROPIC_API_KEY=sk-ant-...   # enables Claude vision
tesseract                       # OCR fallback
```

## Usage

```bash
# Start farming (spam + AFK solver both launch in background)
bash mc_farm.sh

# Stop everything
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
