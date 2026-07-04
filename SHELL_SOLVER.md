# Shell Backup AFK Solver — Complete Reference

> **File:** `mc_farm.sh` (embedded, lines ~2214–2750)  
> **Activated by:** Spam loop liveness check — launches automatically when the Python solver process dies  
> **Dependencies:** `imagemagick`, `xdotool`, `maim` or `scrot`, `gawk`, `awk`  
> **No pip, no Python, no extra runtimes**

---

## Table of Contents

1. [What it does](#1-what-it-does)
2. [How to use it](#2-how-to-use-it)
3. [Architecture overview](#3-architecture-overview)
4. [Configuration constants](#4-configuration-constants)
5. [Function reference](#5-function-reference)
6. [Detection pipeline deep dive](#6-detection-pipeline-deep-dive)
7. [Calibration — how thresholds were derived](#7-calibration--how-thresholds-were-derived)
8. [Failure modes and recovery](#8-failure-modes-and-recovery)
9. [Research techniques used](#9-research-techniques-used)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What it does

The Shell Backup Solver is a **pure-bash AFK popup solver** that activates automatically if the primary Python solver crashes or is killed. It does the same job as the Python solver:

1. Poll for the "Afk Grinding" popup every 400 ms
2. When detected, locate the 3×9 slot strip
3. Hover each slot in priority order (HIGH-confidence slots first)
4. Read the tooltip colour — green = confirm, red = deny
5. Click the confirm slot and verify the popup closed

It uses only tools already available on Ubuntu/Linux Mint LTS:
- **ImageMagick** (`convert`, `identify`) — screenshot analysis
- **xdotool** — mouse movement and clicks
- **maim** / **scrot** — screenshots
- **gawk / awk** — pixel data processing

---

## 2. How to use it

### Normal operation (automatic)

Just run `mc_farm.sh` normally. The shell backup solver is **always on standby**. You do not need to do anything extra. When the Python solver (`AFK_PID`) stops responding, the spam loop detects this within one loop iteration and launches `shell_afk_solver` as a background process.

```
[ MC Farm ] ⚠  Python solver (PID=12345) died — launching shell backup solver
[ MC Farm ]    Shell backup PID=12349
```

The shell solver then runs until you stop the farm script (run `mc_farm.sh` again to toggle off).

### Manual launch (testing)

You can test the shell solver in isolation without starting the full farm:

```bash
# Source the script (loads all functions, skips the toggle logic)
# Then call the main solver loop directly:
source mc_farm.sh   # note: this also touches /tmp/mc_spamming — stop it after testing

# Or, test individual functions:
source mc_farm.sh
sh_popup_open && echo "popup open" || echo "no popup"
sh_find_strip
sh_read_tooltip /path/to/screenshot.png 683 344
```

### Diagnostic log

All shell solver output goes to **`/tmp/mc_sh_afk_log.txt`** and stderr:

```bash
tail -f /tmp/mc_sh_afk_log.txt
```

Sample output during a successful solve:
```
[14:32:01] [ Shell Backup Solver ] started  HAS_IMGMAG=1  HAS_XINPUT=1
[14:32:03] Popup detected!
[14:32:03]   Strip: L=508 T=198 R=854 B=380  slot=38×60px
[14:32:04]   Prescan: 4 HIGH + 5 MED
[14:32:04]   slot[0,3][HIGH] @ (642,228): deny
[14:32:04]   slot[1,7][HIGH] @ (796,288): confirm
[14:32:04]   click OK — popup closed
[14:32:04]   ✓ SOLVED (row=1 col=7)
```

---

## 3. Architecture overview

```
mc_farm.sh spam loop
    │
    ├─ every iteration: kill -0 "$AFK_PID"
    │       if Python is dead AND shell backup not running:
    │           shell_afk_solver &   ←── background process
    │
shell_afk_solver (background)
    │
    ├── every 400ms: sh_popup_open?
    │       NO  → idle counter, log every 50 polls (~20s)
    │       YES → touch AFK_LOCK (pauses spam loop clicks)
    │
    ├── sh_find_strip → strip bounds (L T R B slot_w slot_h)
    │       FAIL → wait 30s for manual solve
    │
    ├── human-like reaction delay 200–650ms
    │
    ├── sh_prescan → candidate slots sorted HIGH first
    │       FAIL → fall back to all 27 slots
    │
    └── for each candidate:
            sh_hover_spiral (17 positions: center + rings 1 & 2)
                → sh_wait_tooltip (60ms initial + 65ms polls)
                    FOUND → sh_read_tooltip (3-backend quorum)
                                confirm → sh_click_verify
                                            → sh_popup_open check
                                deny    → skip
                                empty   → retry once if HIGH
                    NOT FOUND → next slot
```

---

## 4. Configuration constants

All constants are defined just before the solver functions in `mc_farm.sh`:

| Constant | Default | Description |
|---|---|---|
| `SH_SNAP` | `/tmp/_mc_sh_afk.png` | Screenshot path used by the solver |
| `SH_LOG` | `/tmp/mc_sh_afk_log.txt` | Diagnostic log file |
| `SH_POLL_S` | `0.40` | Seconds between popup polls |
| `SH_HOVER_MS` | `220` | Total ms to wait for tooltip after hover |
| `SH_SWEEP_TIMEOUT` | `5` | Seconds before aborting the whole sweep |
| `SH_POPUP_MIN_W` | `200` | Minimum width (px) for popup bbox to be valid |
| `SH_POPUP_MIN_H` | `200` | Minimum height (px) for popup bbox to be valid |
| `SH_TIP_THRESH` | `2000` | Dark-purple pixel count needed to confirm tooltip visible |
| `SH_GRAY_THRESH` | `400` | Unused — kept only as a calibration reference comment |

### When to change them

**`SH_POPUP_MIN_W` / `SH_POPUP_MIN_H`** — Only change these if your Minecraft window is much smaller (< 800px wide) and the popup scales accordingly. On 1366×694 the popup is 346×328.

**`SH_TIP_THRESH`** — The measured gap between tooltip-present (21k+ px) and tooltip-absent (< 384 px) is enormous. You'd only need to lower this if your screen resolution produces a very small tooltip box (unlikely).

**`SH_HOVER_MS`** — Matches Python's `HOVER_WAIT = 0.22`. Minecraft renders tooltips in one game tick (50ms); 220ms total gives three polls after the 60ms initial sleep. Increase to 350ms if on a slow machine.

**`SH_POLL_S`** — Increase to reduce CPU usage; decrease to react faster. The Python solver polls at ~100ms; 400ms is conservative for the shell backup.

---

## 5. Function reference

### `sh_take_snap [output_path]`

Takes a full-screen screenshot and saves to `$SH_SNAP` (or given path).

- Prefers `maim -i <active_window>` to capture the Minecraft window without compositor lag
- Falls back to `maim` (full screen), then `scrot -z`

```bash
sh_take_snap                    # → $SH_SNAP
sh_take_snap /tmp/my_snap.png   # → custom path
```

---

### `sh_popup_open`

Returns 0 (success) if the AFK popup rectangle is currently visible on screen.

**Method:** Runs `convert` with `-fuzz 15% -opaque rgb(198,198,198)` to isolate gray pixels, then `-trim` to get the bounding box. The popup is always ≥346×328 px. The hotbar is ~1366×45 px (PH < 200). Caveat: an open player inventory (E key) would also pass — but that shouldn't happen during AFK farming.

```bash
if sh_popup_open; then
    echo "popup is open"
fi
```

---

### `sh_find_strip [snap_path]`

Locates the AFK slot strip within the popup. Echoes `"L T R B slot_w slot_h"` or returns 1 on failure.

**Three-step algorithm:**
1. `-fuzz 15% -opaque -trim` → popup bounding box (PX, PY, PW, PH)
2. Compress popup crop to 1 pixel wide (`-filter Box -scale 1xH!`) → one pixel per row. First dark row (value < 40) after the top 30% = inventory separator
3. Compute: `strip_top = PY + 18` (title bar), `strip_bot = PY + sep_row` (or 49% fallback), `slot_w = PW / 9`, `slot_h = strip_height / 3`

```bash
strip=$(sh_find_strip)
read -r sl st sr sb slot_w slot_h <<< "$strip"
```

On 1366×694 with the standard popup, typical output:
```
508 198 854 380 38 60
```

---

### `sh_prescan L T R B slot_w slot_h`

Scans all 27 slots (3 rows × 9 cols) and returns candidates sorted HIGH first.

**Method:** Crops the strip region, dumps it as ImageMagick `txt:` pixel data, then in a single `awk` pass classifies each pixel by slot. For each slot, counts pixels in the centre ¼ box where `max_channel - min_channel > 28` (colorful = item present).

| Count | Confidence |
|---|---|
| ≥ 15 colorful px | HIGH |
| ≥ 5 colorful px | MED |
| < 5 | skipped |

Output: one line per candidate — `"row col HIGH|MED"`, HIGH first, then left-to-right top-to-bottom.

```bash
candidates=$(sh_prescan $sl $st $sr $sb $slot_w $slot_h)
echo "$candidates"
# 0 3 HIGH
# 1 7 HIGH
# 2 1 MED
```

---

### `sh_has_tooltip [snap_path]`

Returns 0 if a tooltip background is visible.

**Method:** Counts pixels where `r ≤ 42, g ≤ 10, b ≤ 42` (Minecraft tooltip dark-purple). Returns 0 if count ≥ `SH_TIP_THRESH` (2000).

Calibrated from 68 screenshots:
- Tooltip present → 21,045–45,084 px
- Tooltip absent → 0–383 px

```bash
sh_has_tooltip && echo "tooltip visible"
```

---

### `sh_wait_tooltip [timeout_ms]`

Polls for tooltip appearance after a hover move. Mirrors Python `_wait_for_tooltip()`.

1. Sleep 60ms (Minecraft renders in ≤1 game tick = 50ms)
2. Poll every 65ms until deadline
3. Return 0 on tooltip, 1 on timeout

```bash
sh_wait_tooltip 220    # 220ms total
```

---

### `sh_read_tooltip snap_path hover_x hover_y`

Reads tooltip text colour and returns `"confirm"`, `"deny"`, or `"empty"`.

**Scan zone:** A 260×130 pixel window centred above the hover point (`hover_y - 210` to `hover_y - 80`). This is where Minecraft renders the tooltip title line.

> **Why not bbox detect?** ImageMagick `-fuzz 8% -opaque "rgb(21,5,21)"` on JartexNetwork screenshots returns a 1362×690 bbox (the whole screen), because dark game-world areas sit within fuzz range of the tooltip colour. The hover-position approach bypasses this entirely.

**Three-backend quorum (≥ 2 of 3 must agree):**

| Backend | Green condition | Red condition | Threshold |
|---|---|---|---|
| B1 – RGB range | r:48–135, g:195–255, b:48–135 | r:188–255, g:38–118, b:38–118 | ≥ 5 pixels |
| B2 – Channel ratio | G/(R+G+B) > 0.45 AND G > 80 | R/(R+G+B) > 0.45 AND R > 80 | ≥ 4 pixels |
| B3 – Longest run | G > R+40 AND G > B+40 AND G > 100 | R > G+40 AND R > B+40 AND R > 100 | run ≥ 3 |

Mirrors Python's `CONFIRM_QUORUM = 2`.

```bash
verdict=$(sh_read_tooltip "$SH_SNAP" 683 288)
# → "confirm", "deny", or "empty"
```

---

### `sh_hover_spiral slot_center_x slot_center_y`

Moves the cursor to the slot centre and tries 17 spiral positions waiting for a tooltip after each. Mirrors Python `hover_spiral()` exactly.

**Positions (dx, dy, timeout_ms):**
- Ring 0: `(0, 0, 220)` — centre, full timeout
- Ring 1: `(0,±4), (±4,0)` crosses + `(±3,±3)` diagonals — 110ms each
- Ring 2: `(0,±8), (±8,0)` crosses + `(±6,±6)` diagonals — 88ms each

Uses `bezier_move` (TECHNIQUE 2) for human-like cursor paths. Captures mouse position once per step to avoid race conditions.

Returns: `"found nx ny"` or `"notfound sx sy"`

```bash
result=$(sh_hover_spiral 642 288)
read -r status mx my <<< "$result"
```

---

### `sh_click_verify target_x target_y`

Clicks the given position and verifies the popup closed. Up to 3 retries with 150ms gaps.

**Click method:** `overshoot_click` (TECHNIQUE 5) — moves 25–35% past the target, corrects back, adds micro-drift.

**Verification:** Re-runs `sh_popup_open` after each attempt. When the popup closes, `-trim` finds only the hotbar strip (PH ≈ 45 < 200) → `sh_popup_open` returns 1 → success.

Returns 0 on success, 1 if popup remains after 3 retries.

---

### `shell_afk_solver`

The main loop. Called in background by the spam loop. Runs until `/tmp/mc_spamming` is removed.

**Idle logging:** logs "still watching…" every 50 polls (~20 seconds) so you know it's alive.

**Strip detection failure:** waits 30 seconds for manual solve before re-polling.

**No confirm found:** waits 30 seconds for manual solve before re-polling.

**Post-solve pause:** 800–1400ms random delay before resuming (mirrors Python).

---

## 6. Detection pipeline deep dive

### Step 1 — Popup detection (`sh_popup_open`)

```
screenshot → convert -fuzz 15% -opaque rgb(198,198,198) -trim
                          ↓
                   bbox: WxH+X+Y
                          ↓
              W ≥ 200 AND H ≥ 200 ?
              YES → popup open   NO → no popup
```

The 15% fuzz catches JartexNetwork's custom-pack inventory grays (140–230 range) not just vanilla's 198,198,198.

### Step 2 — Strip location (`sh_find_strip`)

```
popup crop → compress to 1 pixel wide (Box filter)
                    ↓
           scan each row for first dark pixel (value < 40)
           after top 30% of popup height
                    ↓
           separator row found → strip_bot = popup_top + sep_row
           not found → strip_bot = popup_top + popup_height × 49%
```

The title bar (top ~18px) and the "Inventory" separator are both dark bands. The 30% cutoff skips the title bar so we don't confuse it for the separator.

### Step 3 — Slot prescan (`sh_prescan`)

Runs a **single** `convert … txt:-` dump of the whole strip, then processes all pixels in one `awk` pass — no per-slot `convert` calls. For a 346×182 strip this reads ~63k pixels in one pass.

### Step 4 — Hover + tooltip wait (`sh_hover_spiral` + `sh_wait_tooltip`)

The spiral ensures we hit the actual tooltip trigger zone even if the slot center is slightly off. Minecraft's tooltip trigger is not pixel-perfect; the spiral covers ±8px in both axes.

The 60ms initial sleep is critical — Minecraft only renders the tooltip on the **next game tick** (50ms). Polling before that always sees no tooltip.

### Step 5 — Tooltip classification (`sh_read_tooltip`)

Three independent algorithms vote:
- **RGB range** (B1) — direct pixel matching against the Mojang palette colors `§a #55FF55` (green) and `§c #FF5555` (red)
- **Channel ratio** (B2) — catches intermediate shades and shadows that shift absolute values but preserve the channel dominance ratio
- **Longest run** (B3) — catches text even if individual pixel thresholds aren't met, as long as ≥3 consecutive pixels form a run

Quorum requirement (≥2/3) prevents both false positives and false negatives.

### Step 6 — Click verification (`sh_click_verify`)

After clicking, the popup should close within one server tick. Each verification attempt calls `sh_popup_open` which takes a fresh screenshot. If the popup bbox has vanished, the click worked.

---

## 7. Calibration — how thresholds were derived

All values were measured from **68 real JartexNetwork screenshots** in `attached_assets/` (1366×694 RGBA PNGs).

### Screen layout (1366×694)

```
Popup bounding box (from all screenshots): 346×328 at +508+180
  → Center: (681, 344)
  → Strip (3-row slot area): y ≈ 198–380
  → Slot size: ≈ 38×60 px
```

### Gray pixel counts (full screen, r/g/b all 100–230)

| State | Min | Max |
|---|---|---|
| Popup open, no tooltip | 45,923 | 85,890 |
| Popup open, with tooltip | 51,581 | 86,349 |
| **Overlap** | ← **cannot distinguish these states** → |

→ Gray count is **not used** for popup detection.

### Dark-purple pixel counts (r ≤ 42, g ≤ 10, b ≤ 42)

| State | Min | Max |
|---|---|---|
| Tooltip VISIBLE | 21,045 | 45,084 |
| Tooltip ABSENT | 0 | 383 |

→ `SH_TIP_THRESH = 2000` sits in the enormous gap between 383 and 21,045.

### Popup bbox size

All 68 screenshots with popup open produced a bbox of **≥ 346×328**.  
→ `SH_POPUP_MIN_W = 200`, `SH_POPUP_MIN_H = 200` are conservative safe limits.

### Tooltip text pixel counts (in title-line crop, 260×130 scan zone)

| State | Green px | Red px |
|---|---|---|
| Confirm tooltip | 60–1,096 | 8–80 |
| Deny tooltip | 0–60 | 776–1,320 |

→ Backend 1 threshold of ≥5 green / ≥5 red is well within both ranges.

---

## 8. Failure modes and recovery

### Failure: `sh_find_strip` returns empty

**Cause:** Popup opened but the gray region is too faint (resource pack override) or the separator row wasn't found.

**Recovery (built-in):** Waits 30 seconds polling for the popup to close on its own (e.g. user manually clicks). After 30s, clears the lock and resumes polling.

**Manual fix:** If this happens consistently, the fuzz percentage in `sh_find_strip` may need adjusting:
```bash
# In sh_find_strip, change:
-fuzz 15% -fill white -opaque "rgb(198,198,198)" \
# to a higher fuzz if your resource pack uses darker grays:
-fuzz 25% -fill white -opaque "rgb(198,198,198)" \
```

### Failure: Prescan returns no candidates

**Cause:** Slots appear empty to the colorfulness check (item textures are near-grayscale).

**Recovery (built-in):** Falls back to checking all 27 slots in order.

### Failure: No confirm slot found after sweeping all slots

**Cause:** Tooltip read quorum failed (2 of 3 backends disagreed). This can happen if:
- The tooltip appears partially off-screen
- The server uses a non-standard text color
- Screenshot timing was off (tooltip appeared then disappeared mid-scan)

**Recovery (built-in):** Waits 30s for manual solve, then resumes polling.

### Failure: Click verify fails (popup still open after 3 retries)

**Cause:** Click landed on wrong pixel (overshoot didn't correct), or server debounce.

**Recovery (built-in):** Logs the failure, releases the AFK lock, and re-enters the polling loop — next iteration will detect the popup still open and try again.

### Failure: `HAS_IMGMAG=0`

**Cause:** ImageMagick not installed.

**Impact:** `sh_popup_open` returns 1 immediately (no popup ever detected). Shell backup solver is effectively disabled.

**Fix:**
```bash
sudo apt-get install imagemagick
```

---

## 9. Research techniques used

Each function is built from a published technique:

| Technique | Function(s) | Source |
|---|---|---|
| 1 — Log-normal timing | `_lognormal_ms` (used in hover spiral timing) | Box-Muller transform; IEEE "Bot Detection Using Mouse Movements" 2023 |
| 2 — Cubic Bézier mouse | `bezier_move` → called by `sh_hover_spiral` | Smooth cursor movement via `xdotool mousemove_relative` |
| 3 — Polar arc sweep | `polar_arc_sweep` (ACTION 1 camera rotation) | `xdotool --polar` spherical mouse motion |
| 4 — Window-relative clicks | `get_mc_window`, `window_click` | `xdotool getactivewindow`, window-anchored coords |
| 5 — Overshoot correction | `overshoot_click` → called by `sh_click_verify` | Human motor-control overshoot model |
| 6 — ImageMagick pixel scan | `imgmag_pixel`, `imgmag_count_range`, `imgmag_popup_gray` → core of all detection | ImageMagick `txt:` format pixel enumeration |
| 7 — MAE template matching | `imgmag_slot_match`, `save_slot_template` | `compare -metric MAE` for slot fingerprinting |
| 8 — xinput event recorder | `mc_record_solve` | `xinput test-xi2` event stream |
| 9 — Atomic cursor restore | `atomic_move_restore` | `xdotool mousemove restore` |
| 10 — Sync chain clicks | `chain_click` | `xdotool --sync` command chaining |

---

## 10. Troubleshooting

### "Shell backup solver is running but never finds the popup"

Check:
```bash
# 1. Confirm ImageMagick is installed
which convert && convert --version

# 2. Take a manual snapshot and test popup detection
maim /tmp/test_snap.png
convert /tmp/test_snap.png \
  -fuzz 15% -fill white -opaque "rgb(198,198,198)" \
  -fill black +opaque white \
  -trim -format "%wx%h+%X+%Y\n" info:
# Should print something like: 346x328+508+180
# If it prints: 1366x694+0+0  → fuzz is too high or no gray popup exists

# 3. Manually verify tip pixel count
convert /tmp/test_snap.png txt:- | awk '
  NR>1{gsub(/[,:()\n]/," ");r=$3+0;g=$4+0;b=$5+0;
  if(r<=42&&g<=10&&b<=42&&(r+g+b)>2)t++}
  END{print "tip_px="t}
'
# Should be 21000+ when tooltip visible, <400 when not
```

### "Tooltip read always returns 'empty'"

The scan zone may miss the tooltip. Try widening it:
```bash
# In sh_read_tooltip, change:
zone_w=260; zone_h=130
# to:
zone_w=400; zone_h=200
```

Or check if the tooltip is actually rendering:
```bash
maim /tmp/snap.png
# Open snap.png in an image viewer and hover manually — does the tooltip appear?
```

### "Click fires but popup stays open"

The click coordinates may be wrong. Check strip detection:
```bash
source mc_farm.sh   # loads all functions without starting the loop
maim "$SH_SNAP"
sh_find_strip "$SH_SNAP"
# Should output: L T R B slot_w slot_h
```

Also check that `xdotool` is targeting the correct window. The solver uses absolute screen coordinates — make sure Minecraft is in the foreground and not obscured.

### Log verbosity

To increase log detail, edit `_sh_log` to also write to stdout:
```bash
# Change:
printf "[%s] %s\n" "$ts" "$*" | tee -a "$SH_LOG" >&2
# To:
printf "[%s] %s\n" "$ts" "$*" | tee -a "$SH_LOG"
```

### Reset everything

```bash
# Stop the farm (toggles off, kills all PIDs)
./mc_farm.sh

# Clear solver state
rm -f /tmp/mc_sh_afk_log.txt /tmp/_mc_sh_afk.png /tmp/_mc_sh_strip_ps.png /tmp/_mc_sh_tipzone.png

# Restart
./mc_farm.sh
```
