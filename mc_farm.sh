#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  MC AFK Farm — Spam + AFK Popup Solver (single script)
#  Run once to START, run again to STOP (toggle)
#
#  Needs: xdotool  scrot  python3  (zero pip installs)
#  Optional — set before running for best accuracy:
#    export ANTHROPIC_API_KEY="sk-ant-..."
#  RAM: ~22-26 MB
# ═══════════════════════════════════════════════════════════════

FLAG_FILE="/tmp/mc_spamming"
AFK_LOCK="/tmp/mc_afk_solving"
PY_SCRIPT="/tmp/mc_afk_px.py"
PID_FILE="/tmp/mc_farm_pids"

if [ -f "$FLAG_FILE" ]; then
    rm -f "$FLAG_FILE"
    if [ -f "$PID_FILE" ]; then
        while IFS= read -r _pid; do kill "$_pid" 2>/dev/null; done < "$PID_FILE"
        rm -f "$PID_FILE"
    fi
    pkill -f "$PY_SCRIPT" 2>/dev/null
    rm -f "$AFK_LOCK"
    echo "[ MC Farm ] STOPPED"
    exit 0
fi

touch "$FLAG_FILE"
echo "[ MC Farm ] STARTED  (run again to stop)"
sleep 1

cat > "$PY_SCRIPT" << 'PYEOF'
#!/usr/bin/env python3
"""
MC AFK Popup Solver
===================
Finds the "Afk Grinding" strip (the box ABOVE your Inventory), then sweeps
every slot in it left-to-right / top-to-bottom. The instant a tooltip reads
green "Click to Confirm" → left-click it and stop. Red "Do not click" →
move on. Empty slot → move on.

Detection priority (best to worst):
  1. AI vision  (Anthropic Claude) — reads words like a human; needs API key
  2. OCR        (tesseract)        — reads text characters; no key needed
  3. Color scan                    — green vs red pixel count; always works

Calibration data:
  Every popup attempt is saved as a timestamped PNG in attached_assets/ plus
  one JSON line in attached_assets/attempts.jsonl.  After each attempt the
  script pauses and asks for a correctness label via the terminal (20 s
  timeout → auto-skip).  When the script can't find the confirm item the
  spam loop is paused and the user can click manually; the script waits up to
  30 s for the popup to close before resuming.

stdlib only — no pip, no install.
"""
import os, sys, zlib, struct, subprocess, time, random, base64, json
import uuid, pathlib, shutil, select, math

FLAG = "/tmp/mc_spamming"
LOCK = "/tmp/mc_afk_solving"
SNAP = "/tmp/_mc_afk.png"

# ── PNG decode cache — one decode per scrot(), shared by all backends ─────
# Invalidated automatically when SNAP's mtime changes (i.e. after each scrot).
_snap_mtime  = -1.0
_snap_cached = None   # (rows, w, h, bpp) or None

def decode_png_cached():
    """Return decoded SNAP, re-decoding only when the file has been updated."""
    global _snap_mtime, _snap_cached
    try:    mt = os.path.getmtime(SNAP)
    except: mt = -1.0
    if mt != _snap_mtime or _snap_cached is None:
        _snap_cached = decode_png(SNAP)
        _snap_mtime  = mt
    return _snap_cached

# ── Calibration output directory (set by mc_farm.sh via env var) ──────
ASSETS_DIR  = os.environ.get('MC_ASSETS_DIR', '/tmp/mc_afk_assets')
SESSION_ID  = str(uuid.uuid4())[:8]
# Optional — the in-game username of whoever runs this script, used purely to
# make joining against afkverify_events.jsonl unambiguous on shared servers.
PLAYER_NAME = os.environ.get('MC_PLAYER_NAME', '').strip()
_attempt_no = 0          # incremented each time a popup is found

# ── Timing ────────────────────────────────────────────────────
POLL       = 0.35   # seconds between full-screen popup checks
HOVER_WAIT = 0.30   # wait after moving mouse (MC tooltip appears within 1 tick = 50ms; 300ms = ~6 ticks, safer on 14-FPS servers)
REACT_MIN  = 0.20   # human-like pause before starting to solve (min)
REACT_MAX  = 0.65   # human-like pause before starting to solve (max)
# Chat early-warning: how often to run the chat scan (every N poll cycles).
# At POLL=0.35s and N=2, we check chat every ~0.70s — fast enough to catch
# the teleport message before the popup appears (~1-2s later).
CHAT_DETECT_EVERY = 2
SWEEP_TIMEOUT = 5.5 # abort sweep if it's been running this many seconds (server kicks ~7-10s)

# ── Minecraft / JartexNetwork OneBlock AFK-check knowledge ────────────────
#
#  How the check works:
#   • The "Afk Grinding" popup fires every ~5 minutes of continuous AFK play.
#   • JartexNetwork gives ~10-15 seconds to click the correct item.
#     SWEEP_TIMEOUT is deliberately set below that window.
#   • The confirm item is a RANDOM food item — changes every popup.
#     Its tooltip title: "Click to Confirm" in §a green (#55FF55 / rgb 85,255,85).
#     All other (decoy) items say: "Do not click" in §c red (#FF5555 / rgb 255,85,85).
#   • Clicking the wrong item = immediate server kick (same as timeout).
#   • Pressing Escape closes the popup without confirming = treated as timeout = kick.
#   • The popup is a 9-column inventory strip placed ABOVE the player's inventory.
#     It has up to 3 rows (27 slots); usually only 4-8 slots contain items.
#   • GUI scale 2 is standard at 1366×694: slot icons are 16×16 native px,
#     doubled to 32×32 on screen; slot bounding boxes measured at ~30×35 px.
#   • CRITICAL: Minecraft must own the X11 focus before any click registers.
#     xdotool can move the mouse anywhere on screen, but inventory clicks are
#     silently discarded by Java's AWT event queue when another window has focus.
#   • The popup title bar ("Afk Grinding") is a DARK band at the very top of the
#     popup box — distinguishes this GUI from chests, crafting tables, etc.
#
MC_POPUP_TIMEOUT = 12.0     # seconds JartexNetwork allows for a click
MC_WINDOW_TITLE  = "Minecraft"  # xdotool window-name search string

# ── Ban guard ─────────────────────────────────────────────────────────────────
# JartexNetwork: 5 consecutive failures (wrong click or timeout) = 1-hour ban.
# The script tracks consecutive non-solved popups and stops itself before
# reaching the ban threshold.  A "solve" is counted only when the popup
# actually closed after the click (click_success=True AND popup_closed=True).
# Strip-parse failures also count as a miss because the server sees a timeout.
BAN_WARN_AT = 3    # print a loud warning after this many consecutive misses
BAN_STOP_AT = 5    # remove FLAG (stop bot) after this many — stay 1 under limit

# ── Unattended mode ───────────────────────────────────────────────────────────
# Set MC_UNATTENDED=1 in the environment to skip the 20-second post-popup
# feedback prompt.  Useful for overnight farming; disables accuracy labelling.
MC_UNATTENDED = bool(os.environ.get('MC_UNATTENDED', '').strip())

# Measured from screenshots (1366×694 screen, JartexNetwork resource pack):
#   slot_w ≈ 30 px  (popup_width / 9 columns)
#   slot_h ≈ 35 px  (row-center spacing — AFK strip is NOT square)
SLOT_H_DEFAULT = 35  # initial estimate; recalculated dynamically from detected strip height

# ── Hover spiral: positions to try (dx,dy) around the slot center ────────────
# Expands outward in rings so the center is always tried first.
# Covers ±8px — enough to find tooltip even when strip coordinates are 1-2px off.
HOVER_SPIRAL = [
    (0,  0),                               # center
    (0, -4), (4,  0), (0,  4), (-4,  0),  # ring 1: cross ±4
    (-3,-3), (3, -3), (3,  3), (-3,  3),  # ring 1: diagonals ±3
    (0, -8), (8,  0), (0,  8), (-8,  0),  # ring 2: cross ±8
    (-6,-6), (6, -6), (6,  6), (-6,  6),  # ring 2: diagonals ±6
]

# ── Prescan confidence thresholds (colorful pixel count in 14×14 box) ────────
PRESCAN_HIGH = 15   # ≥15 bright pixels → item definitely present
PRESCAN_MED  = 5    # 5-14 → possible item (hover but accept "empty" result)
# < 5 → skip entirely (empty slot)

# ── Tooltip backend weights (kept for _load_weights() compatibility) ─────────
WEIGHT_COLOR    = 2
WEIGHT_HSV      = 2
WEIGHT_AI       = 3
WEIGHT_OCR      = 1
VOTE_THRESHOLD  = 2   # legacy — no longer used for the confirm decision

# ── Consensus quorums ─────────────────────────────────────────────────────────
# read_tooltip_voted() now counts RAW VOTES (not weighted scores).
# CONFIRM_QUORUM: how many backends must independently say "confirm" before
#   we allow a click.  ≥2 prevents any single noisy backend from acting.
# DENY_QUORUM   : how many backends must say "deny" to skip the slot.
#   Kept at 2 — we're equally cautious about skipping the real confirm item.
# When AI is available it counts as one vote (very reliable, never double-counts).
CONFIRM_QUORUM = 2
DENY_QUORUM    = 2

# ── Slot icon template matching ───────────────────────────────────────────────
# Templates are 16×16 downsampled slot crops (box-averaged to 768 floats each).
# Built from attached_assets/attempts.jsonl + popup screenshots at startup,
# then accumulated online as the bot runs.  Persisted to disk after each update.
TEMPLATE_FILE          = None           # set at runtime: ASSETS_DIR/slot_templates.json
TEMPLATE_MAD_THRESHOLD = 70.0           # max MAD to accept a template match
TEMPLATE_MAD_MARGIN    = 18.0           # must beat the other label by this much
_slot_templates = {                     # averaged pixel arrays per label
    'confirm': None, 'deny': None,
    'n': {'confirm': 0, 'deny': 0},
}

# ── Minecraft UI colors ───────────────────────────────────────
# Inventory background gray  ≈ rgb(198,198,198) vanilla; custom packs can be 140-230
# Wide range intentional: catches JartexNetwork custom resource-pack grays AND slot interiors
C_GRAY  = ((100,100,100), (230,230,230))
# "Click to Confirm" title   §a = #55FF55 = rgb(85,255,85)
C_GREEN = ((48, 195, 48),  (135, 255, 135))
# "Do not click" title       §c = #FF5555 = rgb(255,85,85)
# Widened lower bound to catch dimmer/resource-pack-shifted red renders
C_RED   = ((120,  20, 20), (255, 120, 120))
# MC popup title bar + "Inventory" label dark background ≈ rgb(55,55,55)
# These dark bands separate the AFK test strip from the player inventory.
C_DARK  = ((10,  10,  10), (110, 110, 110))
# Chat early-warning: §6 Gold (#FFAA00 = rgb(255,170,0)) is used by JartexNetwork
# to color the "Console" prefix in server-generated messages like:
#   "Console teleported you to spawn."
# Tolerances widened by ±25 to handle anti-aliasing and JPEG compression.
C_CHAT_GOLD = ((200, 130,  0), (255, 200, 50))
# Chat area geometry as fractions of screen size (measured from screenshots):
#   x: left 45% of screen, y: bottom 20% of screen
CHAT_AREA_X_FRAC = 0.45   # width of chat area relative to screen width
CHAT_AREA_Y_FRAC = 0.80   # top of chat area relative to screen height (0=top, 1=bottom)

# ── Detect available methods once ─────────────────────────────
HAS_TESS     = subprocess.run(['which','tesseract'],capture_output=True).returncode==0
HAS_CONVERT  = subprocess.run(['which','convert'],capture_output=True).returncode==0
HAS_MAIM     = subprocess.run(['which','maim'],capture_output=True).returncode==0
API_KEY      = os.environ.get('ANTHROPIC_API_KEY','').strip()
HAS_AI       = bool(API_KEY)

# ── Startup validation — runs after all functions are defined (see bottom) ──

# ── Side-channel data filled during each sweep ────────────────
# read_tooltip_voted() fills this so sweep_strip can record per-backend votes
_last_vote_detail = {'color': None, 'hsv': None, 'ai': None, 'ocr': None, 'scores': {}}
# click_and_verify() fills this so sweep_strip can record click stats
_last_click_stats = {'elapsed_ms': 0, 'retries': 0, 'popup_closed': False}

# ═══════════════════════════════════════════════════════════════
#  PNG DECODER  — pure stdlib (struct + zlib), no Pillow needed
# ═══════════════════════════════════════════════════════════════
def decode_png(path):
    try:
        with open(path,'rb') as f:
            if f.read(8) != b'\x89PNG\r\n\x1a\n': return None
            idat,w,h,bpp = b'',0,0,3
            while True:
                hdr=f.read(8)
                if len(hdr)<8: break
                n=struct.unpack('>I',hdr[:4])[0]; t=hdr[4:]
                d=f.read(n); f.read(4)
                if   t==b'IHDR': w,h=struct.unpack('>II',d[:8]); bpp={2:3,6:4}.get(d[9],3)
                elif t==b'IDAT': idat+=d
                elif t==b'IEND': break
        raw=bytearray(zlib.decompress(idat)); stride=w*bpp
        def pt(a,b,c):
            p=a+b-c; pa,pb,pc=abs(p-a),abs(p-b),abs(p-c)
            return a if pa<=pb and pa<=pc else(b if pb<=pc else c)
        rows=[]; prev=bytearray(stride); i=0
        for _ in range(h):
            ft=raw[i]; i+=1; row=bytearray(raw[i:i+stride]); i+=stride
            if ft==1:
                for x in range(bpp,stride): row[x]=(row[x]+row[x-bpp])&255
            elif ft==2:
                row=bytearray((row[x]+prev[x])&255 for x in range(stride))
            elif ft==3:
                for x in range(stride):
                    a=row[x-bpp]if x>=bpp else 0; row[x]=(row[x]+(a+prev[x])//2)&255
            elif ft==4:
                for x in range(stride):
                    a=row[x-bpp]if x>=bpp else 0; b_=prev[x]; c=prev[x-bpp]if x>=bpp else 0
                    row[x]=(row[x]+pt(a,b_,c))&255
            rows.append(bytes(row)); prev=row
        return rows,w,h,bpp
    except Exception: return None

def count_color(rows, bpp, lo, hi):
    r0,g0,b0=lo; r1,g1,b1=hi; n=0
    for row in rows:
        for x in range(0,len(row),bpp):
            r,g,b=row[x],row[x+1],row[x+2]
            if r0<=r<=r1 and g0<=g<=g1 and b0<=b<=b1: n+=1
    return n

# ═══════════════════════════════════════════════════════════════
#  SCREENSHOT + MOUSE
# ═══════════════════════════════════════════════════════════════
def scrot(x=None,y=None,w=None,h=None):
    """
    Capture screen to SNAP.

    Capture strategy (best → fallback):
      1. maim -i <active_window_id>  — targets the Minecraft window directly,
         bypasses X11 compositor buffer, pixel-perfect even under Picom/Mutter.
         Research shows scrot reads stale compositor buffers; maim reads the
         window's actual pixels.  Only used when HAS_MAIM is True.
      2. scrot -z  — full display, may capture up to ~100ms stale frame under
         compositors, but works in fullscreen (no compositor) reliably.

    Region capture always falls back to scrot (maim region syntax differs).
    """
    if x is not None:
        # Region capture: scrot only (maim region syntax is different)
        subprocess.run(['scrot','-z','-a',f'{x},{y},{w},{h}',SNAP],capture_output=True)
    elif HAS_MAIM:
        # Full-screen capture via maim targeting the active window
        wid = subprocess.run(['xdotool','getactivewindow'],
                             capture_output=True, text=True).stdout.strip()
        if wid:
            subprocess.run(['maim','-i',wid,SNAP],capture_output=True)
        else:
            subprocess.run(['maim',SNAP],capture_output=True)
    else:
        subprocess.run(['scrot','-z',SNAP],capture_output=True)

def screen_wh():
    """Return (width, height) of the display. Tries xdotool → xrandr → None,None."""
    # 1. xdotool (fastest)
    o=subprocess.run(['xdotool','getdisplaygeometry'],capture_output=True,text=True).stdout.strip()
    parts=o.split()
    if len(parts)>=2:
        try: return int(parts[0]),int(parts[1])
        except ValueError: pass
    # 2. xrandr fallback
    try:
        import re as _re
        lines=subprocess.run(['xrandr','--query'],capture_output=True,text=True).stdout.splitlines()
        for ln in lines:
            if ' connected' in ln:
                m=_re.search(r'(\d{3,5})x(\d{3,5})\+0\+0',ln)
                if m: return int(m.group(1)),int(m.group(2))
    except Exception: pass
    # 3. Give up — caller will use full-screen scrot (still works)
    return None,None

def xdo(*a):
    subprocess.run(['xdotool']+list(a),capture_output=True)

def focus_mc_window():
    """
    Find and raise the Minecraft window so it owns the X11 input focus.

    WHY THIS IS CRITICAL:
      xdotool can move the cursor anywhere on screen even when Minecraft
      doesn't have focus, BUT Minecraft's Java AWT input queue only processes
      inventory clicks when the game window is the ACTIVE window.  Without
      focus, every click lands at the right screen coordinate yet the game
      silently ignores it — the popup stays open and we get a timeout kick.

    HOW IT WORKS:
      1. 'xdotool search --name Minecraft' returns all window IDs whose title
         contains "Minecraft".  On a fresh install there is exactly one.
      2. windowfocus --sync  → tells X11 to give keyboard+pointer focus.
      3. windowactivate --sync → also raises the window to the top of the
         stacking order (prevents it being obscured by a terminal/IDE).
      4. 100ms settle pause → ensures the OS has transferred focus before the
         first mousemove event, which some X11 compositors delay slightly.

    Returns True if a Minecraft window was found and focused, False if not.
    Running on a headless / non-X11 system always returns False gracefully.
    """
    try:
        result = subprocess.run(
            ['xdotool', 'search', '--name', MC_WINDOW_TITLE],
            capture_output=True, text=True, timeout=3
        )
        wids = [w.strip() for w in result.stdout.splitlines() if w.strip()]
        if not wids:
            print(f"  [focus] no window named '{MC_WINDOW_TITLE}' — is Minecraft running?")
            return False
        wid = wids[-1]   # last match = most recently created/active
        subprocess.run(['xdotool', 'windowfocus',   '--sync', wid],
                       capture_output=True, timeout=2)
        subprocess.run(['xdotool', 'windowactivate','--sync', wid],
                       capture_output=True, timeout=2)
        # Research-confirmed: Minecraft's LWJGL needs 200ms to process the X11
        # focus transfer before it will accept XTEST mouse/click events.
        # 100ms was marginal; 200ms is the value cited in xdotool+LWJGL guides.
        time.sleep(0.20)
        print(f"  [focus] MC window (id={wid}) focused ✓")
        return True
    except Exception as e:
        print(f"  [focus] could not focus MC window: {e}")
        return False

# ── Cursor position tracking ──────────────────────────────────────────────────
_cur_x = None
_cur_y = None

def _sync_cursor():
    """Read actual cursor position from X11 and update tracking vars."""
    global _cur_x, _cur_y
    out = subprocess.run(['xdotool','getmouselocation','--shell'],
                         capture_output=True, text=True).stdout
    for ln in out.splitlines():
        if ln.startswith('X='): _cur_x = int(ln[2:])
        elif ln.startswith('Y='): _cur_y = int(ln[2:])

# ── WindMouse cursor movement ─────────────────────────────────────────────────
# Research finding: cosine-ease moves in a PERFECTLY STRAIGHT LINE with
# variable speed.  Anti-cheat systems detect this via:
#   • Zero angular variety (all steps same direction)
#   • Discrete angular distribution (0°, 45°, 90°, etc.)
#   • Low event count (< 100 events/second)
#
# WindMouse (BenLand100, 2021) models the cursor as a physical particle pulled
# toward the target by gravity G while a smoothly-changing wind W perturbs it
# sideways.  This produces:
#   • Curved, non-linear paths → continuous angular distribution
#   • Bell-curve velocity (slow at start/end, fast in middle)
#   • ~100 Hz update rate (10 ms per step)
#   • Per-step timing jitter ±2 ms → non-periodic events
#
# Parameters are distance-adaptive so short hover-spiral moves (< 30 px) use
# low wind (precision) while cross-screen moves (> 150 px) use full wind.
#
# Reference: https://ben.land/post/2021/04/25/windmouse-human-mouse-movement/
# ─────────────────────────────────────────────────────────────────────────────
def _wind_mouse(x0, y0, x1, y1, G=9.0, W=3.0, M=15.0, D=12.0):
    """
    Generate a sequence of (x, y) waypoints from (x0,y0) to (x1,y1).

    Physics:
      vx,vy  = velocity (updated each step)
      wx,wy  = wind (random walk, decays near target)
      G      = gravity magnitude — constant pull toward (x1,y1)
      W      = wind magnitude — lateral random force
      M      = max speed (px per step) — caps velocity
      D      = damping start distance — M reduced to dist/2 inside D
    """
    sqrt3 = math.sqrt(3.0)
    sqrt5 = math.sqrt(5.0)
    cx, cy   = float(x0), float(y0)
    vx = vy  = 0.0
    wx = wy  = 0.0
    pts = []
    while True:
        dist = math.hypot(x1 - cx, y1 - cy)
        if dist < 1.0:
            break
        W_cur = min(W, dist)
        if dist >= D:
            wx = wx / sqrt3 + (2.0 * random.random() - 1.0) * W_cur / sqrt5
            wy = wy / sqrt3 + (2.0 * random.random() - 1.0) * W_cur / sqrt5
        else:
            wx /= sqrt3
            wy /= sqrt3
        vx = vx + wx + G * (x1 - cx) / dist
        vy = vy + wy + G * (y1 - cy) / dist
        spd = math.hypot(vx, vy)
        cap = min(M, dist / 2.0) if dist < D else M
        if spd > cap:
            vx = vx * cap / spd
            vy = vy * cap / spd
        cx += vx; cy += vy
        pts.append((round(cx), round(cy)))
    # Guarantee landing exactly on target
    if not pts or pts[-1] != (round(x1), round(y1)):
        pts.append((round(x1), round(y1)))
    return pts

def smooth_move(tx, ty):
    """
    Move cursor from current position to (tx, ty) via WindMouse.

    Distance-adaptive parameters:
      < 30 px   : G=9 W=1 M=8  D=8   precision (hover spiral fine-tuning)
      30-150 px : G=8 W=3 M=15 D=12  normal
      ≥ 150 px  : G=7 W=5 M=20 D=20  full realism (cross-screen moves)

    Update rate: ~100 Hz (10 ms/step ± 2 ms jitter) — non-periodic events.
    """
    global _cur_x, _cur_y
    if _cur_x is None: _sync_cursor()
    cx = _cur_x if _cur_x is not None else tx
    cy = _cur_y if _cur_y is not None else ty
    dist = math.hypot(tx - cx, ty - cy)
    if dist < 1.5:
        _cur_x, _cur_y = tx, ty; return

    if   dist <  30: G, W, M, D = 9.0, 1.0,  8.0,  8.0
    elif dist < 150: G, W, M, D = 8.0, 3.0, 15.0, 12.0
    else:            G, W, M, D = 7.0, 5.0, 20.0, 20.0

    for nx, ny in _wind_mouse(cx, cy, tx, ty, G, W, M, D):
        xdo('mousemove', str(nx), str(ny))
        time.sleep(0.010 + random.uniform(-0.002, 0.002))   # ~100 Hz ± jitter

    _cur_x, _cur_y = tx, ty

def _wait_for_tooltip(timeout=None):
    """
    Poll for a tooltip in SNAP after a hover move.
    Returns True as soon as tooltip pixels are detected; False on timeout.

    Timing notes (from X11 capture research):
      • MC renders tooltips within 1 game tick = 50 ms at 20 TPS.
      • scrot latency ≈ 99 ms; maim ≈ 134 ms.
      • Therefore: wait 60 ms first (tooltip MUST be rendered by then),
        then poll every 65 ms until deadline.
      • With HOVER_WAIT=0.30 s: 0.30 − 0.06 = 0.24 s window → 3-4 polls.
    """
    t = timeout if timeout is not None else HOVER_WAIT
    time.sleep(0.06)   # Give MC 60 ms to render the tooltip (> 1 game tick)
    deadline = time.time() + max(0.0, t - 0.06)
    while True:
        _snap_tooltip(0, 0, 9999, 9999)
        if has_tooltip():
            return True
        if time.time() >= deadline:
            break
        time.sleep(0.065)
    return False

# ═══════════════════════════════════════════════════════════════
#  FIND THE "AFK GRINDING" STRIP  (full-screen, smart separator)
#
#  Detection pipeline:
#   1. Screenshot 90% of screen — popup can be anywhere, not just center.
#   2. Find widest contiguous gray horizontal run → popup L/R + row Y.
#   3. Walk that column up/down to get precise popup top/bottom.
#   4. Row-by-row gray-fraction scan within the popup:
#        • "Afk Grinding" title bar  : dark rows  (< 18 % gray)
#        • AFK test slot rows         : gray rows  (> 40 % gray)
#        • "Inventory" separator      : dark rows  (< 18 % gray)  ← split here
#        • Player inventory rows      : gray rows  (> 40 % gray)  ← ignored
#   5. strip_top = first gray row after title dark band.
#      strip_bot = first dark row after AFK slot gray band.
#      Falls back to slot*1.1 / 47% ratio if either is inconclusive.
#
#  Returns (strip_left, strip_top, strip_right, strip_bottom, slot_px)
#  all in SCREEN coordinates, or None if no popup found.
# ═══════════════════════════════════════════════════════════════
def find_afk_strip():
    """
    Locate the AFK Grinding popup's item strip in the current screenshot.

    Minecraft GUI structure for the "Afk Grinding" check (GUI scale 2):
      ┌─────────────────────────────────────┐  ← dark title bar (~14px tall)
      │           Afk Grinding              │    colour: near-black rgb(30,20,30)
      ├─────────────────────────────────────┤  ← strip_top (first AFK item row)
      │  [slot][slot][slot]...(9 cols wide) │
      │  [slot][slot][slot]...(up to 3 rows)│
      ├─────────────────────────────────────┤  ← "Inventory" separator label
      │  [player inventory rows...]         │  ← NOT part of the AFK strip
      └─────────────────────────────────────┘

    Detection strategy:
      • Scan each row for the longest contiguous run of C_GRAY pixels.
        The popup background is a solid mid-gray; world terrain rarely has
        a horizontal gray run longer than 75 px.
      • Walk that center column up/down to find the full popup bounding box.
      • Inside the bbox, find the dark "Inventory" separator label to locate
        exactly where the AFK rows end and player inventory begins.
      • Slot width = popup_width / 9; slot height = strip_height / 3.

    Returns (strip_left, strip_top, strip_right, strip_bottom, slot_w, slot_h)
    or None if the popup is not visible.
    """
    sw,sh = screen_wh()

    # ── 1. Capture screen — use 90% crop when size is known, full screen otherwise ──
    if sw and sh:
        ox=int(sw*0.05); oy=int(sh*0.05)
        cw=sw-2*ox;      ch=sh-2*oy
        scrot(ox,oy,cw,ch)
    else:
        ox=0; oy=0
        scrot()   # full-screen capture; popup will still be found
    r=decode_png(SNAP)
    if r is None: return None
    rows,iw,ih,bpp=r

    # ── 2. Find widest contiguous gray run (= popup background) ──────
    best=None; bw=0
    for iy in range(0,ih,2):
        row=rows[iy]; rs=None; rlen=0
        for ix in range(iw):
            rr,gg,bb=row[ix*bpp],row[ix*bpp+1],row[ix*bpp+2]
            ok=(C_GRAY[0][0]<=rr<=C_GRAY[1][0] and
                C_GRAY[0][1]<=gg<=C_GRAY[1][1] and
                C_GRAY[0][2]<=bb<=C_GRAY[1][2])
            if ok:
                if rs is None: rs=ix
                rlen+=1
            else:
                if rlen>bw and rlen>=75:
                    bw=rlen; mcx=rs+rlen//2
                    # ── 3. Walk center column up to popup top ──────
                    top=iy
                    while top>0:
                        rr2=rows[top-1][mcx*bpp]; gg2=rows[top-1][mcx*bpp+1]
                        if C_GRAY[0][0]<=rr2<=C_GRAY[1][0] and C_GRAY[0][1]<=gg2<=C_GRAY[1][1]:
                            top-=1
                        else: break
                    # ── 3. Walk center column down to popup bottom ─
                    bot=iy
                    while bot<ih-1:
                        rr2=rows[bot+1][mcx*bpp]; gg2=rows[bot+1][mcx*bpp+1]
                        if C_GRAY[0][0]<=rr2<=C_GRAY[1][0] and C_GRAY[0][1]<=gg2<=C_GRAY[1][1]:
                            bot+=1
                        else: break
                    if bot-top>=40:
                        # Store in screen coords
                        pl=ox+rs; pt=oy+top; pr=ox+rs+rlen; pb=oy+bot
                        best=(pl,pt,pr,pb)
                rs=None; rlen=0

    if best is None: return None
    pl,pt,pr,pb = best
    pw=pr-pl; ph=pb-pt

    # Sanity: popup should be at least 80px wide and 50px tall
    if pw<80 or ph<50: return None

    # ── Slot dimensions (measured from screenshots) ───────────────────
    slot_w = max(10, min(round(pw / 9), 60))
    SLOT_H = SLOT_H_DEFAULT   # fallback used for MIN_STRIP_H; refined below

    # ── 4. Row-by-row gray-fraction scan to locate the two dark bands ─
    DARK_T = 0.18
    GRAY_T = 0.40

    # Convert popup screen coords → image coords
    pt_i = pt-oy; pb_i = pb-oy
    pl_i = pl-ox; pr_i = pr-ox

    state = 'start'
    title_end_rel = None
    inv_sep_rel   = None

    for rel in range(min(ph, pb_i-pt_i)+1):
        iy = pt_i + rel
        if iy >= ih: break
        row = rows[iy]
        gray = 0; span = max(1, pr_i - pl_i)
        for ix in range(pl_i, min(pr_i, iw)):
            rr=row[ix*bpp]; gg=row[ix*bpp+1]; bb=row[ix*bpp+2]
            if (C_GRAY[0][0]<=rr<=C_GRAY[1][0] and
                C_GRAY[0][1]<=gg<=C_GRAY[1][1] and
                C_GRAY[0][2]<=bb<=C_GRAY[1][2]):
                gray+=1
        gf = gray / span

        if state == 'start':
            if gf < DARK_T:   state = 'title_dark'
            elif gf >= GRAY_T: state = 'afk_slots'; title_end_rel = rel
        elif state == 'title_dark':
            if gf >= GRAY_T:  state = 'afk_slots'; title_end_rel = rel
        elif state == 'afk_slots':
            if gf < DARK_T:
                inv_sep_rel = rel
                break

    # ── 5a. Secondary separator: scan for "Inventory" white text rows ───
    if inv_sep_rel is None:
        lo_rel = int(ph * 0.42)
        hi_rel = int(ph * 0.52)
        for rel in range(lo_rel, min(hi_rel, pb_i-pt_i)+1):
            iy = pt_i + rel
            if iy >= ih: break
            row = rows[iy]
            white = 0; span = max(1, pr_i - pl_i)
            for ix in range(pl_i, min(pr_i, iw)):
                rr=row[ix*bpp]; gg=row[ix*bpp+1]; bb=row[ix*bpp+2]
                if rr>=250 and gg>=250 and bb>=250:
                    white += 1
            if white / span > 0.08:
                inv_sep_rel = rel
                break

    # ── 5b. Build final strip coordinates ─────────────────────────────
    if title_end_rel is not None:
        strip_top = pt + title_end_rel
    else:
        strip_top = pt + 18

    MIN_STRIP_H = 3 * SLOT_H

    if inv_sep_rel is not None:
        raw_bot = pt + inv_sep_rel
        strip_bot = max(raw_bot, strip_top + MIN_STRIP_H)
        print(f"  Popup {pw}×{ph}px  slot_w={slot_w}px  SLOT_H={SLOT_H}px  "
              f"title_end={title_end_rel}px  inv_sep={inv_sep_rel}px  [exact]")
    else:
        raw_bot = pt + int(ph * 0.49)
        strip_bot = max(raw_bot, strip_top + MIN_STRIP_H)
        print(f"  Popup {pw}×{ph}px  slot_w={slot_w}px  SLOT_H={SLOT_H}px  "
              f"title_end={title_end_rel}px  sep=not found [49% fallback]")

    if strip_bot <= strip_top: return None

    # Recalculate slot height from the measured strip bounds.
    # The strip holds exactly 3 rows — this makes the value correct at
    # any GUI scale (1, 2, 3, 4) and any screen resolution automatically.
    SLOT_H = max(10, min(60, round((strip_bot - strip_top) / 3)))

    strip_left  = pl + (pw - 9*slot_w) // 2
    strip_right = strip_left + 9*slot_w

    return (strip_left, strip_top, strip_right, strip_bot, slot_w, SLOT_H)

# ═══════════════════════════════════════════════════════════════
#  TOOLTIP READING — AI → OCR → color (best to worst)
# ═══════════════════════════════════════════════════════════════

_PROMPT = """You are watching a Minecraft anti-AFK check.

Think step by step:
1. Find the tooltip box near the mouse cursor (dark purple background).
2. Read the FIRST LINE (the title) of that tooltip.
3. Decide:
   - Title says "Click to Confirm" in GREEN text  → reply: CONFIRM
   - Title says "Do not click"    in RED text     → reply: DENY
   - No tooltip visible at all                    → reply: EMPTY

Reply with exactly one word — CONFIRM, DENY, or EMPTY."""

def ask_ai(path):
    try:
        with open(path,'rb') as f: img=base64.b64encode(f.read()).decode()
        body={"model":"claude-haiku-4-5","max_tokens":8,
              "messages":[{"role":"user","content":[
                  {"type":"image","source":{"type":"base64","media_type":"image/png","data":img}},
                  {"type":"text","text":_PROMPT}]}]}
        from urllib.request import urlopen,Request
        req=Request("https://api.anthropic.com/v1/messages",
                    data=json.dumps(body).encode(),
                    headers={"x-api-key":API_KEY,
                             "anthropic-version":"2023-06-01",
                             "content-type":"application/json"})
        resp=json.loads(urlopen(req,timeout=6).read())
        w=resp["content"][0]["text"].strip().upper()
        if "CONFIRM" in w: return "confirm"
        if "DENY" in w:    return "deny"
        return "empty"
    except Exception as e:
        print(f"    [AI] err: {e}"); return None

def ask_ocr(path):
    try:
        r=subprocess.run(['tesseract',path,'stdout','--psm','6'],
                         capture_output=True,text=True,timeout=3)
        t=r.stdout.lower()
        if 'confirm' in t:                              return 'confirm'
        if 'do not' in t or ('not' in t and 'click' in t): return 'deny'
        return None
    except Exception: return None

def _tooltip_title_rows(rows, ih, bpp):
    """
    Locate the tooltip bounding box by scanning for dark-purple background
    pixels, then return ONLY the top ~25% (the item name / title line).
    Scanning just the title zone eliminates false positives from item
    textures and environmental colors in the rest of the screenshot.
    Falls back to the full image if no tooltip background is found.
    """
    tt = -1; tb = -1
    for iy in range(ih):
        if count_color([rows[iy]], bpp, C_TOOLTIP_BG[0], C_TOOLTIP_BG[1]) >= 3:
            if tt < 0: tt = iy
            tb = iy
    if tt < 0 or tb - tt < 6:
        return rows          # no tooltip box visible — scan everything
    title_bot = tt + max(8, (tb - tt) // 3)   # top 33% (was 25%) — ensures title line included
    return rows[tt:title_bot]

def ask_color(path=None):
    """RGB range scan restricted to the tooltip title zone."""
    r=decode_png_cached()
    if r is None: return "empty"
    rows,_,ih,bpp=r
    scan = _tooltip_title_rows(rows, ih, bpp)
    g =count_color(scan, bpp, C_GREEN[0], C_GREEN[1])
    rd=count_color(scan, bpp, C_RED[0],   C_RED[1])
    if g>=5 and g>rd:  return "confirm"
    if rd>=5 and rd>g: return "deny"
    return "empty"

# ── HSV backend helpers ───────────────────────────────────────────────────────
def _rgb_to_h(r, g, b):
    """Return hue 0-360 from 0-255 RGB.  Returns -1 for achromatic pixels."""
    r,g,b = r/255,g/255,b/255
    mx=max(r,g,b); mn=min(r,g,b); d=mx-mn
    if d<0.15 or mx<0.25: return -1   # achromatic or too dark — skip
    if mx==r: h=60*((g-b)/d%6)
    elif mx==g: h=60*((b-r)/d+2)
    else:        h=60*((r-g)/d+4)
    return h

def ask_hsv(path=None):
    """
    Hue-space classification inside the tooltip title zone.
    Green MC text (§a #55FF55): hue ≈ 120°  → hue 90-160
    Red   MC text (§c #FF5555): hue ≈ 0/360° → hue <20 or >340
    Orthogonal to the RGB range scan — catches cases where brightness or
    gamma shift pushes the color outside the RGB boxes.
    Pure stdlib, no dependencies.
    """
    r=decode_png_cached()
    if r is None: return "empty"
    rows,_,ih,bpp=r
    scan = _tooltip_title_rows(rows, ih, bpp)
    gn=0; rn=0
    for row in scan:
        for ix in range(0, len(row), bpp):
            h=_rgb_to_h(row[ix], row[ix+1], row[ix+2])
            if h<0: continue
            if  90<=h<=160: gn+=1
            elif h<=20 or h>=340: rn+=1
    if gn>=3 and gn>rn: return "confirm"
    if rn>=3 and rn>gn: return "deny"
    return "empty"

def ask_ratio(path=None):
    """
    Channel dominance ratio — completely independent of absolute brightness.
    For every non-dark pixel in the tooltip title zone computes which channel
    (R or G) accounts for the largest fraction of total brightness.
    G/(R+G+B) > 0.45 AND G > 80  →  green vote
    R/(R+G+B) > 0.45 AND R > 80  →  red vote
    Catches resource-pack recolours that shift brightness but keep hue intact.
    """
    r=decode_png_cached()
    if r is None: return "empty"
    rows,_,ih,bpp=r
    scan=_tooltip_title_rows(rows,ih,bpp)
    gn=rn=0
    for row in scan:
        for ix in range(0,len(row),bpp):
            rv=row[ix]; gv=row[ix+1]; bv=row[ix+2]
            total=rv+gv+bv
            if total<120: continue   # too dark to classify
            if gv/total>0.45 and gv>80: gn+=1
            elif rv/total>0.45 and rv>80: rn+=1
    if gn>=4 and gn>rn: return "confirm"
    if rn>=4 and rn>gn: return "deny"
    return "empty"

def ask_runs(path=None):
    """
    Longest consecutive colored-pixel run in the tooltip title zone.
    MC text characters form contiguous horizontal runs of green or red
    pixels; isolated noise pixels do not.  Finding a run ≥ 3 pixels
    long means there's actual text, not a stray artifact.
    Threshold ≥ 3 is intentionally low — even a single letter produces a
    run of 2-6 px at GUI scale 1 and 4-12 px at GUI scale 2.
    """
    r=decode_png_cached()
    if r is None: return "empty"
    rows,_,ih,bpp=r
    scan=_tooltip_title_rows(rows,ih,bpp)
    max_g=max_r=0
    for row in scan:
        gr=rr=0
        for ix in range(0,len(row),bpp):
            rv=row[ix]; gv=row[ix+1]; bv=row[ix+2]
            is_g = gv>rv+40 and gv>bv+40 and gv>100
            is_r = rv>gv+40 and rv>bv+40 and rv>100
            if is_g:
                gr+=1; rr=0
                if gr>max_g: max_g=gr
            elif is_r:
                rr+=1; gr=0
                if rr>max_r: max_r=rr
            else:
                gr=rr=0
    if max_g>=3 and max_g>max_r: return "confirm"
    if max_r>=3 and max_r>max_g: return "deny"
    return "empty"

# ═══════════════════════════════════════════════════════════════
#  SLOT ICON TEMPLATE MATCHING — pure stdlib, no AI, no OCR
#
#  Each slot is downsampled to a 16×16 box average (768 RGB floats).
#  Templates are built from attached_assets/attempts.jsonl at startup
#  and accumulated online during each run.
# ═══════════════════════════════════════════════════════════════
def _downsample(rows, x0, y0, w, h, bpp, size=16):
    """Box-average a crop to size×size. Returns flat list of size²×3 floats."""
    out = []
    for ty in range(size):
        sy0 = y0 + ty * h // size
        sy1 = y0 + (ty + 1) * h // size
        for tx in range(size):
            sx0 = x0 + tx * w // size
            sx1 = x0 + (tx + 1) * w // size
            rs = gs = bs = n = 0
            for sy in range(max(0, sy0), min(len(rows), sy1)):
                row = rows[sy]
                row_len = len(row) // bpp
                for sx in range(max(0, sx0), min(row_len, sx1)):
                    rs += row[sx * bpp]
                    gs += row[sx * bpp + 1]
                    bs += row[sx * bpp + 2]
                    n += 1
            if n: out.extend([rs / n, gs / n, bs / n])
            else: out.extend([128.0, 128.0, 128.0])
    return out   # length = size * size * 3 = 768

def _template_mad(a, b):
    """Mean absolute difference between two flat pixel lists."""
    if not a or not b or len(a) != len(b): return 999.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)

def _load_weights():
    """
    Override hardcoded backend weights from attached_assets/weights.json.
    That file is produced by join_training_data.py after you give it both
    the script's attempts.jsonl and the mod's afkverify_events.jsonl.
    If the file is absent or malformed the hardcoded defaults stay in place.
    """
    global WEIGHT_COLOR, WEIGHT_HSV, WEIGHT_AI, WEIGHT_OCR
    path = os.path.join(ASSETS_DIR, 'weights.json')
    try:
        with open(path) as f:
            w = json.load(f)
        WEIGHT_COLOR = int(w.get('color', WEIGHT_COLOR))
        WEIGHT_HSV   = int(w.get('hsv',   WEIGHT_HSV))
        WEIGHT_AI    = int(w.get('ai',    WEIGHT_AI))
        WEIGHT_OCR   = int(w.get('ocr',   WEIGHT_OCR))
        n   = w.get('n_popups', '?')
        acc = w.get('accuracy', {})
        def _pct(k):
            v = acc.get(k, {}).get('precision')
            return f'{v:.0%}' if isinstance(v, float) else '?'
        print(f"[ weights ] Loaded (n={n} popups) — "
              f"color={WEIGHT_COLOR}({_pct('color')})  "
              f"HSV={WEIGHT_HSV}({_pct('hsv')})  "
              f"AI={WEIGHT_AI}({_pct('ai')})  "
              f"OCR={WEIGHT_OCR}({_pct('ocr')})")
    except FileNotFoundError:
        pass   # no weights.json yet — use hardcoded defaults silently
    except Exception as e:
        print(f"[ weights ] Error loading weights.json: {e} — using defaults")

def _load_templates():
    """Load persisted slot icon templates from ASSETS_DIR/slot_templates.json."""
    global _slot_templates, TEMPLATE_FILE
    TEMPLATE_FILE = os.path.join(ASSETS_DIR, 'slot_templates.json')
    try:
        with open(TEMPLATE_FILE) as f:
            data = json.load(f)
        _slot_templates.update(data)
        n = _slot_templates.get('n', {})
        if _slot_templates.get('confirm') or _slot_templates.get('deny'):
            print(f"[ templates ] Loaded — confirm×{n.get('confirm',0)}  "
                  f"deny×{n.get('deny',0)}")
    except Exception:
        pass   # no templates yet — will bootstrap below

def _save_templates():
    """Persist template averages to disk."""
    try:
        if TEMPLATE_FILE:
            with open(TEMPLATE_FILE, 'w') as f:
                json.dump(_slot_templates, f)
    except Exception:
        pass

def _update_template(label, flat_pixels):
    """
    Online running average — incorporate a new slot icon crop into the
    existing template for 'label'.  Saves to disk immediately so the next
    session starts with the accumulated knowledge.
    """
    n = _slot_templates['n'].get(label, 0)
    existing = _slot_templates.get(label)
    if existing is None or len(existing) != len(flat_pixels):
        _slot_templates[label] = flat_pixels[:]
    else:
        _slot_templates[label] = [
            e + (p - e) / (n + 1)
            for e, p in zip(existing, flat_pixels)
        ]
    _slot_templates['n'][label] = n + 1
    _save_templates()

def _bootstrap_templates_from_assets():
    """
    Cold-start bootstrap: scan attempts.jsonl for attempts the user
    confirmed were correct, then extract slot icon crops from the saved
    popup screenshots to build initial 'confirm' and 'deny' templates.

    Uses the image data already present in attached_assets/ — no new
    screenshots needed, no AI, no external dependencies.
    """
    attempts_path = os.path.join(ASSETS_DIR, 'attempts.jsonl')
    if not os.path.exists(attempts_path):
        return

    bootstrapped = 0
    with open(attempts_path) as f:
        for line in f:
            try:
                rec = json.loads(line.strip())
            except Exception:
                continue

            if rec.get('user_feedback') != 'correct':
                continue

            img_path = os.path.join(ASSETS_DIR, rec.get('screenshot', ''))
            if not os.path.exists(img_path):
                continue

            img = decode_png(img_path)
            if img is None:
                continue
            img_rows, iw, ih, bpp = img

            strip = rec.get('strip', {})
            sl = strip.get('left', 0)
            st = strip.get('top', 0)
            sw = strip.get('slot_w', 18)
            sh_s = strip.get('slot_h', 35)

            # Find the clicked confirm slot and up to 3 deny slots
            confirm_pos = None
            deny_positions = []
            for slot in rec.get('slots_inspected', []):
                r, c = slot.get('row', -1), slot.get('col', -1)
                if r < 0 or c < 0: continue
                if slot.get('clicked') and slot.get('final_answer') == 'confirm':
                    confirm_pos = (r, c)
                elif slot.get('final_answer') == 'deny' and len(deny_positions) < 3:
                    deny_positions.append((r, c))

            if confirm_pos is None:
                continue

            def _extract_slot(row, col):
                pad = max(1, sw // 10)
                x0 = sl + col * sw + pad
                y0 = st + row * sh_s + pad
                wi = sw - 2 * pad
                hi = sh_s - 2 * pad
                if x0 < 0 or y0 < 0 or x0 + wi > iw or y0 + hi > ih:
                    return None
                return _downsample(img_rows, x0, y0, wi, hi, bpp)

            flat = _extract_slot(*confirm_pos)
            if flat: _update_template('confirm', flat)
            for pos in deny_positions:
                flat = _extract_slot(*pos)
                if flat: _update_template('deny', flat)

            bootstrapped += 1

    if bootstrapped:
        n = _slot_templates.get('n', {})
        print(f"[ templates ] Bootstrapped from {bootstrapped} attempt(s) — "
              f"confirm×{n.get('confirm',0)}  deny×{n.get('deny',0)}")

def match_slot_from_prescan(rows, iw, ih, bpp, row, col, slot_w, slot_h):
    """
    Compare a slot's icon in the prescan image against reference templates.

    The prescan image is cropped with origin at strip (left, top), so
    slot (row, col) starts at image pixel (col*slot_w, row*slot_h).

    Returns 'confirm', 'deny', or None when confidence is too low.
    A positive return means the slot can be classified WITHOUT hovering.
    """
    if _slot_templates.get('confirm') is None or _slot_templates.get('deny') is None:
        return None   # not enough templates yet

    pad = max(1, slot_w // 10)
    x0 = col * slot_w + pad
    y0 = row * slot_h + pad
    w  = slot_w - 2 * pad
    h  = slot_h - 2 * pad
    if x0 + w > iw or y0 + h > ih or w < 4 or h < 4:
        return None

    flat = _downsample(rows, x0, y0, w, h, bpp)
    c_mad = _template_mad(flat, _slot_templates['confirm'])
    d_mad = _template_mad(flat, _slot_templates['deny'])

    # Both must be reasonable (not just "other template is terrible")
    best = min(c_mad, d_mad)
    if best > TEMPLATE_MAD_THRESHOLD: return None
    if c_mad + TEMPLATE_MAD_MARGIN <= d_mad: return 'confirm'
    if d_mad + TEMPLATE_MAD_MARGIN <= c_mad: return 'deny'
    return None   # too close to call

# Tooltip background: MC renders it as very dark near-black with slight purple tint.
# Measured from JartexNetwork screenshots: rgb(27,12,27), rgb(23,8,23), rgb(30,15,30).
# Max G widened from 10→18 to catch rgb(27,12,27) variants.
# Game world dark bg is rgb(24,22,22) — g=22 > 18, so still excluded correctly.
C_TOOLTIP_BG = ((0,0,0),(50,18,50))

def has_tooltip(path=None):
    r=decode_png_cached()
    if r is None: return False
    rows,_,_,bpp=r
    # Primary: dark-purple tooltip background pixels (≥8 is enough for a partial frame)
    if count_color(rows,bpp,C_TOOLTIP_BG[0],C_TOOLTIP_BG[1]) >= 8:
        return True
    # Fallback: detect tooltip by its colored title text directly.
    # Robust when tooltip bg is near-invisible or partially off-screen.
    # Threshold 8 = at least 2 letters worth of colored pixels at GUI scale 2.
    if count_color(rows,bpp,C_GREEN[0],C_GREEN[1]) >= 8:
        return True
    if count_color(rows,bpp,C_RED[0],C_RED[1]) >= 8:
        return True
    return False

def _snap_tooltip(mx, my, sw, sh, slot_w=29):
    """Full-screen capture.
    The tooltip can appear anywhere — left/right/above the slot depending on
    screen position — so capturing the whole screen guarantees it is always
    in frame.  decode_png_cached() means this costs one decode, not three.
    mx/my/sw/sh/slot_w kept as parameters so callers need no changes.
    """
    scrot()   # full-screen → SNAP; decode_png_cached() re-decodes on next read

# ═══════════════════════════════════════════════════════════════
#  STRATEGY 1: HOVER WITH SPIRAL FALLBACK
# ═══════════════════════════════════════════════════════════════
def hover_spiral(sx, sy, sw, sh, slot_w=29):
    """
    Smoothly move to each spiral position and poll for tooltip appearance.
    Returns (True, mx, my) as soon as a tooltip is detected.
    Falls back to (False, sx, sy) if none of the spiral positions work.

    Timeout strategy:
      Position 0 (center): full HOVER_WAIT (0.30 s) — most items show here
      Positions 1-8 (ring 1): HOVER_WAIT * 0.5 (0.15 s) — short but fair
      Positions 9-16 (ring 2): HOVER_WAIT * 0.4 (0.12 s) — quick last resort

    Worst-case (no tooltip found at any of 17 positions):
      0.30 + 8×0.15 + 8×0.12 = 0.30 + 1.20 + 0.96 = 2.46 s per empty slot
    vs the old fixed 17×0.30 = 5.10 s.  This keeps sweep time well under
    the 5.5 s SWEEP_TIMEOUT even with multiple empty slots.
    """
    for i, (dx, dy) in enumerate(HOVER_SPIRAL):
        nx = max(0, min(sw-1, sx+dx))
        ny = max(0, min(sh-1, sy+dy))
        smooth_move(nx, ny)
        if i == 0:
            t = HOVER_WAIT              # center: full patience
        elif i <= 8:
            t = HOVER_WAIT * 0.5       # ring 1: half
        else:
            t = HOVER_WAIT * 0.4       # ring 2: quick
        if _wait_for_tooltip(t):
            return True, nx, ny
    return False, sx, sy

# ═══════════════════════════════════════════════════════════════
#  STRATEGY 2: QUORUM-BASED TOOLTIP READING  (5 methods)
#
#  All available backends are polled.  We count raw votes, not
#  weighted scores:
#    CONFIRM_QUORUM (≥2) backends must say "confirm" → click
#    DENY_QUORUM    (≥2) backends must say "deny"    → skip
#    Otherwise                                       → "empty"
#
#  This prevents any single noisy backend from triggering a click.
#  Backends 1-4 are always available (pure stdlib, no deps).
#  Backend 5 (AI) runs only when ANTHROPIC_API_KEY is set.
#  Backend 6 (OCR) runs only when tesseract is installed.
# ═══════════════════════════════════════════════════════════════
def read_tooltip_voted():
    global _last_vote_detail
    votes = {}

    # ── Backend 1: RGB absolute range scan ───────────────────────────────
    votes['color'] = ask_color()

    # ── Backend 2: HSV hue-space scan ────────────────────────────────────
    votes['hsv'] = ask_hsv()

    # ── Backend 3: Channel dominance ratio ───────────────────────────────
    votes['ratio'] = ask_ratio()

    # ── Backend 4: Longest consecutive colored-pixel run ─────────────────
    votes['runs'] = ask_runs()

    # ── Backend 5: AI vision (optional) ──────────────────────────────────
    if HAS_AI:
        votes['ai'] = ask_ai(SNAP)

    # ── Backend 6: OCR (optional) ────────────────────────────────────────
    if HAS_TESS:
        votes['ocr'] = ask_ocr(SNAP)

    nc = sum(1 for v in votes.values() if v == 'confirm')
    nd = sum(1 for v in votes.values() if v == 'deny')
    total = len(votes)

    _last_vote_detail = dict(votes)
    _last_vote_detail['scores'] = {'confirm': nc, 'deny': nd, 'total': total}

    tag = '  '.join(f"{k}={v[0]}" for k,v in votes.items())
    print(f"    [{total} backends] {tag}  →  c={nc} d={nd}")

    if nc >= CONFIRM_QUORUM:
        return "confirm"
    if nd >= DENY_QUORUM:
        return "deny"
    return "empty"

# ═══════════════════════════════════════════════════════════════
#  STRATEGY 3: DOUBLE-CHECK CONFIRM BEFORE CLICKING
# ═══════════════════════════════════════════════════════════════
def double_check_confirm(mx, my, sw, sh, slot_w=29):
    # Drift slightly off-center then smooth back — natural "re-check" motion
    jx = mx + random.randint(-3, 3)
    jy = my + random.randint(-3, 3)
    smooth_move(jx, jy)
    time.sleep(random.uniform(0.04, 0.07))
    smooth_move(mx, my)
    if not _wait_for_tooltip(HOVER_WAIT):
        print("    [double-check] tooltip gone — skipping")
        return False
    second = read_tooltip_voted()
    if second != "confirm":
        print(f"    [double-check] disagreed ({second}) — skipping")
        return False
    print("    [double-check] ✓ confirmed")
    return True

# ═══════════════════════════════════════════════════════════════
#  STRATEGY 4: CLICK WITH POPUP-CLOSE VERIFICATION + RETRY
#  Stores click stats in _last_click_stats for logging.
# ═══════════════════════════════════════════════════════════════
def click_and_verify(mx, my):
    """
    Click the confirm slot with human-like cursor behaviour:
      1. Smooth-move to within a pixel or two of the target.
      2. Tiny micro-jitter (±2px) — cursor is never perfectly still.
      3. Brief settle pause before pressing down.
      4. Natural mousedown duration (60-130 ms).
      5. Post-click drift (random ±6px, ±4px) — hand moves slightly
         after releasing, not frozen on the exact click point.
      6. Verify popup closed; retry up to 3× if not.
    """
    global _last_click_stats, _cur_x, _cur_y
    t0 = time.time()
    open(LOCK,'w').close()
    for attempt in range(1, 4):
        # ── 1. Smooth approach ────────────────────────────────────────────
        smooth_move(mx, my)

        # ── 2. Micro-jitter: land ±2px off-center (hand tremor) ──────────
        jx = mx + random.randint(-2, 2)
        jy = my + random.randint(-2, 2)
        xdo('mousemove', str(jx), str(jy))
        _cur_x, _cur_y = jx, jy
        time.sleep(random.uniform(0.03, 0.07))   # settle

        # ── 3. Press and hold ────────────────────────────────────────────
        xdo('mousedown', '1')
        time.sleep(random.uniform(0.06, 0.13))

        # ── 4. Release ───────────────────────────────────────────────────
        xdo('mouseup', '1')

        # ── 5. Post-click drift ───────────────────────────────────────────
        drift_x = mx + random.randint(-6, 6)
        drift_y = my + random.randint(-4, 4)
        smooth_move(drift_x, drift_y)

        time.sleep(0.40)
        if not popup_open():
            print(f"    [click] popup closed after attempt {attempt} ✓")
            try: os.remove(LOCK)
            except: pass
            _last_click_stats = {
                'elapsed_ms': int((time.time()-t0)*1000),
                'retries'   : attempt,
                'popup_closed': True,
            }
            return True
        print(f"    [click] popup still open after attempt {attempt}, retrying…")
        time.sleep(0.22)
    try: os.remove(LOCK)
    except: pass
    _last_click_stats = {
        'elapsed_ms': int((time.time()-t0)*1000),
        'retries'   : 3,
        'popup_closed': False,
    }
    print("    [click] all retries failed — popup refused to close")
    return False

# ═══════════════════════════════════════════════════════════════
#  QUICK POPUP PRESENCE CHECK  (gray-mass scan, no full analysis)
# ═══════════════════════════════════════════════════════════════
def chat_says_teleported():
    """
    Detect the "Console teleported you to spawn." chat message.

    JartexNetwork flow — ALWAYS in this order:
      1. Server teleports player to Spawn.
      2. Chat message appears: "Console teleported you to spawn."
      3. ~1-2 seconds later the "Afk Grinding" popup GUI opens.

    Detecting step 2 lets us:
      • Pre-focus the MC window immediately (before the popup appears).
      • Position the cursor center-screen so it's inside the popup area.
      • Poll popup_open() at 80ms intervals instead of waiting POLL=0.35s.
    Net result: up to ~1.5s head start, which is significant on a 12s timer.

    Detection strategy (two independent backends):
      1. Tesseract OCR on the chat crop (if installed) — most accurate.
         Looks for "teleport" + "spawn" anywhere in the cropped text.
      2. §6 Gold pixel count — "Console" prefix color is #FFAA00.
         ≥12 gold pixels in the chat area strongly suggests a console
         message is present.  False positives are harmless (worst case:
         focus_mc_window() is called a few ms early, which is fine).

    Returns True if the teleport message is detected by either backend.
    Guaranteed to not raise — returns False on any error.
    """
    sw, sh = screen_wh()
    if not sw or not sh:
        return False
    try:
        # ── Crop: left 45% × bottom 20% of screen (chat area) ────────────
        cx  = 0
        cy  = max(0, int(sh * CHAT_AREA_Y_FRAC))
        cw  = int(sw * CHAT_AREA_X_FRAC)
        ch  = sh - cy
        if cw < 50 or ch < 10:
            return False

        chat_snap = "/tmp/_mc_chat.png"
        subprocess.run(
            ['scrot', '-z', '-a', f'{cx},{cy},{cw},{ch}', chat_snap],
            capture_output=True, timeout=3
        )
        if not os.path.exists(chat_snap):
            return False

        # ── Backend 1: Tesseract OCR ──────────────────────────────────────
        if HAS_TESS:
            try:
                out = subprocess.run(
                    ['tesseract', chat_snap, 'stdout', '--psm', '6'],
                    capture_output=True, text=True, timeout=4
                )
                text = out.stdout.lower()
                # Both "teleport*" and "spawn" must appear — "Console" alone
                # could be any admin message; requiring both keeps FP rate low.
                if ('teleport' in text or 'teleported' in text) and 'spawn' in text:
                    print("  [chat-warn] 'teleport…spawn' via OCR ✓")
                    return True
            except Exception:
                pass

        # ── Backend 2: §6 Gold pixel count ───────────────────────────────
        # "Console" in JartexNetwork messages is formatted in §6 gold (#FFAA00).
        # Count gold pixels in the chat crop — ≥12 means a console message
        # label is likely visible.  Harmless false-positive if another gold
        # message happens to be in chat; the extra window focus costs ~300ms.
        r = decode_png(chat_snap)
        if r is not None:
            rows, iw, ih, bpp = r
            n_gold = count_color(rows, bpp, C_CHAT_GOLD[0], C_CHAT_GOLD[1])
            if n_gold >= 12:
                print(f"  [chat-warn] gold_px={n_gold} in chat → likely Console message")
                return True

    except Exception as e:
        print(f"  [chat-warn] error: {e}")
    return False


def popup_open():
    """
    Detect if the AFK Grinding popup is open.

    Always takes a full-screen screenshot so the popup is captured regardless
    of where it appears — the center-crop approach previously used was too
    sensitive to the popup being slightly off-center or the display size being
    unknown.

    Gray pixel threshold lowered from 1200 to 400: a full-screen shot of a
    Minecraft world contains very few gray pixels when no GUI is open (terrain,
    sky, entities are all colourful or white/dark, not mid-gray).  The AFK
    popup adds thousands of mid-gray pixels, so 400 is a safe floor.
    """
    scrot()   # always full-screen — popup guaranteed to be in frame
    r=decode_png(SNAP)
    if r is None:
        print("  [popup_open] scrot/decode failed")
        return False
    rows,_,_,bpp=r
    n = count_color(rows,bpp,C_GRAY[0],C_GRAY[1])
    # Uncomment to debug threshold:
    # print(f"  [popup_open] gray_px={n}")
    return n > 400

# ═══════════════════════════════════════════════════════════════
#  PRESCAN — find which slots have items WITHOUT hovering
# ═══════════════════════════════════════════════════════════════
def prescan_strip(strip):
    sl, st, sr, sb, slot_w, slot_h = strip
    # Research finding (Jul 2026 screenshot analysis, 8 popups across 2 sessions):
    # JartexNetwork's Afk Grinding GUI uses a 27-slot (3×9) inventory container,
    # but items are ONLY ever placed in the top 2 rows (rows 0 and 1).  Row 2 is
    # always empty gray slots.  Scanning only 2 rows cuts worst-case sweep time
    # by 33% (9 fewer empty slots × ~0.30s each = ~2.7s saved).
    # Safety: if prescan finds nothing in 2 rows it falls back to hovering ALL
    # slots including row 2, so a future popup using row 3 is still handled.
    N_ROWS = 2
    sw, sh = screen_wh()

    px = max(0, sl);  py = max(0, st)
    strip_w = sr - sl + 4
    strip_h = sb - st + 4
    if sw and sh:
        pw = min(sw - px, strip_w)
        ph = min(sh - py, strip_h)
    else:
        pw = strip_w
        ph = strip_h
    scrot(px, py, pw, ph)

    r = decode_png(SNAP)
    if r is None:
        print("  Prescan: decode failed — hovering ALL slots (3 rows for safety)")
        all_s = [(row,col) for row in range(3) for col in range(9)]
        return all_s, [], None

    img_rows, iw, ih, bpp = r
    high, med = [], []

    for row in range(N_ROWS):
        for col in range(9):
            cx = col * slot_w + slot_w // 2
            cy = row * slot_h + slot_h // 2

            # At GUI scale 2 (1366×694) each slot icon is 32×32 px on screen.
            # Scanning slot_w//3 (≈10px) radius = a 20×20 px box — covers the
            # centre of the item icon while avoiding the 2-px dark slot border.
            # Old slot_w//4 (≈7px) gave a 14×14 box which missed icon edges.
            half = max(6, slot_w // 3)
            x0 = max(0, cx - half);  x1 = min(iw, cx + half)
            y0 = max(0, cy - half);  y1 = min(ih, cy + half)

            colorful = 0
            for iy in range(y0, y1):
                rd = img_rows[iy]
                for ix in range(x0, x1):
                    rr=rd[ix*bpp]; gg=rd[ix*bpp+1]; bb=rd[ix*bpp+2]
                    if max(rr,gg,bb) - min(rr,gg,bb) > 28:
                        colorful += 1
                    if colorful >= PRESCAN_HIGH:
                        break
                if colorful >= PRESCAN_HIGH:
                    break

            if   colorful >= PRESCAN_HIGH: high.append((row, col))
            elif colorful >= PRESCAN_MED:  med.append((row, col))

    print(f"  Prescan: {len(high)} HIGH + {len(med)} MED "
          f"({9*N_ROWS - len(high) - len(med)} empty skipped)")

    if not high and not med:
        # Nothing found in the normal 2-row scan — fall back to all 3 rows in
        # case JartexNetwork ever places items in the 3rd row or the prescan
        # crop missed them.  This is a safety net, not the normal path.
        print("  Prescan found nothing — hovering ALL 27 slots (3 rows) as safety fallback")
        all_s = [(row,col) for row in range(3) for col in range(9)]
        return all_s, [], (img_rows, iw, ih, bpp)

    return high, med, (img_rows, iw, ih, bpp)

# ── Diagnostic log ─────────────────────────────────────────────────────────
_LOG = "/tmp/mc_afk_log.txt"
def _log(msg):
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(_LOG, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
#  CALIBRATION DATA HELPERS
# ═══════════════════════════════════════════════════════════════
def _ensure_assets():
    """Create calibration data directory if it doesn't exist."""
    pathlib.Path(ASSETS_DIR).mkdir(parents=True, exist_ok=True)


def save_popup_screenshot(strip):
    """
    Capture full screen, draw the detected strip rect on top (green box via
    ImageMagick if available, raw copy otherwise), save to ASSETS_DIR.

    Returns (ts_str, base_name) where base_name is the filename stem
    (without .png) used for both the image and the JSON record.
    """
    global _attempt_no
    _ensure_assets()
    _attempt_no += 1
    ts   = time.strftime("%Y%m%d_%H%M%S")
    base = f"popup_{ts}_{SESSION_ID}_{_attempt_no:04d}"
    raw  = f"/tmp/_mc_save_{_attempt_no}.png"
    dest = os.path.join(ASSETS_DIR, base + ".png")

    subprocess.run(['scrot', '-z', raw], capture_output=True)

    sl, st, sr, sb, slot_w, slot_h = strip
    ann = (f"#{_attempt_no} sess={SESSION_ID} strip({sl},{st})-({sr},{sb}) "
           f"sw={slot_w} sh={slot_h}")

    if HAS_CONVERT:
        try:
            r = subprocess.run([
                'convert', raw,
                # Green box around the detected AFK strip
                '-fill', 'none', '-stroke', '#00FF00', '-strokewidth', '2',
                '-draw', f'rectangle {sl},{st} {sr},{sb}',
                # Slot grid lines (faint yellow)
                '-stroke', '#FFFF0060', '-strokewidth', '1',
                # Label top-left of strip
                '-fill', '#00FF00', '-stroke', 'none', '-pointsize', '11',
                '-annotate', f'+{sl}+{max(13, st - 2)}', ann,
                dest
            ], capture_output=True, timeout=5)
            if r.returncode != 0 or not os.path.exists(dest):
                raise RuntimeError("convert failed")
        except Exception:
            try: shutil.copy2(raw, dest)
            except Exception: pass
    else:
        try: shutil.copy2(raw, dest)
        except Exception: pass

    try: os.remove(raw)
    except: pass

    return ts, base


def ask_feedback(context=''):
    """
    Pause and ask the user for a correctness label via /dev/tty.
    Works even when this process runs in the background (not attached to
    stdin) because /dev/tty connects directly to the controlling terminal.

    Returns 'correct' | 'incorrect' | 'skipped'.  20-second timeout.

    When MC_UNATTENDED=1 is set, returns 'skipped' immediately without
    prompting — useful for overnight/unattended farming runs where no one
    is watching the terminal.  Accuracy labels are simply not collected.
    """
    if MC_UNATTENDED:
        return 'skipped'
    sep = '─' * 62
    msg = (f"\n{sep}\n"
           f"[FEEDBACK]  {context}\n"
           f"  Did the solver act correctly?\n"
           f"  [y] Yes — right click    [n] No — wrong or missed\n"
           f"  [s] Skip  (20 s auto-skip)    → ")
    try:
        with open('/dev/tty', 'r') as inp, open('/dev/tty', 'w') as out:
            out.write(msg); out.flush()
            readable, _, _ = select.select([inp], [], [], 20.0)
            if readable:
                ans   = inp.readline().strip().lower()
                label = ('correct'   if ans.startswith('y') else
                         'incorrect' if ans.startswith('n') else 'skipped')
                out.write(f"  → saved as: {label}\n{sep}\n"); out.flush()
                return label
            out.write(f'(timeout — auto-skipped)\n{sep}\n'); out.flush()
    except Exception as e:
        print(f"  [feedback] /dev/tty error: {e}")
    return 'skipped'


def write_attempt_record(record):
    """Append one JSON line to attached_assets/attempts.jsonl."""
    try:
        _ensure_assets()
        path = os.path.join(ASSETS_DIR, 'attempts.jsonl')
        with open(path, 'a') as f:
            f.write(json.dumps(record, separators=(',', ':')) + '\n')
    except Exception as e:
        print(f"  [record] write error: {e}")

# ═══════════════════════════════════════════════════════════════
#  SWEEP — all 5 strategies + calibration data recording
#
#  Each popup attempt produces:
#    attached_assets/popup_<ts>_<sess>_<N>.png   — annotated screenshot
#    attached_assets/attempts.jsonl              — one JSON record appended
#
#  After the attempt the script pauses for user feedback (20 s timeout).
#  If the script fails to find the confirm item it opens AFK_LOCK (pausing
#  the spam loop) and watches for 30 s so the user can handle the popup
#  manually; then asks feedback before resuming.
# ═══════════════════════════════════════════════════════════════
def sweep_strip(strip):
    sl, st, sr, sb, slot_w, slot_h = strip
    _sw, _sh = screen_wh()
    # Use large safe defaults when display size is unknown so min/max clamps
    # in hover_spiral() and _snap_tooltip() still work without crashing.
    sw = _sw if _sw else 9999
    sh = _sh if _sh else 9999
    sweep_start = time.time()

    # ── CRITICAL: ensure Minecraft has X11 focus before any mouse event ──
    # If the game is not the active window, xdotool clicks land at the right
    # screen pixel but Java's AWT input queue discards them — the popup never
    # closes and the server kicks us for timeout.  We focus even if we think
    # the window is already active, because alt-tab or a system dialog can
    # silently steal focus between the popup detection and the sweep.
    focus_mc_window()

    # Budget check — JartexNetwork allows ~MC_POPUP_TIMEOUT seconds total.
    # If our sweep takes longer than SWEEP_TIMEOUT we abort and let the
    # bash backup solver handle it, rather than rushing a potentially wrong click.
    # SWEEP_TIMEOUT (5.5s) < MC_POPUP_TIMEOUT (12s) leaves 6+ seconds of margin.

    # ── Save popup screenshot and start the attempt record ────────────────
    ts_str, base_name = save_popup_screenshot(strip)

    record = {
        'schema'         : 'afk_attempt_v1',
        'session_id'     : SESSION_ID,
        'attempt_no'     : _attempt_no,
        'timestamp_iso'  : time.strftime('%Y-%m-%dT%H:%M:%S'),
        'timestamp_epoch': round(time.time(), 3),
        # Optional — set MC_PLAYER_NAME before running mc_farm.sh so this
        # record can be filtered/joined against the exact matching player in
        # afkverify_events.jsonl on a shared/multiplayer server. Falls back
        # to timestamp-proximity join (see join_training_data.py) if unset.
        'player_name'    : PLAYER_NAME or None,
        'screenshot'     : base_name + '.png',
        'strip'          : {
            'left': sl, 'top': st, 'right': sr, 'bottom': sb,
            'slot_w': slot_w, 'slot_h': slot_h,
        },
        'prescan'        : {},
        'slots_inspected': [],
        'outcome'        : {},
        'user_feedback'  : None,
    }

    # ── S1: Two-tier prescan ─────────────────────────────────────────────
    high_slots, med_slots, prescan_img = prescan_strip(strip)
    record['prescan'] = {'high': high_slots, 'med': med_slots}
    total_items = len(high_slots) + len(med_slots)
    print(f"  sweep_strip: {len(high_slots)} HIGH + {len(med_slots)} MED  "
          f"[slot_w={slot_w} slot_h={slot_h}]")

    # ── S1b: Template pre-classification (no hover needed) ───────────────
    # Match slot icons against the accumulated template library built from
    # attached_assets/.  Deny slots are skipped immediately; confirm slots
    # get fast-tracked.  Falls back to hover+tooltip if templates are absent
    # or confidence is too low.
    template_votes = {}
    if prescan_img is not None:
        p_rows, p_iw, p_ih, p_bpp = prescan_img
        for _r, _c in high_slots + med_slots:
            tv = match_slot_from_prescan(p_rows, p_iw, p_ih, p_bpp,
                                         _r, _c, slot_w, slot_h)
            if tv:
                template_votes[(_r, _c)] = tv
        if template_votes:
            n_c = sum(1 for v in template_votes.values() if v == 'confirm')
            n_d = sum(1 for v in template_votes.values() if v == 'deny')
            print(f"  Template pre-scan: {n_c} confirm  {n_d} deny  "
                  f"{total_items - len(template_votes)} unclassified")

    # ── Reorder sweep: template-confirm slots first (most likely the answer),
    # then remaining HIGH, then remaining MED.  Preserves original is_high flag
    # so double-check behaviour is unchanged.
    tmpl_conf_h = [(r, c) for r, c in high_slots if template_votes.get((r, c)) == 'confirm']
    tmpl_conf_m = [(r, c) for r, c in med_slots  if template_votes.get((r, c)) == 'confirm']
    rest_high   = [(r, c) for r, c in high_slots  if template_votes.get((r, c)) != 'confirm']
    rest_med    = [(r, c) for r, c in med_slots   if template_votes.get((r, c)) != 'confirm']
    sweep_order = [
        (tmpl_conf_h, True),   # template-confirm from HIGH → double-checked
        (tmpl_conf_m, False),  # template-confirm from MED  → no double-check
        (rest_high,   True),
        (rest_med,    False),
    ]

    solved_info = [None]   # set by process_slot when click succeeds

    def process_slot(row, col, is_high):
        elapsed = time.time() - sweep_start
        if elapsed > SWEEP_TIMEOUT:
            print(f"  ⚠ timeout at {elapsed:.1f}s — aborting sweep")
            return "timeout"

        sx = sl + col * slot_w + slot_w // 2
        sy = st + row * slot_h + slot_h // 2
        label = f"row{row+1}c{col+1}[{'H' if is_high else 'M'}]"
        tmpl = template_votes.get((row, col))

        slot_info = {
            'row': row, 'col': col, 'screen_x': sx, 'screen_y': sy,
            'confidence'  : 'HIGH' if is_high else 'MED',
            'template_pre': tmpl,
            'color_vote' : None, 'hsv_vote' : None,
            'ratio_vote' : None, 'runs_vote' : None,
            'ai_vote'    : None, 'ocr_vote'  : None,
            'vote_scores' : {},
            'final_answer': None,
            'double_checked': False,
            'clicked'       : False,
            'click_success' : None,
        }

        # ── S1c: Template deny → skip without hovering ───────────────────
        if tmpl == 'deny':
            slot_info['final_answer'] = 'deny_template'
            record['slots_inspected'].append(slot_info)
            print(f"  {label}: deny (template, no hover)")
            return False

        # ── S2: Hover + spiral fallback ──────────────────────────────────
        found, mx, my = hover_spiral(sx, sy, sw, sh, slot_w)
        if not found:
            slot_info['final_answer'] = 'no_tooltip'
            record['slots_inspected'].append(slot_info)
            print(f"  {label}: no tooltip after spiral → skip")
            return False

        # ── S3: Voted tooltip reading ────────────────────────────────────
        answer = read_tooltip_voted()
        slot_info['color_vote']  = _last_vote_detail.get('color')
        slot_info['hsv_vote']   = _last_vote_detail.get('hsv')
        slot_info['ratio_vote'] = _last_vote_detail.get('ratio')
        slot_info['runs_vote']  = _last_vote_detail.get('runs')
        slot_info['ai_vote']    = _last_vote_detail.get('ai')
        slot_info['ocr_vote']   = _last_vote_detail.get('ocr')
        slot_info['vote_scores'] = _last_vote_detail.get('scores', {})
        slot_info['final_answer'] = answer
        print(f"  {label}: {answer}")

        # ── Online template update ────────────────────────────────────────
        # Reinforce the template with this slot's icon crop from the prescan
        # image so future popups can skip hovering entirely.
        def _reinforce_template(label_str):
            if prescan_img is None:
                return
            p_rows2, p_iw2, p_ih2, p_bpp2 = prescan_img
            pad = max(1, slot_w // 10)
            x0 = col * slot_w + pad;  y0 = row * slot_h + pad
            wi = slot_w - 2*pad;      hi = slot_h - 2*pad
            if x0+wi <= p_iw2 and y0+hi <= p_ih2 and wi >= 4 and hi >= 4:
                flat = _downsample(p_rows2, x0, y0, wi, hi, p_bpp2)
                if flat:
                    _update_template(label_str, flat)

        if answer == "confirm":
            if is_high:
                # ── S4: Double-check before clicking (HIGH slots only) ───
                ok = double_check_confirm(mx, my, sw, sh, slot_w)
                slot_info['double_checked'] = True
                if not ok:
                    record['slots_inspected'].append(slot_info)
                    return False

            # ── S5: Click with popup-close verification ──────────────────
            slot_info['clicked'] = True
            success = click_and_verify(mx, my)
            slot_info['click_success']    = success
            slot_info['click_elapsed_ms'] = _last_click_stats['elapsed_ms']
            slot_info['click_retries']    = _last_click_stats['retries']
            record['slots_inspected'].append(slot_info)

            elapsed = time.time() - sweep_start
            _log(f"{'SOLVED' if success else 'CLICK-FAIL'} "
                 f"{label} conf={'H' if is_high else 'M'} t={elapsed:.2f}s")

            if success:
                solved_info[0] = slot_info
                _reinforce_template('confirm')
                time.sleep(random.uniform(0.8, 1.4))
                return True
            else:
                print(f"  {label}: click failed — continuing scan")
                return False

        elif answer == "deny":
            # Reinforce deny template only when the vote margin is clear —
            # avoids drifting the template on ambiguous or borderline reads.
            deny_score = slot_info['vote_scores'].get('deny', 0)
            conf_score = slot_info['vote_scores'].get('confirm', 0)
            if deny_score >= VOTE_THRESHOLD and deny_score - conf_score >= WEIGHT_COLOR:
                _reinforce_template('deny')
            record['slots_inspected'].append(slot_info)
            return False

        record['slots_inspected'].append(slot_info)
        return False

    # ── Sweep in priority order: template-confirm first, then high, then med ─
    final_result = False
    for slots, is_high in sweep_order:
        for row, col in slots:
            r = process_slot(row, col, is_high)
            if r == "timeout":
                record['outcome'] = {
                    'script_result': 'timeout',
                    'swept_for_s'  : round(time.time() - sweep_start, 2),
                    'popup_closed' : False,
                }
                _log(f"TIMEOUT items={total_items} t={time.time()-sweep_start:.2f}s")
                record['user_feedback'] = ask_feedback(
                    f"Script TIMED OUT after {record['outcome']['swept_for_s']}s "
                    f"(scanned {total_items} items).  Screenshot: {base_name}.png")
                write_attempt_record(record)
                return False
            if r is True:
                final_result = True
                break
        if final_result:
            break

    # ── Outcome A: script clicked the confirm item ────────────────────────
    if final_result:
        si = solved_info[0]
        record['outcome'] = {
            'script_result'   : 'clicked',
            'popup_closed'    : _last_click_stats['popup_closed'],
            'click_elapsed_ms': _last_click_stats['elapsed_ms'],
            'click_retries'   : _last_click_stats['retries'],
            'swept_for_s'     : round(time.time() - sweep_start, 2),
        }
        ctx = (f"Script clicked row={si['row']+1} col={si['col']+1}  "
               f"({'popup closed ✓' if _last_click_stats['popup_closed'] else 'popup stayed open?'}).  "
               f"Screenshot: {base_name}.png")
        record['user_feedback'] = ask_feedback(ctx)
        write_attempt_record(record)
        return True

    # ── Outcome B: script failed — watch for user to handle popup ────────
    record['outcome'] = {
        'script_result': 'not_found',
        'swept_for_s'  : round(time.time() - sweep_start, 2),
        'popup_closed' : False,
    }

    # Pause spam loop while we watch
    open(LOCK, 'w').close()
    print(f"\n[ watch ] Script scanned {total_items} item(s) — confirm not found.")
    print( "[ watch ] Popup still open.  Handle it yourself (30 s), then give feedback.")
    print( "[ watch ] The spam loop is paused until the popup closes.\n")

    wt = time.time()
    while popup_open() and (time.time() - wt) < 30.0:
        time.sleep(0.5)

    popup_gone = not popup_open()
    record['outcome']['popup_closed']    = popup_gone
    record['outcome']['watch_elapsed_s'] = round(time.time() - wt, 1)

    try: os.remove(LOCK)
    except: pass

    _log(f"FAILED items={total_items} t={time.time()-sweep_start:.2f}s "
         f"user_handled={'Y' if popup_gone else 'N'}")

    ctx = (f"Script FAILED ({total_items} items scanned, confirm not found).  "
           f"{'You closed the popup ✓' if popup_gone else 'Popup still open / timed out.'}  "
           f"Screenshot: {base_name}.png")
    record['user_feedback'] = ask_feedback(ctx)
    write_attempt_record(record)
    return False

# ═══════════════════════════════════════════════════════════════
#  BAN GUARD
# ═══════════════════════════════════════════════════════════════
def _ban_check(consec_fails):
    """
    Emit a warning or stop the bot based on consecutive-failure count.

    JartexNetwork bans players for 1 hour after 5 consecutive AFK-check
    failures (timeout or wrong click).  This function:
      • Prints a loud warning at BAN_WARN_AT (default 3) misses.
      • Removes the FLAG file at BAN_STOP_AT (default 5) misses,
        which causes the main while-loop to exit gracefully on its
        next iteration — stopping the bot BEFORE the ban triggers.

    Called after every popup that the script did NOT successfully solve.
    """
    if consec_fails <= 0:
        return
    bar = '!' * 60
    if consec_fails >= BAN_STOP_AT:
        print(f"\n{bar}")
        print(f"  BAN GUARD: {consec_fails} consecutive misses — STOPPING BOT")
        print(f"  JartexNetwork bans at 5 misses.  Remove the flag and")
        print(f"  restart only after fixing the detection issue.")
        print(f"{bar}\n")
        _log(f"BAN_GUARD_STOP consec_fails={consec_fails}")
        try: os.remove(FLAG)
        except: pass
    elif consec_fails >= BAN_WARN_AT:
        print(f"\n{'═'*60}")
        print(f"  ⚠ WARNING: {consec_fails} consecutive misses "
              f"({BAN_STOP_AT - consec_fails} left before auto-stop).")
        print(f"  Check detection accuracy or stop the bot manually.")
        print(f"{'═'*60}\n")
        _log(f"BAN_GUARD_WARN consec_fails={consec_fails}")


# ═══════════════════════════════════════════════════════════════
#  MAIN LOOP
# ═══════════════════════════════════════════════════════════════
def main():
    mode = ("AI+" if HAS_AI else "") + "color+HSV" + ("+OCR" if HAS_TESS else "")
    print(f"[ AFK solver ] mode: {mode}")
    print(f"[ AFK solver ] session: {SESSION_ID}")
    print(f"[ AFK solver ] calibration data → {ASSETS_DIR}/")
    print(f"[ AFK solver ] server popup timeout: {MC_POPUP_TIMEOUT}s  "
          f"sweep budget: {SWEEP_TIMEOUT}s  "
          f"margin: {MC_POPUP_TIMEOUT - SWEEP_TIMEOUT:.1f}s")
    print(f"[ AFK solver ] ban guard: warn@{BAN_WARN_AT}  stop@{BAN_STOP_AT}  "
          f"| unattended: {'ON (feedback skipped)' if MC_UNATTENDED else 'OFF (20s feedback prompt)'}"
          f"  | chat-warn: every {CHAT_DETECT_EVERY} polls")
    print( "[ AFK solver ] watching for Afk Grinding popup...\n")

    # Focus MC window now so the OS has raised it before the first popup.
    # This also gives the user an early warning if Minecraft is not running.
    focus_mc_window()

    _ensure_assets()
    _load_weights()
    _load_templates()
    # Bootstrap from historic attempts only when no templates exist yet —
    # avoids re-ingesting old data every run, which would dampen recent
    # online learning accumulated during previous sessions.
    if _slot_templates.get('confirm') is None and _slot_templates.get('deny') is None:
        _bootstrap_templates_from_assets()
    idle           = 0
    _chat_ctr      = 0   # counts up; chat scan fires when it reaches CHAT_DETECT_EVERY
    _consec_fails  = 0   # consecutive non-solved popups (ban guard)
    _history       = []  # last 10 outcomes: True=solved, False=missed (streak display)

    while os.path.exists(FLAG):
        try:
            _popup_now = popup_open()

            if not _popup_now:
                idle += 1
                if idle % 40 == 0:
                    # Show running solve streak so user can see accuracy at a glance
                    if _history:
                        _streak = ''.join('✓' if x else '✗' for x in _history[-10:])
                        _rate   = sum(_history) / len(_history)
                        print(f"[ AFK solver ] still watching...  "
                              f"streak: {_streak}  ({_rate:.0%})")
                    else:
                        print("[ AFK solver ] still watching...")

                # ── Chat early-warning (every CHAT_DETECT_EVERY polls) ────
                # Checks for "Console teleported you to spawn." in the chat
                # area.  When found we pre-focus MC and poll aggressively
                # (80ms intervals) for up to 3s to catch the popup the
                # moment it appears — giving us back 1-2s of solve budget.
                _chat_ctr += 1
                if _chat_ctr >= CHAT_DETECT_EVERY:
                    _chat_ctr = 0
                    if chat_says_teleported():
                        print("[ AFK solver ] ⚡ teleport detected in chat — "
                              "pre-focusing MC and waiting for popup...")
                        # ── BUG FIX: set lock IMMEDIATELY on chat detection ──
                        # The old flow only set LOCK after popup_open() returned
                        # True (line ~1979 below).  During the 1-3s gap before
                        # the popup appears the bash spam loop was still running
                        # with the cursor at screen-centre — inside the popup
                        # zone — so it could misclick a slot before we started.
                        # Setting lock here pauses spam for the whole pre-popup
                        # wait.  If no popup appears (false positive) we release
                        # the lock at the end of the tight-poll block below.
                        open(LOCK, 'w').close()
                        focus_mc_window()
                        # ── BUG FIX: safe corner, NOT screen centre ──────────
                        # Popup occupies roughly x=378-646, y=141-375 on the
                        # typical 1366×694 screen.  Moving to screen centre
                        # put the cursor INSIDE the popup area.  Bottom-left
                        # corner (50, sh-50) is always outside the popup.
                        _psw, _psh = screen_wh()
                        if _psw and _psh:
                            xdo('mousemove', '50', str(_psh - 50))
                        # Tight poll: 80ms × 37 iterations = up to 3s wait
                        for _ in range(37):
                            time.sleep(0.08)
                            if popup_open():
                                _popup_now = True
                                break
                        if not _popup_now:
                            # False positive — popup never appeared.
                            # Release lock so spam loop resumes normally.
                            try: os.remove(LOCK)
                            except OSError: pass

                if not _popup_now:
                    time.sleep(POLL)
                    continue

            # ── Popup detected ────────────────────────────────────────────
            idle = 0
            print("[ AFK solver ] popup detected — pausing spam loop")

            # Create LOCK NOW so the bash spam loop stops immediately.
            # This must happen as soon as the popup is confirmed — before
            # strip parsing — so we don't misclick during analysis.
            open(LOCK, 'w').close()

            time.sleep(random.uniform(REACT_MIN, REACT_MAX))

            # ── Parse the exact strip geometry ───────────────────────────
            strip = find_afk_strip()
            if strip is None:
                print("[ AFK solver ] strip parse failed — waiting for popup to close")
                _log("STRIP_FAIL could_not_parse")
                t_wp = time.time()
                while popup_open() and time.time()-t_wp < 30.0:
                    time.sleep(0.5)
                try: os.remove(LOCK)
                except: pass
                # Strip-parse failure = server sees timeout = counts as a miss
                _consec_fails += 1
                _history.append(False)
                if len(_history) > 50: _history.pop(0)
                _ban_check(_consec_fails)
                continue

            sl,st,sr,sb,slot_w,slot_h=strip
            print(f"[ AFK solver ] strip → left={sl} top={st} right={sr} bot={sb}  "
                  f"slot_w={slot_w}px  slot_h={slot_h}px")

            solved = sweep_strip(strip)

            # Belt-and-suspenders LOCK cleanup
            try: os.remove(LOCK)
            except: pass

            # ── Ban guard: track consecutive misses ───────────────────────
            # "Solved" only counts when the popup actually closed after the
            # click — a click that left the popup open is still a miss from
            # the server's perspective (it will kick us after the timer).
            if solved:
                _consec_fails = 0
                _history.append(True)
                print("[ AFK solver ] ✓ solved! Back to watching...\n")
            else:
                _consec_fails += 1
                _history.append(False)
                _ban_check(_consec_fails)
                time.sleep(POLL)
            if len(_history) > 50: _history.pop(0)

        except KeyboardInterrupt:
            break
        except Exception as e:
            import traceback
            print(f"[ AFK solver ] error: {e}")
            print(traceback.format_exc())
            try: os.remove(LOCK)
            except: pass
            time.sleep(1)

    print("[ AFK solver ] stopped")

# ── Startup validation (here so screen_wh() is already defined above) ──────
_missing_tools = [t for t in ('scrot','xdotool') if subprocess.run(['which',t],capture_output=True).returncode!=0]
if _missing_tools:
    print(f"[ FATAL ] Missing required tools: {', '.join(_missing_tools)}")
    print(f"  Install with:  sudo apt install {' '.join(_missing_tools)}")
    sys.exit(1)
_sw0,_sh0 = screen_wh()
_res = f"{_sw0}×{_sh0}" if _sw0 else "unknown(full-screen mode)"
print(f"[ init ] screen={_res}  scrot=✓  xdotool=✓"
      + ("  maim=✓(better capture)" if HAS_MAIM else "  maim=✗(using scrot)")
      + ("  AI=✓" if HAS_AI else "  AI=✗(no key)")
      + ("  OCR=✓" if HAS_TESS else "  OCR=✗"))

if __name__=="__main__":
    main()
PYEOF

# ── Set up calibration output dir and tell Python where it lives ────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/attached_assets"
export MC_ASSETS_DIR="$SCRIPT_DIR/attached_assets"
echo "[ MC Farm ] calibration data → $MC_ASSETS_DIR/"

# ── Launch AFK solver in background ────────────────────────────
python3 "$PY_SCRIPT" &
AFK_PID=$!
echo "[ MC Farm ] AFK solver PID=$AFK_PID"
echo "$AFK_PID" > "$PID_FILE"

# ═══════════════════════════════════════════════════════════════════════════════
#  RESEARCH-SOURCED TECHNIQUE EXTENSIONS  (pure Bash / awk / xdotool / ImageMagick)
#
#  Every technique below was identified by web research on general GUI automation.
#  Sources cited per function.  No new language runtimes — only tools already
#  present on Linux Mint via apt (ImageMagick, xinput, xdotool, awk).
# ═══════════════════════════════════════════════════════════════════════════════

# ── Tool detection ────────────────────────────────────────────────────────────
HAS_IMGMAG=0
command -v convert  >/dev/null 2>&1 && \
command -v compare  >/dev/null 2>&1 && HAS_IMGMAG=1

HAS_XINPUT=0
command -v xinput   >/dev/null 2>&1 && HAS_XINPUT=1

HAS_XWININFO=0
command -v xwininfo >/dev/null 2>&1 && HAS_XWININFO=1

[ $HAS_IMGMAG  -eq 1 ] && echo "[ MC Farm ] ImageMagick=✓  (shell pixel ops)" \
                       || echo "[ MC Farm ] ImageMagick=✗  (sudo apt install imagemagick)"
[ $HAS_XINPUT  -eq 1 ] && echo "[ MC Farm ] xinput=✓       (popup recording)" \
                       || echo "[ MC Farm ] xinput=✗       (sudo apt install xinput)"

# ─────────────────────────────────────────────────────────────────────────────
#  TECHNIQUE 1 — Sigma-lognormal step timing  (Box-Muller via awk)
#
#  Source: Pointergeist/PHC-mouse-movement-gen (GitHub, 2024-2025)
#          "Bot Detection Using Mouse Movements" IEEE Dynamics 2023
#          "Exploring visual representations of computer mouse movements for
#           bot detection using deep learning" ESWA 2023 (doi 10.1016/j.eswa.2023.120225)
#
#  Finding: real human inter-event timing follows a log-normal distribution,
#  not uniform random.  The Box-Muller transform converts two uniform variates
#  into a standard normal; exponentiating gives log-normal.
#  mu / sigma are in log-space (ln seconds).
#  Default mu=-4.7 → e^-4.7 ≈ 9ms mean.  sigma=0.28 → moderate variance.
#
#  Usage:  sleep "$(_lognormal_ms)"
#          sleep "$(_lognormal_ms -3.5 0.4)"   # ~30ms mean, more spread
# ─────────────────────────────────────────────────────────────────────────────
_lognormal_ms() {
    local mu=${1:--4.7} sigma=${2:-0.28}
    awk -v mu="$mu" -v sig="$sigma" -v s1="$RANDOM" -v s2="$RANDOM" '
    BEGIN {
        srand(s1 * 65536 + s2)
        u1 = rand(); if (u1 < 1e-10) u1 = 1e-10
        u2 = rand()
        # Box-Muller: two uniform → standard normal variate
        z  = sqrt(-2 * log(u1)) * cos(6.283185307 * u2)
        v  = exp(mu + sig * z)
        if (v < 0.004) v = 0.004    # clamp: 4ms floor
        if (v > 0.200) v = 0.200    # clamp: 200ms ceiling
        printf "%.4f\n", v
    }'
}

# ─────────────────────────────────────────────────────────────────────────────
#  TECHNIQUE 2 — Cubic Bézier cursor movement  (pure awk, no Python)
#
#  Source: vincentbavitz/bezmouse  (GitHub, 208 stars — "Simulate human mouse
#          movements with xdotool")
#          Vinyzu/cursory (GitHub, 99 stars — "100% human-realistic Mouse
#          Trajectories with Timings")
#
#  Formula: B(t) = (1-t)³P₀ + 3(1-t)²tP₁ + 3(1-t)t²P₂ + t³P₃
#  Control points P₁/P₂ are placed at random perpendicular offsets along the
#  path (≤15% of total distance) producing a natural curved arc.  Step timing
#  uses TECHNIQUE 1 (log-normal) for a realistic velocity bell-curve.
#
#  Usage:  bezier_move x0 y0 x1 y1
# ─────────────────────────────────────────────────────────────────────────────
bezier_move() {
    local x0=$1 y0=$2 x1=$3 y1=$4
    awk -v x0="$x0" -v y0="$y0" -v x1="$x1" -v y1="$y1" \
        -v s1="$RANDOM" -v s2="$RANDOM" \
    'BEGIN {
        srand(s1 * 65536 + s2)
        dx = x1 - x0;  dy = y1 - y0
        dist = sqrt(dx*dx + dy*dy)
        if (dist < 1) { print x1, y1; exit }

        # Perpendicular unit vector (rotate path vector 90°)
        px = -dy / dist;  py = dx / dist

        # Control-point positions (t=0.22..0.38 and t=0.62..0.78)
        t1 = 0.22 + rand() * 0.16
        t2 = 0.62 + rand() * 0.16

        # Perpendicular offset: up to 15% of distance
        max_off = dist * 0.15
        o1 =  max_off * (rand() * 2 - 1)
        o2 =  max_off * (rand() * 2 - 1) * 0.55   # less offset near end

        cp1x = x0 + dx*t1 + px*o1;  cp1y = y0 + dy*t1 + py*o1
        cp2x = x0 + dx*t2 + px*o2;  cp2y = y0 + dy*t2 + py*o2

        # Steps proportional to distance (~1.2 per pixel)
        n = int(dist * 1.2)
        if (n <   8) n =   8
        if (n > 220) n = 220

        ppx = x0;  ppy = y0
        for (i = 1; i <= n; i++) {
            t  = i / n;  mt = 1 - t
            bx = mt^3*x0 + 3*mt^2*t*cp1x + 3*mt*t^2*cp2x + t^3*x1
            by = mt^3*y0 + 3*mt^2*t*cp1y + 3*mt*t^2*cp2y + t^3*y1
            rx = int(bx + 0.5);  ry = int(by + 0.5)
            if (rx != ppx || ry != ppy) {
                print rx, ry
                ppx = rx;  ppy = ry
            }
        }
        print x1, y1
    }' | while IFS=' ' read -r bx by; do
        xdotool mousemove "$bx" "$by" 2>/dev/null
        # Log-normal timing: ~9ms mean (TECHNIQUE 1)
        sleep "$(_lognormal_ms -4.7 0.28)"
    done
}

# ─────────────────────────────────────────────────────────────────────────────
#  TECHNIQUE 3 — Polar arc camera sweep  (xdotool --polar flag)
#
#  Source: xdotool(1) man page — "mousemove_relative --polar"
#          Arch Linux / Ubuntu manpages (archlinux.org/man/xdotool.1,
#          manpages.ubuntu.com/manpages/focal/man1/xdotool.1.html)
#
#  --polar makes x=angle(degrees, 0=up, clockwise), y=distance(pixels).
#  This describes circular/arc motion — how a wrist actually pivots when
#  turning a camera — rather than decomposing into separate x/y steps.
#  Cosine easing across the arc gives natural acceleration + deceleration.
#  Log-normal per-step timing (TECHNIQUE 1) adds velocity realism.
#
#  Usage:  polar_arc_sweep [total_angle_deg] [step_radius_px]
# ─────────────────────────────────────────────────────────────────────────────
polar_arc_sweep() {
    # Pan camera in one direction by a cosine-eased sequence of polar moves.
    # total_px: total pixel distance to travel (≈ camera rotation amount)
    # Each step moves along a fixed heading by a cosine-eased fraction of that
    # distance.  Heading is chosen randomly with a horizontal bias (mostly left/
    # right pans) ±small vertical component, matching typical camera behaviour.
    #
    # NOTE on xdotool --polar semantics (man page): angle=0 means "up" (north),
    # clockwise: 90=right, 180=down, 270=left.  distance is pixels.  This is a
    # DIRECTIONAL move, not arc-around-a-point, so we fix the heading and vary
    # the per-step distance — the correct way to pan a camera.
    local total_px=${1:-$((50 + RANDOM % 80))}   # 50-130px total pan
    local n_steps=$((9 + RANDOM % 7))            # 9-15 steps
    # Pick heading: 70-110° (mostly rightward) or 250-290° (mostly leftward)
    # with a small vertical offset (±15°) for realism
    local base_h=$(( (RANDOM % 2) * 180 + 80 + RANDOM % 21 - 10 ))  # ~90 or ~270 ±10
    local heading=$(( (base_h + RANDOM % 31 - 15 + 360) % 360 ))    # ±15° extra spread

    awk -v total="$total_px" -v n="$n_steps" \
        -v heading="$heading" -v seed="$RANDOM" '
    BEGIN {
        srand(seed)
        pi = 3.14159265358979
        prev_t = 0
        for (s = 1; s <= n; s++) {
            t  = 0.5 * (1 - cos(pi * s / n))   # cosine ease-in/out
            dt = t - prev_t;  prev_t = t
            d  = int(total * dt + 0.5)
            if (d < 1) d = 1
            # ±3° heading jitter per step (hand is not locked on a rail)
            jitter = int((rand() - 0.5) * 6)
            printf "%d %d\n", (heading + jitter + 360) % 360, d
        }
    }' | while IFS=' ' read -r angle dist_px; do
        xdotool mousemove_relative --polar "$angle" "$dist_px" 2>/dev/null
        sleep "$(_lognormal_ms -4.3 0.30)"   # ~13ms mean (TECHNIQUE 1)
    done
}

# ─────────────────────────────────────────────────────────────────────────────
#  TECHNIQUE 4 — xdotool --window targeting  (window-relative coordinates)
#
#  Source: xdotool(1) man page — "mousemove --window WINDOW",
#          "getwindowgeometry --shell"
#          GitHub jordansissel/xdotool issue #176: "getwindowgeometry reports
#          incorrect coordinates" (compositor offset bug)
#          SO question 27788559: "xdotool offset/mismatch when using windowmove"
#
#  Moving relative to a specific window ID via --window bypasses the
#  compositor coordinate offset that causes absolute moves to be wrong under
#  Mutter/Picom/KWin.  xdotool translates window-relative coords internally.
#
#  Usage:  wid=$(get_mc_window)
#          window_move   $wid $rel_x $rel_y
#          window_click  $wid $rel_x $rel_y [hold_ms]
# ─────────────────────────────────────────────────────────────────────────────
get_mc_window() {
    local wid
    wid=$(xdotool search --name "[Mm]inecraft" 2>/dev/null | tail -1)
    [ -z "$wid" ] && wid=$(xdotool getactivewindow 2>/dev/null)
    echo "$wid"
}

window_move() {
    local wid=$1 rx=$2 ry=$3
    xdotool mousemove --window "$wid" "$rx" "$ry" 2>/dev/null
}

# xdotool --sync: "wait until the mouse is actually moved" (from man page)
# prevents race conditions between move and click in fast automation
window_click() {
    local wid=$1 rx=$2 ry=$3 hold_ms=${4:-65}
    xdotool mousemove --sync --window "$wid" "$rx" "$ry" 2>/dev/null
    xdotool mousedown 1 2>/dev/null
    sleep "$(awk -v ms="$hold_ms" -v seed="$RANDOM" \
        'BEGIN{srand(seed); printf "%.4f",(ms+(rand()-0.5)*18)/1000}')"
    xdotool mouseup 1 2>/dev/null
}

# ─────────────────────────────────────────────────────────────────────────────
#  TECHNIQUE 5 — Overshoot + corrective sub-movement  (Fitts' Law / PHC model)
#
#  Source: Pointergeist/PHC-mouse-movement-gen (sigma-lognormal model, GitHub)
#          Vinyzu/cursory — "Generate 100% human-realistic Mouse Trajectories"
#          Tomotsugu-dev/HumanMoveMouse — "statistical model trained on 300
#          samples of real human movement data" (GitHub)
#
#  Human movement research (Fitts' Law / sigma-lognormal): fast ballistic
#  movements overshoot by 3-8px, then a short corrective sub-movement (~80ms
#  delay) brings the cursor exactly onto target.  Anti-cheat ML classifiers
#  trained on real mouse data expect this pattern; perfectly straight stops
#  are a strong bot signal.
#
#  Usage:  overshoot_click tx ty [hold_ms]
# ─────────────────────────────────────────────────────────────────────────────
overshoot_click() {
    local tx=$1 ty=$2 hold_ms=${3:-65}

    # Overshoot: 3-8px in a random direction past the target
    local od=$((3 + RANDOM % 6))
    local oa=$((RANDOM % 360))
    local ox oy
    ox=$(awk -v t="$tx" -v a="$oa" -v d="$od" \
        'BEGIN{printf "%d", int(t + d*cos(a*3.14159265/180) + 0.5)}')
    oy=$(awk -v t="$ty" -v a="$oa" -v d="$od" \
        'BEGIN{printf "%d", int(t + d*sin(a*3.14159265/180) + 0.5)}')

    # Phase 1 — ballistic move to overshoot point via Bézier (TECHNIQUE 2)
    # Read position ONCE to avoid X/Y coming from different samples (race fix)
    local _loc _cx _cy
    _loc=$(xdotool getmouselocation --shell 2>/dev/null)
    _cx=$(printf '%s\n' "$_loc" | awk -F= '/^X/{print $2}')
    _cy=$(printf '%s\n' "$_loc" | awk -F= '/^Y/{print $2}')
    local cx=${_cx:-$tx} cy=${_cy:-$ty}
    bezier_move "$cx" "$cy" "$ox" "$oy"

    # Correction pause: 60-120ms (sub-movement initiation delay)
    sleep "$(awk -v seed="$RANDOM" \
        'BEGIN{srand(seed); printf "%.4f", 0.060 + rand()*0.060}')"

    # Phase 2 — precise corrective sub-movement onto exact target
    bezier_move "$ox" "$oy" "$tx" "$ty"

    # Click with log-normal hold (TECHNIQUE 1)
    xdotool mousedown 1 2>/dev/null
    sleep "$(_lognormal_ms -2.8 0.22)"   # ~61ms mean hold
    xdotool mouseup 1 2>/dev/null

    # Post-click drift: ±5px (hand never freezes on the exact click pixel)
    local driftx=$(( tx + (RANDOM % 11) - 5 ))
    local drifty=$(( ty + (RANDOM % 7)  - 3 ))
    bezier_move "$tx" "$ty" "$driftx" "$drifty"
}

# ─────────────────────────────────────────────────────────────────────────────
#  TECHNIQUE 6 — ImageMagick pixel color reading  (pure shell, no Python)
#
#  Source: superuser.com/q/576949 "Getting the predominant colour in an image"
#          stackoverflow.com/q/27359798 "print image histogram statistics"
#          stackoverflow.com/q/69874962 "list number of different color pixels"
#
#  "convert img.png txt:-" outputs one line per pixel:
#    x,y: (R,G,B,A)  #RRGGBB  srgb(R,G,B)
#  awk splits on the field separators colon/comma/space/parens to get R G B.
#  This is the pure-shell equivalent of our embedded Python decode_png() +
#  count_color() functions — no Python subprocess needed.
#
#  imgmag_pixel IMGPATH X Y         → "R G B" at exact coordinate
#  imgmag_count_range IMGPATH …     → pixel count in RGB box within a crop
#  imgmag_popup_gray IMGPATH        → gray pixel count (shell popup detection)
# ─────────────────────────────────────────────────────────────────────────────
imgmag_pixel() {
    local img=$1 x=$2 y=$3
    [ $HAS_IMGMAG -eq 0 ] && echo "0 0 0" && return
    # txt: format per pixel:  "x,y: (R,G,B,A)  #RRGGBB  name"
    # Splitting "0,0: (255,128,64,255)" on [:(,) ]+ yields:
    #   $1=x  $2=y  $3=R  $4=G  $5=B  $6=A
    convert "$img" txt:- 2>/dev/null | \
        awk -F'[:(,) ]+' -v px="$x" -v py="$y" '
        NR > 1 && $1+0 == px && $2+0 == py {
            printf "%d %d %d\n", $3, $4, $5; exit
        }'
}

imgmag_count_range() {
    # Count pixels in RGB box [lo_r..hi_r]×[lo_g..hi_g]×[lo_b..hi_b]
    # within the given crop region of the screenshot.
    local img=$1 cx=$2 cy=$3 cw=$4 ch=$5
    local lo_r=$6 lo_g=$7 lo_b=$8 hi_r=$9 hi_g=${10} hi_b=${11}
    [ $HAS_IMGMAG -eq 0 ] && echo 0 && return
    convert "$img" -crop "${cw}x${ch}+${cx}+${cy}" +repage txt:- 2>/dev/null | \
        awk -F'[:(,) ]+' \
            -v r0="$lo_r" -v g0="$lo_g" -v b0="$lo_b" \
            -v r1="$hi_r" -v g1="$hi_g" -v b1="$hi_b" '
        NR > 1 {
            r=$3+0; g=$4+0; b=$5+0    # $3=R $4=G $5=B (not $4,$5,$6)
            if (r>=r0 && r<=r1 && g>=g0 && g<=g1 && b>=b0 && b<=b1) n++
        }
        END { print n+0 }'
}

# Shell-only popup gray-mass check — ImageMagick equivalent of Python popup_open()
# Uses ImageMagick's -fuzz flag to match pixels within 15% of MC inventory gray
imgmag_popup_gray() {
    local img=${1:-/tmp/mc_shell_snap.png}
    [ $HAS_IMGMAG -eq 0 ] && echo 0 && return
    convert "$img" \
        -fuzz 15% -fill white -opaque "rgb(198,198,198)" \
        -fill black +opaque white \
        -format "%[fx:w*h*mean]" info: 2>/dev/null | \
        awk '{print int($1+0.5)}'
}

# ─────────────────────────────────────────────────────────────────────────────
#  TECHNIQUE 7 — ImageMagick MAE template slot matching  (pure shell)
#
#  Source: imagemagick.org/script/compare.php
#          "compare -metric MAE img1 img2 /dev/null" → Mean Absolute Error
#          (lower = more similar, 0 = identical)
#
#  Shell-only equivalent of the Python _template_mad() function: crops a slot
#  from the prescan screenshot, box-averages it to 16×16 with -resize, then
#  compares against saved reference PNGs using ImageMagick's compare binary.
#  compare's C implementation is faster than our Python awk loop for large batches.
#
#  save_slot_template IMGPATH SX SY SW SH LABEL
#  imgmag_slot_match  IMGPATH SX SY SW SH → "confirm"|"deny"|"unknown"
# ─────────────────────────────────────────────────────────────────────────────
IMGMAG_TMPL_DIR="/tmp/mc_imgmag_templates"
IMGMAG_MAE_THRESHOLD=28   # slots within this MAE → candidate match
IMGMAG_MAE_MARGIN=10      # winning label must beat other by ≥ this

save_slot_template() {
    local img=$1 sx=$2 sy=$3 sw=$4 sh=$5 label=$6
    [ $HAS_IMGMAG -eq 0 ] && return 1
    mkdir -p "$IMGMAG_TMPL_DIR"
    convert "$img" \
        -crop "${sw}x${sh}+${sx}+${sy}" +repage \
        -resize 16x16! \
        "${IMGMAG_TMPL_DIR}/${label}.png" 2>/dev/null && \
        echo "[ imgmag ] template saved → ${IMGMAG_TMPL_DIR}/${label}.png"
}

imgmag_slot_match() {
    local img=$1 sx=$2 sy=$3 sw=$4 sh=$5
    [ $HAS_IMGMAG -eq 0 ] && echo "unknown" && return
    local conf="${IMGMAG_TMPL_DIR}/confirm.png"
    local deny="${IMGMAG_TMPL_DIR}/deny.png"
    [ ! -f "$conf" ] || [ ! -f "$deny" ] && echo "unknown" && return

    local crop_tmp
    crop_tmp=$(mktemp /tmp/mc_slot_XXXXXX.png)
    convert "$img" \
        -crop "${sw}x${sh}+${sx}+${sy}" +repage \
        -resize 16x16! \
        "$crop_tmp" 2>/dev/null

    local mae_c mae_d
    mae_c=$(compare -metric MAE "$crop_tmp" "$conf" /dev/null 2>&1 | awk '{print int($1+0.5)}')
    mae_d=$(compare -metric MAE "$crop_tmp" "$deny" /dev/null 2>&1 | awk '{print int($1+0.5)}')
    rm -f "$crop_tmp"

    awk -v mc="${mae_c:-999}" -v md="${mae_d:-999}" \
        -v thr="$IMGMAG_MAE_THRESHOLD" -v margin="$IMGMAG_MAE_MARGIN" '
    BEGIN {
        if (mc > thr && md > thr)            { print "unknown"; exit }
        if (mc <= thr && mc+margin <= md)    { print "confirm"; exit }
        if (md <= thr && md+margin <= mc)    { print "deny";    exit }
        print "unknown"
    }'
}

# ─────────────────────────────────────────────────────────────────────────────
#  TECHNIQUE 8 — xinput test-xi2 popup recorder  (record real human solves)
#
#  Source: world-playground-deceit.net/blog/2025/07/x11-record-and-replay.html
#          (published 2025-07-03, tags: sh, programming)
#          xinput(1), xmodmap(1)
#
#  The blog shows how to record X11 events with "xinput test-xi2 --root"
#  and replay them via xdotool.  We adapt the AWK converter to produce a
#  self-contained replay script instead of a live pipe.
#
#  Usage:  mc_record_solve [output.sh]   — then press Scroll_Lock to stop
#          bash output.sh               — replay the recorded solve
# ─────────────────────────────────────────────────────────────────────────────
XINPUT_AWK=/tmp/mc_xinput2xdo.awk
cat > "$XINPUT_AWK" << 'AWKEOF'
# Convert xinput test-xi2 --root events → xdotool replay script
# Adapted from: world-playground-deceit.net/blog/2025/07/x11-record-and-replay.html
# Stop recording by pressing Scroll_Lock.
function emit() {
    if (prev_time)
        print "sleep", sprintf("%.4f", (time - prev_time) / 1000.0)
    prev_time = time
    if (pos[1] != prev_x || pos[2] != prev_y) {
        print "xdotool mousemove", pos[1], pos[2]
        prev_x = pos[1];  prev_y = pos[2]
    }
    print "xdotool", cmd, arg
}
function read_keymap(   line, f) {
    while (("xmodmap -pke" | getline line) == 1) {
        split(line, f)
        if (length(f) > 3) xkbmap[f[2]] = f[4]
    }
    close("xmodmap -pke")
}
BEGIN { read_keymap() }
$1 == "EVENT" {
    if (cmd) emit()
    if      ($4 == "(ButtonPress)")   cmd = "mousedown"
    else if ($4 == "(ButtonRelease)") cmd = "mouseup"
    else if ($4 == "(KeyPress)")      cmd = "keydown"
    else if ($4 == "(KeyRelease)")    cmd = "keyup"
    else                              cmd = ""
    next
}
cmd && $1 == "time:"   { time = $2; next }
cmd && $1 == "detail:" {
    arg = (cmd ~ /^key/ ? xkbmap[$2] : $2)
    if (cmd == "keydown" && arg == "Scroll_Lock") exit
    next
}
cmd && $1 == "event:"  { split($2, pos, "/"); next }
AWKEOF

mc_record_solve() {
    local output=${1:-"/tmp/mc_solve_replay_$(date +%s).sh"}
    if [ $HAS_XINPUT -eq 0 ]; then
        echo "[ record ] xinput not found. Install: sudo apt install xinput"; return 1
    fi
    printf '#!/bin/bash\n# Recorded solve — replay: bash %s\n' "$output" > "$output"
    echo "[ record ] Recording... Press Scroll_Lock to stop → $output"
    xinput test-xi2 --root 2>/dev/null | awk -f "$XINPUT_AWK" >> "$output"
    chmod +x "$output"
    echo "[ record ] Saved $(wc -l < "$output") lines → $output"
}

# ─────────────────────────────────────────────────────────────────────────────
#  TECHNIQUE 9 — xdotool mousemove restore  (cursor save/restore)
#
#  Source: xdotool(1) man page — "mousemove restore"
#  "You can move the mouse to the previous location if you specify 'restore'
#   instead of X and Y.  Restoring only works if you have moved previously
#   in this same command invocation."
#
#  Saves cursor position before a disruptive move, restores it afterward —
#  all in a single xdotool subprocess call so restore is guaranteed to work.
#
#  Usage:  atomic_move_restore dest_x dest_y    # moves there, clicks, restores
# ─────────────────────────────────────────────────────────────────────────────
atomic_move_restore() {
    local dx=$1 dy=$2
    # Single xdotool chain: current pos is saved implicitly at invocation start,
    # then restored after click.
    xdotool mousemove "$dx" "$dy" mousedown 1 2>/dev/null
    sleep "$(_lognormal_ms -2.8 0.22)"
    xdotool mouseup 1 mousemove restore 2>/dev/null
}

# ─────────────────────────────────────────────────────────────────────────────
#  TECHNIQUE 10 — xdotool command chaining  (single subprocess atomicity)
#
#  Source: xdotool(1) man page — "COMMAND CHAINING"
#  "Multiple commands may be specified on the command line and they will be
#   executed in order."  Chaining move+down+up in one xdotool call eliminates
#   the inter-process timing gaps that separate subprocess calls introduce.
#  Also uses --sync: "wait until the mouse is actually moved" before clicking.
#
#  Usage:  chain_click x y [hold_ms]
# ─────────────────────────────────────────────────────────────────────────────
chain_click() {
    local cx=$1 cy=$2 hold_ms=${3:-65}
    # Move (--sync waits for completion), then down+up in same process
    xdotool mousemove --sync "$cx" "$cy" mousedown 1 2>/dev/null
    sleep "$(awk -v ms="$hold_ms" 'BEGIN{printf "%.4f",ms/1000}')"
    xdotool mouseup 1 2>/dev/null
}

echo "[ MC Farm ] research techniques loaded (Bézier/lognormal/polar/ImgMag/overshoot/xinput/chaining)"

# ═══════════════════════════════════════════════════════════════════════════════
#  SHELL BACKUP AFK SOLVER
#
#  Activated automatically when the Python solver process dies (liveness check
#  in the spam loop below).  Pure shell: ImageMagick + xdotool + awk only.
#  No Python, no pip, no extra runtimes.
#
#  Pipeline mirrors the Python solver exactly:
#    sh_popup_open → sh_find_strip → sh_prescan →
#    sh_hover_spiral → sh_read_tooltip → sh_click_verify
#
#  Color constants match Python src:
#    C_GRAY:        rgb(100-230, 100-230, 100-230)  inventory background
#    C_GREEN:       rgb(48-135, 195-255, 48-135)    §a "Click to Confirm"
#    C_RED:         rgb(120-255, 20-120, 20-120)    §c "Do not click" (widened)
#    C_TOOLTIP_BG:  rgb(0-50, 0-18, 0-50)           tooltip dark-purple bg
#                   (Python line: C_TOOLTIP_BG = ((0,0,0),(50,18,50)))
#
#  Hover timing matches Python:
#    60ms initial (MC renders tooltip in 1 tick = 50ms)
#    then 65ms polls  (identical to Python _wait_for_tooltip)
#    HOVER_WAIT  =  300ms total (Python HOVER_WAIT = 0.30)
#
#  Sources for every technique used here are in the function comments above.
# ═══════════════════════════════════════════════════════════════════════════════

SH_SNAP="/tmp/_mc_sh_afk.png"        # backup solver screenshot path
SH_LOG="/tmp/mc_sh_afk_log.txt"      # backup solver diagnostic log
SH_POLL_S="0.40"                     # seconds between popup polls
SH_HOVER_MS=300                      # ms total to wait for tooltip (increased from 220 for 14-FPS servers)
SH_SWEEP_TIMEOUT=5                   # seconds before aborting a sweep
#
# ── Calibrated from attached_assets/ screenshots (1366×694, JartexNetwork) ──
#
# SH_POPUP_MIN_W / SH_POPUP_MIN_H:
#   The AFK popup bbox is consistently 346x328 on this screen.
#   A bare hotbar strip is ~1366x45 — PH never reaches 200 without a popup.
#   Require ≥200×200 so a thin UI strip never false-triggers.
SH_POPUP_MIN_W=200
SH_POPUP_MIN_H=200
#
# SH_TIP_THRESH — dark-purple tooltip background pixel count threshold.
#   Measured from all 68 screenshots:
#     Tooltip VISIBLE  → 21,045 – 45,084 dark-purple px  (r≤42, g≤10, b≤42)
#     Tooltip ABSENT   →      0 –    383 dark-purple px
#   Gap is enormous.  2000 sits safely in the middle.
SH_TIP_THRESH=2000
#
# SH_GRAY_THRESH — legacy value kept only for documentation.
#   Full-screen gray count is NOT a reliable popup detector:
#   popup-open images have 45k–86k gray px AND no-tooltip images have 74k–86k.
#   The overlap makes 400 (the old threshold) trivially always-true.
#   sh_popup_open now uses the bbox size check instead.
SH_GRAY_THRESH=400   # unused by sh_popup_open; kept for reference

_sh_log() {
    local ts; ts=$(date +%H:%M:%S 2>/dev/null || echo "??")
    printf "[%s] %s\n" "$ts" "$*" | tee -a "$SH_LOG" >&2
}

# ── sh_take_snap ─────────────────────────────────────────────────────────────
# Full-screen screenshot to SH_SNAP (or given path).
# Uses maim on the active window if available — avoids stale compositor frames
# (same strategy as Python scrot() with HAS_MAIM).
sh_take_snap() {
    local out="${1:-$SH_SNAP}"
    if command -v maim >/dev/null 2>&1; then
        local wid; wid=$(xdotool getactivewindow 2>/dev/null)
        if [ -n "$wid" ]; then
            maim -i "$wid" "$out" 2>/dev/null && return
        fi
        maim "$out" 2>/dev/null && return
    fi
    scrot -z "$out" 2>/dev/null
}

# ── sh_popup_open ─────────────────────────────────────────────────────────────
# Returns 0 if the AFK popup appears to be open.
#
# Strategy: use ImageMagick -trim to find the bounding box of the largest gray
# region and check that it is at least SH_POPUP_MIN_W × SH_POPUP_MIN_H pixels.
#
#   WHY NOT gray pixel count?
#     Full-screen gray count is unreliable: measured from 68 real screenshots,
#     both popup-open and normal-game images have 45k–86k gray pixels (overlap).
#     The old threshold of 400 was always true.
#
#   WHY bbox size?
#     The AFK popup is a compact rectangle (~346×328 on 1366×694).
#     After -fuzz 15% -opaque the gray region trims to exactly that box.
#     The Minecraft hotbar is a ~1366×45 thin strip — PH never ≥ 200 without popup.
#     Caveat: an open player inventory (pressing E) is also a large gray box and
#     would false-trigger.  The solver is only launched when the AFK popup fires,
#     not during normal play, so this is acceptable.
#
sh_popup_open() {
    [ "$HAS_IMGMAG" -eq 0 ] && return 1
    sh_take_snap "$SH_SNAP"
    local bbox PW PH
    bbox=$(convert "$SH_SNAP" \
        -fuzz 15% -fill white -opaque "rgb(198,198,198)" \
        -fill black +opaque white \
        -trim -format "%wx%h" info: 2>/dev/null)
    PW=$(printf '%s' "$bbox" | awk -F'x' '{print $1+0}')
    PH=$(printf '%s' "$bbox" | awk -F'x' '{print $2+0}')
    [ "${PW:-0}" -ge "$SH_POPUP_MIN_W" ] && [ "${PH:-0}" -ge "$SH_POPUP_MIN_H" ]
}

# ── sh_find_strip ─────────────────────────────────────────────────────────────
# Locate the AFK slot strip in SH_SNAP.  Mirrors Python find_afk_strip():
#
#   Step 1 — isolate gray inventory pixels via ImageMagick fuzz+opaque trick,
#             then use -trim to get the bounding box of the popup region.
#
#   Step 2 — compress the popup crop to 1-pixel wide (-filter Box -scale 1xH!)
#             so each output row represents the average of its full-width row.
#             Dark rows (< 40/255) are the "Inventory" separator band or title
#             bar.  Find the first dark row after the top 30% = separator.
#             (The title bar dark band is always in the top 30%.)
#
#   Step 3 — derive:
#               strip_top = popup_top + title_bar (~18px, constant)
#               strip_bot = popup_top + sep_rel  (or 49% fallback)
#               slot_w    = popup_width / 9
#               slot_h    = (strip_bot - strip_top) / 3
#               strip_left/right centered inside popup (Python formula)
#
# Echo: "sl st sr sb slot_w slot_h"  or empty on failure.
sh_find_strip() {
    [ "$HAS_IMGMAG" -eq 0 ] && return 1
    local img="${1:-$SH_SNAP}"

    # Step 1: Isolate gray pixels → bounding box
    local bbox
    bbox=$(convert "$img" \
        -fuzz 15% -fill white -opaque "rgb(198,198,198)" \
        -fill black +opaque white \
        -trim -format "%wx%h+%X+%Y" info: 2>/dev/null)
    [ -z "$bbox" ] && return 1

    # ImageMagick emits "%wx%h+%X+%Y" where %X/%Y already carry their sign,
    # so positive offsets produce "346x328++508++180" (double-plus).
    # -F'[x+]+' (one-or-more) collapses "++" to a single separator so
    # $3=508 and $4=180 correctly; plain -F'[x+]' leaves an empty $3 (=0).
    local PW PH PX PY
    PW=$(printf '%s' "$bbox" | awk -F'[x+]+' '{print $1+0}')
    PH=$(printf '%s' "$bbox" | awk -F'[x+]+' '{print $2+0}')
    PX=$(printf '%s' "$bbox" | awk -F'[x+]+' '{print $3+0}')
    PY=$(printf '%s' "$bbox" | awk -F'[x+]+' '{print $4+0}')

    { [ "${PW:-0}" -ge 80 ] && [ "${PH:-0}" -ge 50 ]; } 2>/dev/null || return 1

    # Step 2: Compress each row to 1 pixel, find first dark row after top 30%
    # -filter Box -scale "1xH!" box-averages each row into one pixel.
    # Gray→white, dark→black binary image: dark pixel (R<40) = separator.
    local sep_rel
    sep_rel=$(convert "$img" \
        -crop "${PW}x${PH}+${PX}+${PY}" +repage \
        -fuzz 15% -fill white -opaque "rgb(198,198,198)" \
        -fill black +opaque white \
        -filter Box -scale "1x${PH}!" \
        txt:- 2>/dev/null | \
        awk -F'[:(,) ]+' -v H="$PH" '
        NR > 1 {
            y = $2+0; v = $3+0
            # First dark row (v<40 = mostly non-gray) after top 30%
            if (y > H*0.30 && v < 40 && !found) { print y; found=1 }
        }')

    # Step 3: Derive coordinates
    local TITLE_H=18    # title bar height (constant across GUI scales)
    local STRIP_TOP STRIP_BOT SLOT_W SLOT_H STRIP_L STRIP_R

    STRIP_TOP=$(( PY + TITLE_H ))

    if [ -n "$sep_rel" ] && { [ "$sep_rel" -gt 0 ] 2>/dev/null; }; then
        STRIP_BOT=$(( PY + sep_rel ))
    else
        STRIP_BOT=$(( PY + PH * 49 / 100 ))   # Python 49% fallback
    fi

    SLOT_W=$(( PW / 9 ))
    [ "$SLOT_W" -lt 8 ] && SLOT_W=8

    local STRIP_H=$(( STRIP_BOT - STRIP_TOP ))
    [ "$STRIP_H" -lt 30 ] && STRIP_H=30
    SLOT_H=$(( STRIP_H / 3 ))
    [ "$SLOT_H" -lt 8 ] && SLOT_H=8

    # Python: strip_left = pl + (pw - 9*slot_w) // 2
    STRIP_L=$(( PX + (PW - 9 * SLOT_W) / 2 ))
    STRIP_R=$(( STRIP_L + 9 * SLOT_W ))

    echo "$STRIP_L $STRIP_TOP $STRIP_R $STRIP_BOT $SLOT_W $SLOT_H"
}

# ── sh_prescan ────────────────────────────────────────────────────────────────
# Colorful-pixel prescan over the AFK slot strip.
# Mirrors Python prescan_strip(): for each of the 27 slots (3 rows × 9 cols)
# count pixels whose (max_channel - min_channel) > 28 inside the center ¼-box.
#   ≥15 colorful pixels → HIGH confidence (item definitely present)
#   ≥5                  → MED confidence (possible item)
#   <5                  → skip (empty slot)
#
# Uses ImageMagick txt: output + awk (single pass, no per-slot convert calls).
# Output: lines "row col HIGH|MED", sorted HIGH first.
sh_prescan() {
    local sl=$1 st=$2 sr=$3 sb=$4 slot_w=$5 slot_h=$6
    [ "$HAS_IMGMAG" -eq 0 ] && return 1

    local strip_w=$(( sr - sl + 4 ))
    local strip_h=$(( sb - st + 4 ))
    local strip_snap="/tmp/_mc_sh_strip_ps.png"

    # Crop the strip region out of SH_SNAP
    convert "$SH_SNAP" \
        -crop "${strip_w}x${strip_h}+${sl}+${st}" +repage \
        "$strip_snap" 2>/dev/null || return 1

    local half=$(( slot_w / 4 ))
    [ "$half" -lt 4 ] && half=4

    # Single awk pass: classify every pixel once
    convert "$strip_snap" txt:- 2>/dev/null | \
        awk -F'[:(,) ]+' \
            -v sw="$slot_w" -v sh_="$slot_h" \
            -v half="$half" -v HI=15 -v MED=5 '
        NR > 1 {
            px=$1+0; py=$2+0; r=$3+0; g=$4+0; b=$5+0
            col=int(px/sw); row=int(py/sh_)
            if (col>8 || row>2) next
            cx=col*sw+int(sw/2); cy=row*sh_+int(sh_/2)
            if (px<cx-half||px>cx+half||py<cy-half||py>cy+half) next
            mn=r; if(g<mn)mn=g; if(b<mn)mn=b
            mx=r; if(g>mx)mx=g; if(b>mx)mx=b
            if (mx-mn>28) cnt[row,col]++
        }
        END {
            for (k in cnt) {
                split(k,a,SUBSEP); n=cnt[k]
                if      (n>=HI)  print a[1],a[2],"HIGH"
                else if (n>=MED) print a[1],a[2],"MED"
            }
        }' | sort -k3,3r -k1,1n -k2,2n    # HIGH first, left-to-right, top-to-bottom
    rm -f "$strip_snap"
}

# ── sh_has_tooltip ────────────────────────────────────────────────────────────
# Returns 0 if SH_SNAP contains tooltip evidence:
#   Primary  : ≥SH_TIP_THRESH dark-purple bg pixels (C_TOOLTIP_BG 0-50, 0-18, 0-50)
#   Fallback : ≥8 green title pixels (§a "Click to Confirm") OR red title pixels
# Widened from (0-42,0-10,0-42) to (0-50,0-18,0-50) based on measured bg colors.
sh_has_tooltip() {
    [ "$HAS_IMGMAG" -eq 0 ] && return 1
    local snap="${1:-$SH_SNAP}"
    local n; n=$(imgmag_count_range "$snap" 0 0 99999 99999 0 0 0 50 18 50)
    [ "${n:-0}" -ge "$SH_TIP_THRESH" ] && return 0
    # Fallback: detect by colored title text (green §a or red §c)
    local ng; ng=$(imgmag_count_range "$snap" 0 0 99999 99999 48 195 48 135 255 135)
    [ "${ng:-0}" -ge 8 ] && return 0
    local nr; nr=$(imgmag_count_range "$snap" 0 0 99999 99999 120 20 20 255 120 120)
    [ "${nr:-0}" -ge 8 ] && return 0
    return 1
}

# ── sh_wait_tooltip ────────────────────────────────────────────────────────────
# Poll for tooltip appearance after a hover move.
# Timing mirrors Python _wait_for_tooltip():
#   sleep 60ms first (MC renders within 1 game tick = 50ms)
#   then poll every 65ms until deadline
# Returns 0 if tooltip appeared, 1 on timeout.
sh_wait_tooltip() {
    local timeout_ms="${1:-$SH_HOVER_MS}"
    sleep 0.06    # ≥ 1 game tick; Python does the same
    local remain_ms=$(( timeout_ms - 60 ))
    local steps=$(( remain_ms / 65 + 1 ))
    [ "$steps" -lt 1 ] && steps=1
    local i=0
    while [ $i -lt "$steps" ]; do
        sh_take_snap "$SH_SNAP"
        sh_has_tooltip "$SH_SNAP" && return 0
        sleep 0.065
        i=$(( i + 1 ))
    done
    return 1
}

# ── sh_read_tooltip ───────────────────────────────────────────────────────────
# Classify tooltip as "confirm", "deny", or "empty".
# Requires quorum ≥2 of 3 independent backends (mirrors Python CONFIRM_QUORUM=2).
#
# Localization: find tooltip dark-purple bounding box → scan only the top 25%
# (title line: "Click to Confirm" or "Do not click").
# Fallback: scan a 600×400 region centred on the hover point so game-world
# colors far from the cursor don't generate false votes.
#
# Backend 1 — RGB absolute range scan (Python ask_color):
#   GREEN pixels: r 48-135, g 195-255, b 48-135  (§a #55FF55)
#   RED pixels:   r 188-255, g 38-118, b 38-118  (§c #FF5555)
#   ≥5 pixels of winning color → 1 vote
#
# Backend 2 — Channel dominance ratio (Python ask_ratio):
#   G/(R+G+B) > 0.45 AND G>80 → green;  R/(R+G+B) > 0.45 AND R>80 → red
#   ≥4 pixels of winning color → 1 vote
#
# Backend 3 — Longest consecutive colored run (Python ask_runs):
#   run ≥3 of (G>R+40 AND G>B+40 AND G>100) → green
#   run ≥3 of (R>G+40 AND R>B+40 AND R>100) → red
#   winning run is longer → 1 vote
sh_read_tooltip() {
    [ "$HAS_IMGMAG" -eq 0 ] && echo "empty" && return
    local snap="${1:-$SH_SNAP}"
    local hover_x="${2:-}" hover_y="${3:-}"

    # Locate tooltip box (dark-purple bg; Python C_TOOLTIP_BG = (0-50,0-18,0-50))
    local tip_bbox
    tip_bbox=$(convert "$snap" \
        -fuzz 8% -fill white -opaque "rgb(21,5,21)" \
        -fill black +opaque white \
        -trim -format "%wx%h+%X+%Y" info: 2>/dev/null)

    # Same double-plus issue as sh_find_strip: use [x+]+ to collapse "++N" → "N"
    local TW TH TX TY zone_x zone_y zone_w zone_h
    TW=$(printf '%s' "$tip_bbox" | awk -F'[x+]+' '{print $1+0}')
    TH=$(printf '%s' "$tip_bbox" | awk -F'[x+]+' '{print $2+0}')
    TX=$(printf '%s' "$tip_bbox" | awk -F'[x+]+' '{print $3+0}')
    TY=$(printf '%s' "$tip_bbox" | awk -F'[x+]+' '{print $4+0}')

    # ── Determine scan zone ──────────────────────────────────────────────────
    #
    # WHY NOT bbox detect via -fuzz -opaque?
    #   Tested against real screenshots: -fuzz 8% -opaque "rgb(21,5,21)" on a
    #   1366×694 JartexNetwork screenshot produces a bbox of 1362×690 (nearly
    #   the entire screen) because the dark Minecraft night-sky / cave pixels
    #   sit within fuzz range of the tooltip bg color.
    #
    # FIX: derive the scan zone from the hover position instead.
    #   In Minecraft, the tooltip renders ABOVE and slightly right of the cursor.
    #   For the AFK popup strip (y ≈ 198–508 on 1366×694), hovering any slot
    #   puts the tooltip fully within the popup box (y 180–508).
    #   We scan a 260×130 window centered above the hover point — wide enough
    #   to catch the tooltip at any slot position, narrow enough to exclude the
    #   game world below/around the popup.
    #
    if [ -n "$hover_x" ] && [ -n "$hover_y" ]; then
        # Primary: wide zone around the cursor — tooltip can render above, below, or
        # to either side depending on screen position.  260×130 above-only missed
        # tooltips on top-row slots where MC renders them BELOW/RIGHT the cursor.
        zone_x=$(( hover_x - 280 )); [ "$zone_x" -lt 0 ] && zone_x=0
        zone_y=$(( hover_y - 280 )); [ "$zone_y" -lt 0 ] && zone_y=0
        zone_w=620; zone_h=520
    elif [ -n "$TW" ] && { [ "$TW" -ge 20 ] && [ "$TW" -lt 1200 ] 2>/dev/null; }; then
        # Secondary: bbox result is plausible (not full-screen), use top 25%
        local title_h=$(( TH / 4 ))
        [ "$title_h" -lt 6 ] && title_h=6
        zone_x=$TX; zone_y=$TY; zone_w=$TW; zone_h=$title_h
    else
        # Last resort: center-screen strip (popup is always near screen center)
        zone_x=300; zone_y=100; zone_w=800; zone_h=400
    fi

    # Crop the scan zone once; run all 3 backends in a single awk pass
    local zone_snap="/tmp/_mc_sh_tipzone.png"
    convert "$snap" \
        -crop "${zone_w}x${zone_h}+${zone_x}+${zone_y}" +repage \
        "$zone_snap" 2>/dev/null

    convert "$zone_snap" txt:- 2>/dev/null | \
        awk -F'[:(,) ]+' '
        NR > 1 {
            r=$3+0; g=$4+0; b=$5+0

            # ── Backend 1: RGB absolute range (Python ask_color) ────────────
            if (r>=48  && r<=135 && g>=195 && g<=255 && b>=48  && b<=135) b1g++
            if (r>=120 && r<=255 && g>=20  && g<=120 && b>=20  && b<=120) b1r++

            # ── Backend 2: Channel dominance ratio (Python ask_ratio) ───────
            tot=r+g+b
            if (tot>=120) {
                if (g/tot>0.45 && g>80)  b2g++
                else if (r/tot>0.45 && r>80) b2r++
            }

            # ── Backend 3: Longest consecutive run (Python ask_runs) ────────
            is_g=(g>r+40 && g>b+40 && g>100)
            is_r=(r>g+40 && r>b+40 && r>100)
            if      (is_g) { b3gr++; b3rr=0; if(b3gr>b3mg) b3mg=b3gr }
            else if (is_r) { b3rr++; b3gr=0; if(b3rr>b3mr) b3mr=b3rr }
            else             { b3gr=0; b3rr=0 }
        }
        END {
            # Quorum: ≥2 of 3 backends must agree (Python CONFIRM_QUORUM=2)
            nc=0; nd=0
            if (b1g>=5  && b1g>b1r)   nc++; else if (b1r>=5  && b1r>b1g)   nd++
            if (b2g>=4  && b2g>b2r)   nc++; else if (b2r>=4  && b2r>b2g)   nd++
            if (b3mg>=3 && b3mg>b3mr) nc++; else if (b3mr>=3 && b3mr>b3mg) nd++
            if      (nc>=2) print "confirm"
            else if (nd>=2) print "deny"
            else            print "empty"
        }'
    rm -f "$zone_snap"
}

# ── sh_hover_spiral ────────────────────────────────────────────────────────────
# Move to slot center then try 17 spiral positions, waiting for tooltip after
# each.  Mirrors Python hover_spiral() HOVER_SPIRAL positions exactly:
#   ring 0: center (full timeout)
#   ring 1: ±4px cross + ±3px diagonal (half timeout)
#   ring 2: ±8px cross + ±6px diagonal (40% timeout)
#
# Uses bezier_move (TECHNIQUE 2) for human-like cursor paths.
# Gets current position ONCE per step to avoid the race condition described
# in the gotchas (two separate getmouselocation calls can sample different
# positions if the pointer moves between them).
#
# Echo: "found nx ny"  or  "notfound sx sy"
sh_hover_spiral() {
    local sx=$1 sy=$2

    # (dx dy timeout_ms) — matches Python HOVER_SPIRAL with updated HOVER_WAIT=300ms
    local -a spiral=(
        "0 0 300"
        "0 -4 150" "4 0 150" "0 4 150" "-4 0 150"
        "-3 -3 150" "3 -3 150" "3 3 150" "-3 3 150"
        "0 -8 120" "8 0 120" "0 8 120" "-8 0 120"
        "-6 -6 120" "6 -6 120" "6 6 120" "-6 6 120"
    )

    local triplet dx dy ms nx ny
    for triplet in "${spiral[@]}"; do
        read -r dx dy ms <<< "$triplet"
        nx=$(( sx + dx ))
        ny=$(( sy + dy ))

        # Read current position once (race-condition-safe)
        local _loc _cx _cy
        _loc=$(xdotool getmouselocation --shell 2>/dev/null)
        _cx=$(printf '%s\n' "$_loc" | awk -F= '/^X/{print $2}')
        _cy=$(printf '%s\n' "$_loc" | awk -F= '/^Y/{print $2}')
        _cx="${_cx:-$nx}"; _cy="${_cy:-$ny}"

        # TECHNIQUE 2: Bézier move; fall back to plain mousemove if not available
        bezier_move "$_cx" "$_cy" "$nx" "$ny" 2>/dev/null || \
            xdotool mousemove --sync "$nx" "$ny" 2>/dev/null

        if sh_wait_tooltip "$ms"; then
            echo "found $nx $ny"
            return
        fi
    done

    echo "notfound $sx $sy"
}

# ── sh_click_verify ────────────────────────────────────────────────────────────
# Click at (tx, ty) using overshoot_click (TECHNIQUE 5), then verify the
# popup closed.  Mirrors Python click_and_verify(): up to 3 retries, 150ms gap.
#
# WHY NOT gray pixel count?
#   After clicking, the popup closes but the farm scene still has 74k–86k gray
#   pixels (stone, hotbar, etc.).  The old SH_GRAY_THRESH=400 check would
#   never trigger since even a bare farm scene exceeds 400.
#
# FIX: re-use sh_popup_open (bbox size check).
#   When the popup closes the gray rectangle vanishes → -trim finds only the
#   hotbar strip (PH ≈ 45) which fails the ≥200 height requirement → sh_popup_open
#   returns 1 (closed) → we return success.
#
sh_click_verify() {
    local tx=$1 ty=$2

    # TECHNIQUE 5: overshoot + corrective sub-movement + post-drift
    overshoot_click "$tx" "$ty" 65

    local retries=0
    while [ $retries -lt 3 ]; do
        sleep 0.15
        # sh_popup_open takes its own fresh screenshot internally
        if ! sh_popup_open; then
            _sh_log "  click OK — popup closed"
            return 0
        fi
        retries=$(( retries + 1 ))
        _sh_log "  click retry $retries — popup still open"
        sleep "$(awk -v s="$RANDOM" 'BEGIN{srand(s);printf"%.3f",0.15+rand()*0.10}')"
    done
    _sh_log "  click FAILED — popup still open after 3 retries"
    return 1
}

# ── sh_chat_says_teleported ───────────────────────────────────────────────────
# Bash mirror of Python chat_says_teleported().
#
# Detects "Console teleported you to spawn." in the chat area of the screen
# using the same two-backend approach:
#   1. Tesseract OCR on the chat crop — most accurate (optional dep).
#   2. §6 Gold pixel count via ImageMagick — "Console" prefix color #FFAA00.
#
# Returns 0 (true) if the teleport message is likely present, 1 (false) otherwise.
#
# WHY: JartexNetwork always teleports the player to Spawn ~1-2s before the
# Afk Grinding popup opens.  Detecting this lets the backup solver pre-focus
# the MC window and use tight 80ms polling instead of waiting SH_POLL_S=0.5s.
#
sh_chat_says_teleported() {
    # ── Screen dimensions ─────────────────────────────────────────────────
    local sw sh dims
    dims=$(xdotool getdisplaygeometry 2>/dev/null)
    [ -z "$dims" ] && return 1
    sw=$(echo "$dims" | cut -d' ' -f1)
    sh=$(echo "$dims" | cut -d' ' -f2)

    # ── Chat crop: left 45% × bottom 20% of screen ───────────────────────
    local cx cy cw ch
    cx=0
    cy=$(( sh * 80 / 100 ))
    cw=$(( sw * 45 / 100 ))
    ch=$(( sh - cy ))
    [ "$cw" -lt 50 ] || [ "$ch" -lt 10 ] && return 1

    local chat_snap="/tmp/_mc_chat_sh.png"
    scrot -z -a "${cx},${cy},${cw},${ch}" "$chat_snap" 2>/dev/null
    [ -f "$chat_snap" ] || return 1

    # ── Backend 1: Tesseract OCR ──────────────────────────────────────────
    if command -v tesseract >/dev/null 2>&1; then
        local ocr_text
        ocr_text=$(tesseract "$chat_snap" stdout --psm 6 2>/dev/null \
                   | tr '[:upper:]' '[:lower:]')
        # Require "teleport" (or "teleported") AND "spawn" — same logic as Python
        if echo "$ocr_text" | grep -qE 'teleport' && \
           echo "$ocr_text" | grep -q 'spawn'; then
            _sh_log "  [chat-warn] 'teleport…spawn' via OCR ✓"
            rm -f "$chat_snap"
            return 0
        fi
    fi

    # ── Backend 2: §6 Gold pixel count ───────────────────────────────────
    # Count pixels near #FFAA00 (rgb 255,170,0) — "Console" prefix colour.
    # If ImageMagick is absent, the Bash backup solver has no chat detection
    # (it falls back to normal popup polling — no regression).
    if [ "$HAS_IMGMAG" -eq 1 ]; then
        local n_gold
        n_gold=$(convert "$chat_snap" \
            -define png:compression-level=0 \
            -fill white -fuzz 20% -opaque "rgb(255,170,0)" \
            -fill black +opaque white \
            -format "%[fx:round(w*h*mean)]" info: 2>/dev/null)
        rm -f "$chat_snap"
        n_gold="${n_gold:-0}"
        if [ "$n_gold" -ge 12 ]; then
            _sh_log "  [chat-warn] gold_px=${n_gold} → likely Console message"
            return 0
        fi
        return 1
    fi

    rm -f "$chat_snap"
    return 1
}

# ── shell_afk_solver ──────────────────────────────────────────────────────────
# Main backup solver loop.  Called in background when Python solver dies.
# Runs until FLAG_FILE is removed (same as Python main loop condition).
shell_afk_solver() {
    _sh_log "[ Shell Backup Solver ] started  HAS_IMGMAG=$HAS_IMGMAG  HAS_XINPUT=$HAS_XINPUT"
    local idle=0
    local sh_chat_ctr=0   # chat scan fires every SH_CHAT_EVERY polls
    local SH_CHAT_EVERY=2 # mirror of Python CHAT_DETECT_EVERY

    while [ -f "$FLAG_FILE" ]; do
        sleep "$SH_POLL_S"
        [ -f "$AFK_LOCK" ] && continue   # Python might still hold lock briefly

        # ── Poll for popup ────────────────────────────────────────────────
        if ! sh_popup_open; then
            idle=$(( idle + 1 ))
            [ $(( idle % 50 )) -eq 0 ] && _sh_log "  still watching... (idle=${idle})"

            # ── Chat early-warning (every SH_CHAT_EVERY polls) ───────────
            sh_chat_ctr=$(( sh_chat_ctr + 1 ))
            if [ "$sh_chat_ctr" -ge "$SH_CHAT_EVERY" ]; then
                sh_chat_ctr=0
                if sh_chat_says_teleported; then
                    _sh_log "  ⚡ teleport in chat — pre-focusing MC, tight poll for popup..."
                    # Focus the window now — 1-2s before the popup appears
                    local _pre_wid
                    _pre_wid=$(xdotool search --name "Minecraft" 2>/dev/null | tail -1)
                    if [ -n "$_pre_wid" ]; then
                        xdotool windowfocus    --sync "$_pre_wid" 2>/dev/null
                        xdotool windowactivate --sync "$_pre_wid" 2>/dev/null
                        sleep 0.20
                    fi
                    # Move cursor to screen centre so it lands inside the popup
                    local _psw _psh _pdims
                    _pdims=$(xdotool getdisplaygeometry 2>/dev/null)
                    if [ -n "$_pdims" ]; then
                        _psw=$(echo "$_pdims" | cut -d' ' -f1)
                        _psh=$(echo "$_pdims" | cut -d' ' -f2)
                        xdotool mousemove $(( _psw / 2 )) $(( _psh / 2 )) 2>/dev/null
                    fi
                    # Tight poll: up to 3s at 80ms intervals
                    local _pt _pe _found_popup=0
                    _pt=$SECONDS
                    while [ $(( SECONDS - _pt )) -lt 3 ]; do
                        sleep 0.08
                        if sh_popup_open; then
                            _found_popup=1; break
                        fi
                    done
                    [ "$_found_popup" -eq 0 ] && continue  # false positive — resume
                    # Fall through to popup handling below
                else
                    continue
                fi
            else
                continue
            fi
        fi
        idle=0
        _sh_log "Popup detected!"
        touch "$AFK_LOCK"

        # ── Find strip ────────────────────────────────────────────────────
        local strip
        strip=$(sh_find_strip "$SH_SNAP")
        if [ -z "$strip" ]; then
            _sh_log "  Strip detection failed — watching 30s for manual solve"
            local wt=$SECONDS
            while [ -f "$FLAG_FILE" ] && [ $(( SECONDS - wt )) -lt 30 ]; do
                sleep 0.5
                ! sh_popup_open && break   # sh_popup_open takes its own fresh snap
            done
            rm -f "$AFK_LOCK"; continue
        fi

        local sl st sr sb slot_w slot_h
        read -r sl st sr sb slot_w slot_h <<< "$strip"
        _sh_log "  Strip: L=$sl T=$st R=$sr B=$sb  slot=${slot_w}×${slot_h}px"

        # ── Focus Minecraft window (CRITICAL — see Python focus_mc_window()) ─
        # Without focus, xdotool clicks land at the right pixel but Java's
        # AWT event queue discards them → popup never closes → server kick.
        local _mc_wid
        _mc_wid=$(xdotool search --name "Minecraft" 2>/dev/null | tail -1)
        if [ -n "$_mc_wid" ]; then
            xdotool windowfocus   --sync "$_mc_wid" 2>/dev/null
            xdotool windowactivate --sync "$_mc_wid" 2>/dev/null
            # Research-confirmed: LWJGL needs 200ms to process focus before
            # XTEST clicks are accepted. 100ms was marginal.
            sleep 0.20
            _sh_log "  MC window (id=$_mc_wid) focused ✓"
        else
            _sh_log "  WARNING: no Minecraft window found — clicks may not register"
        fi

        # Human-like reaction delay: 200-650ms (Python REACT_MIN/REACT_MAX)
        sleep "$(awk -v s="$RANDOM" 'BEGIN{srand(s);printf"%.3f",0.2+rand()*0.45}')"

        # ── Prescan — find slots that contain items ───────────────────────
        local candidates
        candidates=$(sh_prescan "$sl" "$st" "$sr" "$sb" "$slot_w" "$slot_h" 2>/dev/null)
        if [ -z "$candidates" ]; then
            _sh_log "  Prescan failed — falling back to all 27 slots"
            candidates=$(awk 'BEGIN{for(r=0;r<3;r++)for(c=0;c<9;c++)print r,c,"HIGH"}')
        else
            local nhigh nmed
            nhigh=$(printf '%s\n' "$candidates" | grep -c "HIGH" 2>/dev/null || echo 0)
            nmed=$(printf '%s\n'  "$candidates" | grep -c "MED"  2>/dev/null || echo 0)
            _sh_log "  Prescan: $nhigh HIGH + $nmed MED"
        fi

        # ── Sweep — hover each candidate slot and read its tooltip ────────
        local found=0 sweep_start=$SECONDS

        while IFS=' ' read -r row col confidence; do
            [ -z "$row" ] && continue
            [ $found -eq 1 ] && break
            if [ $(( SECONDS - sweep_start )) -ge $SH_SWEEP_TIMEOUT ]; then
                _sh_log "  ✗ sweep timeout (${SH_SWEEP_TIMEOUT}s)"; break
            fi

            # Slot centre in screen coords (Python: sx=sl+col*sw+sw//2)
            local sx=$(( sl + col * slot_w + slot_w / 2 ))
            local sy=$(( st + row * slot_h + slot_h / 2 ))

            # Hover with spiral fallback (TECHNIQUE 2 Bézier + spiral positions)
            local hover_result
            hover_result=$(sh_hover_spiral "$sx" "$sy")
            local hover_status hover_mx hover_my
            read -r hover_status hover_mx hover_my <<< "$hover_result"

            if [ "$hover_status" != "found" ]; then
                _sh_log "  slot[$row,$col][$confidence]: no tooltip → skip"
                continue
            fi

            # 3-backend quorum tooltip read (TECHNIQUE 6 ImageMagick pixel scan)
            local verdict
            verdict=$(sh_read_tooltip "$SH_SNAP" "$hover_mx" "$hover_my")
            _sh_log "  slot[$row,$col][$confidence] @ (${hover_mx},${hover_my}): $verdict"

            case "$verdict" in
                confirm)
                    if sh_click_verify "$hover_mx" "$hover_my"; then
                        found=1
                        _sh_log "  ✓ SOLVED (row=$row col=$col)"
                        # Post-solve pause: 800-1400ms (Python range)
                        sleep "$(awk -v s="$RANDOM" \
                            'BEGIN{srand(s);printf"%.3f",0.8+rand()*0.6}')"
                    fi
                    ;;
                deny)
                    ;; # Skip — not the confirm slot
                empty)
                    # HIGH-confidence slot with no readable tooltip: one retry
                    if [ "$confidence" = "HIGH" ]; then
                        sleep 0.10
                        verdict=$(sh_read_tooltip "$SH_SNAP" "$hover_mx" "$hover_my")
                        if [ "$verdict" = "confirm" ]; then
                            sh_click_verify "$hover_mx" "$hover_my" && found=1 && \
                                _sh_log "  ✓ SOLVED (retry, row=$row col=$col)"
                        fi
                    fi
                    ;;
            esac
        done <<< "$candidates"

        if [ $found -eq 0 ]; then
            _sh_log "  ✗ confirm slot not found — watching 30s for manual solve"
            local wt2=$SECONDS
            while [ -f "$FLAG_FILE" ] && [ $(( SECONDS - wt2 )) -lt 30 ]; do
                sleep 0.5
                ! sh_popup_open && break   # sh_popup_open takes its own fresh snap
            done
        fi

        rm -f "$AFK_LOCK"
    done
    _sh_log "[ Shell Backup Solver ] stopped"
}

echo "[ MC Farm ] shell backup solver loaded (sh_popup_open/sh_find_strip/sh_prescan/sh_hover_spiral/sh_read_tooltip/sh_click_verify)"

# ── Spam loop ───────────────────────────────────────────────────
SH_SOLVER_PID=""        # PID of shell backup solver (empty = not launched)
START_TIME=$SECONDS
LAST_ROTATE=$SECONDS
LAST_VIBRATE=$SECONDS
NEXT_ROTATE_INTERVAL=$((30 + RANDOM % 45))
NEXT_VIBRATE_INTERVAL=$((15 + RANDOM % 25))
_LOCK_FIRST_SEEN=0      # epoch-$SECONDS when we first saw AFK_LOCK (stale-lock guard)
# Stale-lock threshold: max legitimate LOCK hold time is
#   sweep (~5.5s) + not-found watch loop (30s) = ~35.5s.
# We allow 40s before declaring the solver crashed and forcibly releasing.
_LOCK_STALE_S=40

while [ -f "$FLAG_FILE" ]; do

    # ── Pause while AFK solver is holding the lock ───────────────────────────
    # Stale-lock guard: if the solver crashes while holding AFK_LOCK the spam
    # loop would freeze indefinitely.  We track how long the lock has been held
    # and forcibly remove it after _LOCK_STALE_S seconds so farming resumes.
    if [ -f "$AFK_LOCK" ]; then
        if [ "$_LOCK_FIRST_SEEN" -eq 0 ]; then
            _LOCK_FIRST_SEEN=$SECONDS
        elif [ $(( SECONDS - _LOCK_FIRST_SEEN )) -gt $_LOCK_STALE_S ]; then
            echo "[ MC Farm ] ⚠ AFK_LOCK held >$_LOCK_STALE_S s — solver may have crashed; releasing lock"
            rm -f "$AFK_LOCK"
            _LOCK_FIRST_SEEN=0
            # Fall through — do NOT continue; resume spam clicks immediately
        else
            sleep 0.2; continue
        fi
    else
        _LOCK_FIRST_SEEN=0   # reset whenever lock is absent
    fi

    # ── Python solver liveness check (fixes Bug #3 from initial code review) ──
    # If the Python solver process has exited for any reason, launch the shell
    # backup solver automatically rather than continuing with no popup detection.
    # Uses kill -0 (signal 0): succeeds if the PID exists, fails if it doesn't.
    # _SH_SOLVER_LAUNCHED prevents relaunching after the shell backup is already
    # running; SH_SOLVER_PID lets the cleanup at the top of the file kill it.
    if ! kill -0 "$AFK_PID" 2>/dev/null; then
        if [ -z "$SH_SOLVER_PID" ] || ! kill -0 "$SH_SOLVER_PID" 2>/dev/null; then
            echo "[ MC Farm ] ⚠  Python solver (PID=$AFK_PID) died — launching shell backup solver"
            shell_afk_solver &
            SH_SOLVER_PID=$!
            echo "[ MC Farm ]    Shell backup PID=$SH_SOLVER_PID"
            echo "$SH_SOLVER_PID" >> "$PID_FILE"
        fi
    fi

    ELAPSED=$((SECONDS - START_TIME))
    FATIGUE_DELAY=$(( (ELAPSED / 60) * (RANDOM % 15) ))
    [ $FATIGUE_DELAY -gt 80 ] && FATIGUE_DELAY=80
    BEHAVIOR_ROLL=$((RANDOM % 100))

    # ── ACTION 1: camera rotation ────────────────────────────────────────────
    # TECHNIQUE 3 (polar arc sweep) + TECHNIQUE 1 (log-normal timing).
    # xdotool --polar maps (angle°, distance_px) directly onto X11 relative
    # motion, matching how a wrist physically pivots. Cosine easing + log-normal
    # per-step timing produces the same acceleration bell-curve as the old awk
    # loop but in fewer subprocesses and with no rectangular-grid artefacts.
    # Old rectangular cosine loop kept below as fallback (50/50 random choice).
    if [ $BEHAVIOR_ROLL -lt 3 ] && [ $((SECONDS - LAST_ROTATE)) -ge $NEXT_ROTATE_INTERVAL ]; then
        if [ $((RANDOM % 2)) -eq 0 ]; then
            # ── NEW: polar arc sweep (TECHNIQUE 3) ───────────────────────────
            polar_arc_sweep "$((50 + RANDOM % 80))"
        else
            # ── ORIGINAL: cosine-eased rectangular rotation (kept as variant) ─
            rot_x=$(( (RANDOM % 201) - 100 ))
            rot_y=$(( (RANDOM % 41) - 20 ))
            N=$((8 + RANDOM % 5))
            prev_t="0.000000"
            for ((s=1; s<=N; s++)); do
                read -r dx dy <<< "$(awk \
                    -v cx="$rot_x" -v cy="$rot_y" \
                    -v s="$s" -v n="$N" -v pt="$prev_t" \
                    -v rx1="$RANDOM" -v rx2="$RANDOM" \
                    -v ry1="$RANDOM" -v ry2="$RANDOM" '
                    BEGIN {
                        pi = 3.14159265358979
                        t  = 0.5 * (1 - cos(pi * s / n))
                        dt = t - pt
                        jx = (rx1/32767 - 0.5) + (rx2/32767 - 0.5)
                        jy = (ry1/32767 - 0.5) + (ry2/32767 - 0.5)
                        sx = int(cx*dt + jx + 0.5)
                        sy = int(cy*dt + jy + 0.5)
                        print sx, sy
                    }')"
                prev_t=$(awk -v s="$s" -v n="$N" \
                    'BEGIN{pi=3.14159265358979; printf "%.6f",0.5*(1-cos(pi*s/n))}')
                xdotool mousemove_relative -- $dx $dy 2>/dev/null
                # TECHNIQUE 1: log-normal step timing replaces static 7-15ms range
                sleep "$(_lognormal_ms -4.5 0.25)"
            done
        fi
        LAST_ROTATE=$SECONDS
        NEXT_ROTATE_INTERVAL=$((30 + RANDOM % 45))
        [ $((RANDOM % 10)) -eq 0 ] && START_TIME=$SECONDS
        # TECHNIQUE 1: log-normal post-rotate pause (~115ms mean)
        sleep "$(_lognormal_ms -2.16 0.38)"
        continue
    fi

    # ── ACTION 2: screen vibration ───────────────────────────────────────────
    if [ $BEHAVIOR_ROLL -ge 3 ] && [ $BEHAVIOR_ROLL -lt 8 ] && \
       [ $((SECONDS - LAST_VIBRATE)) -ge $NEXT_VIBRATE_INTERVAL ]; then
        vib_cycles=$((2 + RANDOM % 4))
        for ((i=0; i<vib_cycles; i++)); do
            shk_x=$(( (RANDOM % 7) - 3 ))
            shk_y=$(( (RANDOM % 7) - 3 ))
            xdotool mousemove_relative -- $shk_x $shk_y
            sleep 0.02
            xdotool mousemove_relative -- $(( -shk_x )) $(( -shk_y ))
        done
        LAST_VIBRATE=$SECONDS
        NEXT_VIBRATE_INTERVAL=$((15 + RANDOM % 25))
        continue
    fi

    # ── ACTION 3: standard attack ─────────────────────────────────────────────
    xdotool mousedown 1
    hold_ms=$((30 + RANDOM % 41))
    sleep $(awk -v ms="$hold_ms" 'BEGIN {print ms / 1000}')
    xdotool mouseup 1

    swing_ms=$((625 + RANDOM % 51 + FATIGUE_DELAY))
    if [ $((RANDOM % 10)) -eq 0 ]; then
        pause_ms=$((50 + RANDOM % 101))
        swing_ms=$((swing_ms + pause_ms))
    fi

    sleep_time=$(awk -v ms="$swing_ms" 'BEGIN {print ms / 1000}')
    sleep "$sleep_time"

done

kill "$AFK_PID" 2>/dev/null
rm -f "$AFK_LOCK"
echo "[ MC Farm ] all stopped"
