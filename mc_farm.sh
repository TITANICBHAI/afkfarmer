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

if [ -f "$FLAG_FILE" ]; then
    rm -f "$FLAG_FILE"
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
import uuid, pathlib, shutil, select

FLAG = "/tmp/mc_spamming"
LOCK = "/tmp/mc_afk_solving"
SNAP = "/tmp/_mc_afk.png"

# ── Calibration output directory (set by mc_farm.sh via env var) ──────
ASSETS_DIR  = os.environ.get('MC_ASSETS_DIR', '/tmp/mc_afk_assets')
SESSION_ID  = str(uuid.uuid4())[:8]
# Optional — the in-game username of whoever runs this script, used purely to
# make joining against afkverify_events.jsonl unambiguous on shared servers.
PLAYER_NAME = os.environ.get('MC_PLAYER_NAME', '').strip()
_attempt_no = 0          # incremented each time a popup is found

# ── Timing ────────────────────────────────────────────────────
POLL       = 0.35   # seconds between full-screen popup checks
HOVER_WAIT = 0.10   # wait after moving mouse (MC tooltip appears within 1 tick = 50ms; 100ms plenty)
REACT_MIN  = 0.20   # human-like pause before starting to solve (min)
REACT_MAX  = 0.65   # human-like pause before starting to solve (max)
SWEEP_TIMEOUT = 5.5 # abort sweep if it's been running this many seconds (server kicks ~7-10s)

# Measured from screenshots (all sessions, 1024×576 screen):
#   slot_w = 29 px  (column width  = popup_w / 9)
#   slot_h = 35 px  (row height    = spacing between row centers, CONSTANT)
# These are DIFFERENT — the AFK grid is NOT square in this server's GUI.
SLOT_H_DEFAULT = 35

# ── Hover spiral: positions to try (dx,dy) if center hover yields no tooltip
# Starts at center (0,0), then expands outward in a cross/diamond pattern.
# Each position is tried in order until a tooltip appears.
HOVER_SPIRAL = [(0,0),(0,-5),(5,0),(0,5),(-5,0),(-4,-4),(4,-4),(4,4),(-4,4)]

# ── Prescan confidence thresholds (colorful pixel count in 14×14 box) ────────
PRESCAN_HIGH = 15   # ≥15 bright pixels → item definitely present
PRESCAN_MED  = 5    # 5-14 → possible item (hover but accept "empty" result)
# < 5 → skip entirely (empty slot)

# ── Tooltip backend weights for voting ───────────────────────────────────────
# Color scan is fast and very reliable for these two distinct MC colors.
# AI is most accurate but slow. OCR is a middle ground.
WEIGHT_COLOR = 2
WEIGHT_AI    = 3
WEIGHT_OCR   = 1
VOTE_THRESHOLD = 2   # minimum weighted votes needed to act

# ── Minecraft UI colors ───────────────────────────────────────
# Inventory background gray  ≈ rgb(198,198,198)
C_GRAY  = ((180,180,180), (222,222,222))
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
API_KEY      = os.environ.get('ANTHROPIC_API_KEY','').strip()
HAS_AI       = bool(API_KEY)

# ── Side-channel data filled during each sweep ────────────────
# read_tooltip_voted() fills this so sweep_strip can record per-backend votes
_last_vote_detail = {'color': None, 'ai': None, 'ocr': None, 'scores': {}}
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
    if x is not None:
        subprocess.run(['scrot','-z','-a',f'{x},{y},{w},{h}',SNAP],capture_output=True)
    else:
        subprocess.run(['scrot','-z',SNAP],capture_output=True)

def screen_wh():
    o=subprocess.run(['xdotool','getdisplaygeometry'],capture_output=True,text=True).stdout.strip()
    a,b=o.split(); return int(a),int(b)

def xdo(*a):
    subprocess.run(['xdotool']+list(a),capture_output=True)

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

    # ── 1. Capture 90% of screen so popup is found wherever it sits ──
    ox=int(sw*0.05); oy=int(sh*0.05)
    cw=sw-2*ox;      ch=sh-2*oy
    scrot(ox,oy,cw,ch)
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
                if rlen>bw and rlen>=100:
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
                    if bot-top>=70:
                        # Store in screen coords
                        pl=ox+rs; pt=oy+top; pr=ox+rs+rlen; pb=oy+bot
                        best=(pl,pt,pr,pb)
                rs=None; rlen=0

    if best is None: return None
    pl,pt,pr,pb = best
    pw=pr-pl; ph=pb-pt

    # Sanity: popup should be 120-450px wide and at least 80px tall
    if pw<120 or ph<80: return None

    # ── Slot dimensions (measured from screenshots) ───────────────────
    slot_w = max(10, min(round(pw / 9), 60))
    SLOT_H = SLOT_H_DEFAULT   # 35 px — constant regardless of popup size

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

def ask_color(path):
    r=decode_png(path)
    if r is None: return "empty"
    rows,_,_,bpp=r
    g=count_color(rows,bpp,C_GREEN[0],C_GREEN[1])
    rd=count_color(rows,bpp,C_RED[0],C_RED[1])
    if g>=10 and g>rd:  return "confirm"
    if rd>=10 and rd>g: return "deny"
    return "empty"

# Tooltip background: MC renders it as very dark purple ~rgb(16,0,16)
C_TOOLTIP_BG = ((0,0,0),(42,10,42))

def has_tooltip(path):
    r=decode_png(path)
    if r is None: return False
    rows,_,_,bpp=r
    return count_color(rows,bpp,C_TOOLTIP_BG[0],C_TOOLTIP_BG[1])>=15

def _snap_tooltip(mx, my, sw, sh):
    tx=max(0,mx-100); ty=max(0,my-110)
    tw=min(sw-tx,460); th=min(sh-ty,145)
    scrot(tx,ty,tw,th)
    return tx, ty

# ═══════════════════════════════════════════════════════════════
#  STRATEGY 1: HOVER WITH SPIRAL FALLBACK
# ═══════════════════════════════════════════════════════════════
def hover_spiral(sx, sy, sw, sh):
    for dx, dy in HOVER_SPIRAL:
        nx = max(0, min(sw-1, sx+dx))
        ny = max(0, min(sh-1, sy+dy))
        xdo('mousemove', str(nx), str(ny))
        time.sleep(HOVER_WAIT + random.uniform(0, 0.03))
        _snap_tooltip(nx, ny, sw, sh)
        if has_tooltip(SNAP):
            return True, nx, ny
    return False, sx, sy

# ═══════════════════════════════════════════════════════════════
#  STRATEGY 2: MULTI-BACKEND VOTED TOOLTIP READING
#  color(wt=2) → AI(wt=3) → OCR(wt=1)
#  Stores per-backend results in _last_vote_detail for logging.
# ═══════════════════════════════════════════════════════════════
def read_tooltip_voted():
    global _last_vote_detail
    _last_vote_detail = {'color': None, 'ai': None, 'ocr': None, 'scores': {}}
    scores = {"confirm": 0, "deny": 0}

    # ── Backend 1: color pixel scan ──────────────────────────────────────
    a = ask_color(SNAP)
    _last_vote_detail['color'] = a
    if a in scores:
        scores[a] += WEIGHT_COLOR
        if scores[a] >= VOTE_THRESHOLD:
            _last_vote_detail['scores'] = dict(scores)
            print(f"    [color✓{scores[a]}] → {a}"); return a

    # ── Backend 2: AI vision ─────────────────────────────────────────────
    if HAS_AI:
        a = ask_ai(SNAP)
        _last_vote_detail['ai'] = a
        if a in scores:
            scores[a] += WEIGHT_AI
            winner = max(scores, key=scores.get)
            _last_vote_detail['scores'] = dict(scores)
            if scores[winner] >= VOTE_THRESHOLD:
                print(f"    [AI✓{scores[winner]}] → {winner}"); return winner

    # ── Backend 3: OCR ───────────────────────────────────────────────────
    if HAS_TESS:
        a = ask_ocr(SNAP)
        _last_vote_detail['ocr'] = a
        if a in scores:
            scores[a] += WEIGHT_OCR

    _last_vote_detail['scores'] = dict(scores)

    if any(v > 0 for v in scores.values()):
        winner = max(scores, key=scores.get)
        print(f"    [voted {scores}] → {winner}"); return winner

    print("    [no vote] → deny"); return "deny"

# ═══════════════════════════════════════════════════════════════
#  STRATEGY 3: DOUBLE-CHECK CONFIRM BEFORE CLICKING
# ═══════════════════════════════════════════════════════════════
def double_check_confirm(mx, my, sw, sh):
    time.sleep(0.06)
    xdo('mousemove', str(mx), str(my))
    time.sleep(0.06)
    _snap_tooltip(mx, my, sw, sh)
    if not has_tooltip(SNAP):
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
    global _last_click_stats
    t0 = time.time()
    open(LOCK,'w').close()
    for attempt in range(1, 4):
        xdo('mousemove', str(mx), str(my))
        time.sleep(random.uniform(0.03, 0.08))
        xdo('mousedown','1')
        time.sleep(random.uniform(0.06, 0.12))
        xdo('mouseup','1')
        time.sleep(0.45)
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
        time.sleep(0.25)
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
    sw,sh=screen_wh()
    ox=sw//2-200; oy=sh//2-150
    scrot(ox, oy, 400, 300)
    r=decode_png(SNAP)
    if r is None: return False
    rows,_,_,bpp=r
    return count_color(rows,bpp,C_GRAY[0],C_GRAY[1])>1200

# ═══════════════════════════════════════════════════════════════
#  PRESCAN — find which slots have items WITHOUT hovering
# ═══════════════════════════════════════════════════════════════
def prescan_strip(strip):
    sl, st, sr, sb, slot_w, slot_h = strip
    N_ROWS = 3
    sw, sh = screen_wh()

    px = max(0, sl);  py = max(0, st)
    pw = min(sw - px, sr - sl + 4)
    ph = min(sh - py, sb - st + 4)
    scrot(px, py, pw, ph)

    r = decode_png(SNAP)
    if r is None:
        print("  Prescan: decode failed — hovering ALL slots")
        all_s = [(row,col) for row in range(N_ROWS) for col in range(9)]
        return all_s, []

    img_rows, iw, ih, bpp = r
    high, med = [], []

    for row in range(N_ROWS):
        for col in range(9):
            cx = col * slot_w + slot_w // 2
            cy = row * slot_h + slot_h // 2

            half = 7
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
        return all_s, []

    return high, med

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
    sw, sh = screen_wh()
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
    high_slots, med_slots = prescan_strip(strip)
    record['prescan'] = {'high': high_slots, 'med': med_slots}
    total_items = len(high_slots) + len(med_slots)
    print(f"  sweep_strip: {len(high_slots)} HIGH + {len(med_slots)} MED  "
          f"[slot_w={slot_w} slot_h={slot_h}]")

    solved_info = [None]   # set by process_slot when click succeeds

    def process_slot(row, col, is_high):
        elapsed = time.time() - sweep_start
        if elapsed > SWEEP_TIMEOUT:
            print(f"  ⚠ timeout at {elapsed:.1f}s — aborting sweep")
            return "timeout"

        sx = sl + col * slot_w + slot_w // 2
        sy = st + row * slot_h + slot_h // 2
        label = f"row{row+1}c{col+1}[{'H' if is_high else 'M'}]"

        slot_info = {
            'row': row, 'col': col, 'screen_x': sx, 'screen_y': sy,
            'confidence' : 'HIGH' if is_high else 'MED',
            'color_vote' : None, 'ai_vote': None, 'ocr_vote': None,
            'vote_scores': {},
            'final_answer': None,
            'double_checked': False,
            'clicked'       : False,
            'click_success' : None,
        }

        # ── S2: Hover + spiral fallback ──────────────────────────────────
        found, mx, my = hover_spiral(sx, sy, sw, sh)
        if not found:
            slot_info['final_answer'] = 'no_tooltip'
            record['slots_inspected'].append(slot_info)
            print(f"  {label}: no tooltip after spiral → skip")
            return False

        # ── S3: Voted tooltip reading ────────────────────────────────────
        answer = read_tooltip_voted()
        slot_info['color_vote']   = _last_vote_detail.get('color')
        slot_info['ai_vote']      = _last_vote_detail.get('ai')
        slot_info['ocr_vote']     = _last_vote_detail.get('ocr')
        slot_info['vote_scores']  = _last_vote_detail.get('scores', {})
        slot_info['final_answer'] = answer
        print(f"  {label}: {answer}")

        if answer == "confirm":
            if is_high:
                # ── S4: Double-check before clicking (HIGH slots only) ───
                ok = double_check_confirm(mx, my, sw, sh)
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
                time.sleep(random.uniform(0.8, 1.4))
                return True
            else:
                print(f"  {label}: click failed — continuing scan")
                return False

        elif answer == "deny":
            record['slots_inspected'].append(slot_info)
            return False

        record['slots_inspected'].append(slot_info)
        return False

    # ── Process HIGH first, then MED; break on first success ────────────
    final_result = False
    for slots, is_high in [(high_slots, True), (med_slots, False)]:
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
    mode = ("AI+color" if HAS_AI else "") + ("+OCR" if HAS_TESS else "") or "color-only"
    print(f"[ AFK solver ] mode: {mode}")
    print(f"[ AFK solver ] session: {SESSION_ID}")
    print(f"[ AFK solver ] calibration data → {ASSETS_DIR}/")
    print( "[ AFK solver ] watching for Afk Grinding popup...\n")

    _ensure_assets()
    idle=0
    while os.path.exists(FLAG):
        try:
            if not popup_open():
                idle+=1
                if idle%40==0: print("[ AFK solver ] still watching...")
                time.sleep(POLL)
                continue

            strip = find_afk_strip()
            if strip is None:
                time.sleep(POLL)
                continue

            sl,st,sr,sb,slot_w,slot_h=strip
            print(f"[ AFK solver ] Afk strip found — width={sr-sl}px  "
                  f"slot_w={slot_w}px  slot_h={slot_h}px")

            time.sleep(random.uniform(REACT_MIN, REACT_MAX))

            idle=0
            solved = sweep_strip(strip)

            if solved:
                print("[ AFK solver ] ✓ solved! Back to watching...\n")
            else:
                time.sleep(POLL)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[ AFK solver ] error: {e}")
            time.sleep(1)

    print("[ AFK solver ] stopped")

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

    # ACTION 1: smooth camera rotation
    if [ $BEHAVIOR_ROLL -lt 3 ] && [ $((SECONDS - LAST_ROTATE)) -ge $NEXT_ROTATE_INTERVAL ]; then
        rot_x=$(( (RANDOM % 201) - 100 ))
        rot_y=$(( (RANDOM % 41) - 20 ))
        for ((s=0; s<5; s++)); do
            xdotool mousemove_relative -- $((rot_x/5)) $((rot_y/5)); sleep 0.01
        done
        LAST_ROTATE=$SECONDS
        NEXT_ROTATE_INTERVAL=$((30 + RANDOM % 45))
        [ $((RANDOM % 10)) -eq 0 ] && START_TIME=$SECONDS
        sleep $(awk -v ms="$((100 + RANDOM % 150))" 'BEGIN {print ms/1000}')
        continue
    fi

    # ACTION 2: micro-vibration
    if [ $BEHAVIOR_ROLL -ge 3 ] && [ $BEHAVIOR_ROLL -lt 8 ] && \
       [ $((SECONDS - LAST_VIBRATE)) -ge $NEXT_VIBRATE_INTERVAL ]; then
        for ((i=0; i<$((2 + RANDOM % 4)); i++)); do
            shk_x=$(( (RANDOM%7)-3 )); shk_y=$(( (RANDOM%7)-3 ))
            xdotool mousemove_relative -- $shk_x $shk_y; sleep 0.02
            xdotool mousemove_relative -- $((-shk_x)) $((-shk_y))
        done
        LAST_VIBRATE=$SECONDS
        NEXT_VIBRATE_INTERVAL=$((15 + RANDOM % 25))
        continue
    fi

    # ACTION 3: standard attack click
    xdotool mousedown 1
    sleep $(awk -v ms="$((30 + RANDOM % 41))" 'BEGIN {print ms/1000}')
    xdotool mouseup 1

    swing_ms=$((625 + RANDOM % 51 + FATIGUE_DELAY))
    [ $((RANDOM % 10)) -eq 0 ] && swing_ms=$((swing_ms + 50 + RANDOM % 101))
    sleep $(awk -v ms="$swing_ms" 'BEGIN {print ms/1000}')

done

kill "$AFK_PID" 2>/dev/null
rm -f "$AFK_LOCK"
echo "[ MC Farm ] all stopped"
