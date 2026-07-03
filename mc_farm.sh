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
HOVER_WAIT = 0.22   # wait after moving mouse (MC tooltip appears within 1 tick = 50ms; 220ms = ~4 ticks, safer)
REACT_MIN  = 0.20   # human-like pause before starting to solve (min)
REACT_MAX  = 0.65   # human-like pause before starting to solve (max)
SWEEP_TIMEOUT = 5.5 # abort sweep if it's been running this many seconds (server kicks ~7-10s)

# Measured from screenshots (all sessions, 1024×576 screen):
#   slot_w = 29 px  (column width  = popup_w / 9)
#   slot_h = 35 px  (row height    = spacing between row centers, CONSTANT)
# These are DIFFERENT — the AFK grid is NOT square in this server's GUI.
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
C_RED   = ((188,  38, 38), (255, 118, 118))
# MC popup title bar + "Inventory" label dark background ≈ rgb(55,55,55)
# These dark bands separate the AFK test strip from the player inventory.
C_DARK  = ((10,  10,  10), (110, 110, 110))

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
      • With HOVER_WAIT=0.22 s: 0.22 − 0.06 = 0.16 s window → 2-3 polls.
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
    title_bot = tt + max(6, (tb - tt) // 4)
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

# Tooltip background: MC renders it as very dark (near-black) ~rgb(16,0,16).
# Keep the range tight to avoid false-positives on other dark game areas.
C_TOOLTIP_BG = ((0,0,0),(42,10,42))

def has_tooltip(path=None):
    r=decode_png_cached()
    if r is None: return False
    rows,_,_,bpp=r
    return count_color(rows,bpp,C_TOOLTIP_BG[0],C_TOOLTIP_BG[1])>=15

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
      Position 0 (center): full HOVER_WAIT (0.15 s) — most items show here
      Positions 1-8 (ring 1): HOVER_WAIT * 0.5 (0.075 s) — short but fair
      Positions 9-16 (ring 2): HOVER_WAIT * 0.4 (0.060 s) — quick last resort

    Worst-case (no tooltip found at any of 17 positions):
      0.15 + 8×0.075 + 8×0.060 = 0.15 + 0.60 + 0.48 = 1.23 s per empty slot
    vs the old fixed 17×0.15 = 2.55 s.  This keeps sweep time well under
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
    N_ROWS = 3
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
        print("  Prescan: decode failed — hovering ALL slots")
        all_s = [(row,col) for row in range(N_ROWS) for col in range(9)]
        return all_s, [], None

    img_rows, iw, ih, bpp = r
    high, med = [], []

    for row in range(N_ROWS):
        for col in range(9):
            cx = col * slot_w + slot_w // 2
            cy = row * slot_h + slot_h // 2

            half = max(5, slot_w // 4)   # scale with GUI scale
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
        print("  Prescan found nothing — hovering ALL as safety fallback")
        all_s = [(row,col) for row in range(N_ROWS) for col in range(9)]
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
    """
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
#  MAIN LOOP
# ═══════════════════════════════════════════════════════════════
def main():
    mode = ("AI+" if HAS_AI else "") + "color+HSV" + ("+OCR" if HAS_TESS else "")
    print(f"[ AFK solver ] mode: {mode}")
    print(f"[ AFK solver ] session: {SESSION_ID}")
    print(f"[ AFK solver ] calibration data → {ASSETS_DIR}/")
    print( "[ AFK solver ] watching for Afk Grinding popup...\n")

    _ensure_assets()
    _load_weights()
    _load_templates()
    # Bootstrap from historic attempts only when no templates exist yet —
    # avoids re-ingesting old data every run, which would dampen recent
    # online learning accumulated during previous sessions.
    if _slot_templates.get('confirm') is None and _slot_templates.get('deny') is None:
        _bootstrap_templates_from_assets()
    idle=0
    while os.path.exists(FLAG):
        try:
            if not popup_open():
                idle+=1
                if idle%40==0: print("[ AFK solver ] still watching...")
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
                continue

            sl,st,sr,sb,slot_w,slot_h=strip
            print(f"[ AFK solver ] strip → left={sl} top={st} right={sr} bot={sb}  "
                  f"slot_w={slot_w}px  slot_h={slot_h}px")

            solved = sweep_strip(strip)

            # Belt-and-suspenders LOCK cleanup
            try: os.remove(LOCK)
            except: pass

            if solved:
                print("[ AFK solver ] ✓ solved! Back to watching...\n")
            else:
                time.sleep(POLL)

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

# ── Spam loop ───────────────────────────────────────────────────
START_TIME=$SECONDS
LAST_ROTATE=$SECONDS
LAST_VIBRATE=$SECONDS
NEXT_ROTATE_INTERVAL=$((30 + RANDOM % 45))
NEXT_VIBRATE_INTERVAL=$((15 + RANDOM % 25))

while [ -f "$FLAG_FILE" ]; do

    # Pause while AFK solver is in the middle of clicking (or watching for user)
    if [ -f "$AFK_LOCK" ]; then sleep 0.2; continue; fi

    ELAPSED=$((SECONDS - START_TIME))
    FATIGUE_DELAY=$(( (ELAPSED / 60) * (RANDOM % 15) ))
    [ $FATIGUE_DELAY -gt 80 ] && FATIGUE_DELAY=80
    BEHAVIOR_ROLL=$((RANDOM % 100))

    # ── ACTION 1: cosine-eased camera rotation ───────────────────────────────
    # Splits total displacement across 8-12 steps using ease-in/ease-out so the
    # camera accelerates and decelerates naturally.  Per-step ±1px Gaussian-like
    # jitter (sum of two uniforms) prevents the movement looking like a slideshow.
    if [ $BEHAVIOR_ROLL -lt 3 ] && [ $((SECONDS - LAST_ROTATE)) -ge $NEXT_ROTATE_INTERVAL ]; then
        rot_x=$(( (RANDOM % 201) - 100 ))
        rot_y=$(( (RANDOM % 41) - 20 ))
        N=$((8 + RANDOM % 5))        # 8-12 steps
        prev_t="0.000000"
        for ((s=1; s<=N; s++)); do
            # Ease-in/ease-out delta and ±1px Gaussian-like jitter per axis
            read dx dy <<< $(awk \
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
                }')
            # Advance prev_t to current t for next iteration
            prev_t=$(awk -v s="$s" -v n="$N" '
                BEGIN { pi=3.14159265358979; printf "%.6f", 0.5*(1-cos(pi*s/n)) }')
            xdotool mousemove_relative -- $dx $dy 2>/dev/null
            sleep $(awk -v ms="$((7 + RANDOM % 9))" 'BEGIN {print ms/1000}')   # 7-15 ms
        done
        LAST_ROTATE=$SECONDS
        NEXT_ROTATE_INTERVAL=$((30 + RANDOM % 45))
        [ $((RANDOM % 10)) -eq 0 ] && START_TIME=$SECONDS
        sleep $(awk -v ms="$((90 + RANDOM % 160))" 'BEGIN {print ms/1000}')
        continue
    fi

    # ── ACTION 2: micro-vibration with Gaussian-ish shake and natural drift ──
    # Shake magnitude drawn from sum of two uniforms → bell-shaped distribution.
    # Return is NOT a perfect mirror: a ±1px residual is left to mimic real
    # hand tremor that never returns exactly to the starting pixel.
    if [ $BEHAVIOR_ROLL -ge 3 ] && [ $BEHAVIOR_ROLL -lt 8 ] && \
       [ $((SECONDS - LAST_VIBRATE)) -ge $NEXT_VIBRATE_INTERVAL ]; then
        VIBS=$((2 + RANDOM % 4))
        for ((i=0; i<VIBS; i++)); do
            # Gaussian-ish ±4 via sum of two uniform ±2 draws
            shk_x=$(( (RANDOM%5-2) + (RANDOM%5-2) ))
            shk_y=$(( (RANDOM%5-2) + (RANDOM%5-2) ))
            xdotool mousemove_relative -- $shk_x $shk_y 2>/dev/null
            sleep $(awk -v ms="$((16 + RANDOM % 12))" 'BEGIN {print ms/1000}')  # 16-27 ms
            # Return with 1px random residual (don't land exactly back)
            ret_x=$(( -shk_x + (RANDOM % 3) - 1 ))
            ret_y=$(( -shk_y + (RANDOM % 3) - 1 ))
            xdotool mousemove_relative -- $ret_x $ret_y 2>/dev/null
            sleep $(awk -v ms="$((10 + RANDOM % 10))" 'BEGIN {print ms/1000}')
        done
        LAST_VIBRATE=$SECONDS
        NEXT_VIBRATE_INTERVAL=$((15 + RANDOM % 25))
        continue
    fi

    # ── ACTION 3: attack click ────────────────────────────────────────────────
    # Hold duration: 30-70 ms normally.
    # 8% chance of a fumbled "short tap" (12-25 ms).
    # 4% chance of an "overhold" (120-200 ms) — accidentally held too long.
    if [ $((RANDOM % 100)) -lt 8 ]; then
        hold_ms=$((12 + RANDOM % 14))                 # short tap
    elif [ $((RANDOM % 100)) -lt 4 ]; then
        hold_ms=$((120 + RANDOM % 81))                # overhold
    else
        hold_ms=$((30 + RANDOM % 41))                 # normal 30-70 ms
    fi
    xdotool mousedown 1 2>/dev/null
    sleep $(awk -v ms="$hold_ms" 'BEGIN {print ms/1000}')
    xdotool mouseup 1 2>/dev/null

    swing_ms=$((625 + RANDOM % 51 + FATIGUE_DELAY))
    [ $((RANDOM % 10)) -eq 0 ] && swing_ms=$((swing_ms + 50 + RANDOM % 101))
    sleep $(awk -v ms="$swing_ms" 'BEGIN {print ms/1000}')

done

kill "$AFK_PID" 2>/dev/null
rm -f "$AFK_LOCK"
echo "[ MC Farm ] all stopped"
