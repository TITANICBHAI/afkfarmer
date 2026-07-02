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

stdlib only — no pip, no install.
"""
import os, sys, zlib, struct, subprocess, time, random, base64, json

FLAG = "/tmp/mc_spamming"
LOCK = "/tmp/mc_afk_solving"
SNAP = "/tmp/_mc_afk.png"

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
HAS_TESS = subprocess.run(['which','tesseract'],capture_output=True).returncode==0
API_KEY  = os.environ.get('ANTHROPIC_API_KEY','').strip()
HAS_AI   = bool(API_KEY)

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
    #
    #  slot_w: column width = popup_w / 9  (round, not floor)
    #          pw=260 → slot_w=29.  The old int(pw*0.90/9)=26 put col 0
    #          13 px too far right and missed col 8 entirely.
    #
    #  SLOT_H: row height is CONSTANT = 35 px (spacing between row centres
    #          measured across all screenshot sessions).  The grid is NOT
    #          square — using one value for both axes caused a 16 px vertical
    #          error on row 2, making those items completely unhittable.
    slot_w = max(10, min(round(pw / 9), 60))
    SLOT_H = SLOT_H_DEFAULT   # 35 px — constant regardless of popup size

    # ── 4. Row-by-row gray-fraction scan to locate the two dark bands ─
    #
    #   gray_frac[i] = fraction of popup-width pixels that are C_GRAY
    #   in the row at screen-y = pt+i.
    #
    #   Thresholds (tuned from screenshots):
    #     DARK_T = 0.18  — row is a "dark band" (title / Inventory label)
    #     GRAY_T = 0.40  — row is a "slot row"
    #
    #   State machine:
    #     START → TITLE_DARK → AFK_SLOTS → INV_SEP (stop here)
    #
    DARK_T = 0.18
    GRAY_T = 0.40

    # Convert popup screen coords → image coords
    pt_i = pt-oy; pb_i = pb-oy
    pl_i = pl-ox; pr_i = pr-ox

    state = 'start'
    title_end_rel = None   # row index (relative to pt) where title ends
    inv_sep_rel   = None   # row index (relative to pt) where Inventory sep starts

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
                break   # found the "Inventory" separator — stop scanning

    # ── 5a. Secondary separator: scan for "Inventory" white text rows ───
    #
    #  The "Inventory" label uses MC pure-white text (R≥250,G≥250,B≥250).
    #  Items in the AFK grid are coloured — no pure-white pixels.
    #  Scan rows at 42-52% of popup height.  Measured across all sessions:
    #  "Inventory" text appears at exactly 46.1% of popup height.
    #
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
                break   # first row with heavy white text = "Inventory" label

    # ── 5b. Build final strip coordinates ─────────────────────────────
    #
    #  strip_top: measured title bar = 18 px (constant across all sessions)
    if title_end_rel is not None:
        strip_top = pt + title_end_rel
    else:
        strip_top = pt + 18   # fixed 18 px — measured MC title bar height

    #  strip_bot: must include all 3 AFK rows.
    #  Row 2 bottom = strip_top + 3*SLOT_H = strip_top + 105.
    #  "Inventory" text at ~46% is at y≈249, row 2 bottom at y≈253 — very
    #  close, so we always enforce the minimum to avoid cutting row 2 off.
    MIN_STRIP_H = 3 * SLOT_H   # always show 3 AFK rows (= 105 px)

    if inv_sep_rel is not None:
        raw_bot = pt + inv_sep_rel
        strip_bot = max(raw_bot, strip_top + MIN_STRIP_H)
        print(f"  Popup {pw}×{ph}px  slot_w={slot_w}px  SLOT_H={SLOT_H}px  "
              f"title_end={title_end_rel}px  inv_sep={inv_sep_rel}px  [exact]")
    else:
        # 49% fallback: 130+126=256 > strip_top+105=253 → 3 rows always included
        raw_bot = pt + int(ph * 0.49)
        strip_bot = max(raw_bot, strip_top + MIN_STRIP_H)
        print(f"  Popup {pw}×{ph}px  slot_w={slot_w}px  SLOT_H={SLOT_H}px  "
              f"title_end={title_end_rel}px  sep=not found [49% fallback]")

    if strip_bot <= strip_top: return None

    # Center 9 columns horizontally within the popup using slot_w
    strip_left  = pl + (pw - 9*slot_w) // 2
    strip_right = strip_left + 9*slot_w

    # Return 6-element tuple — callers must unpack as (sl,st,sr,sb,slot_w,slot_h)
    return (strip_left, strip_top, strip_right, strip_bot, slot_w, SLOT_H)

# ═══════════════════════════════════════════════════════════════
#  TOOLTIP READING — AI → OCR → color (best to worst)
# ═══════════════════════════════════════════════════════════════

# Step-by-step prompt: tells Claude to reason the same way a human would
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
    """
    Fast pre-check before AI/OCR: did any tooltip appear at all?
    MC tooltip boxes have a very distinctive near-black-purple background.
    Empty slots produce NO tooltip so we skip expensive calls entirely.
    Needs ≥15 matching pixels to avoid false positives from dark game BG.
    """
    r=decode_png(path)
    if r is None: return False
    rows,_,_,bpp=r
    return count_color(rows,bpp,C_TOOLTIP_BG[0],C_TOOLTIP_BG[1])>=15

def _snap_tooltip(mx, my, sw, sh):
    """Capture the tooltip region around (mx,my) into SNAP. Returns (tx,ty)."""
    tx=max(0,mx-100); ty=max(0,my-110)
    tw=min(sw-tx,460); th=min(sh-ty,145)
    scrot(tx,ty,tw,th)
    return tx, ty

# ═══════════════════════════════════════════════════════════════
#  STRATEGY 1: HOVER WITH SPIRAL FALLBACK
#  If the slot-centre hover produces no tooltip (e.g. border mis-hit)
#  we try 8 positions spiralling outward before giving up.
# ═══════════════════════════════════════════════════════════════
def hover_spiral(sx, sy, sw, sh):
    """
    Move mouse to (sx,sy) and surrounding spiral positions until a tooltip
    appears.  Returns (found: bool, final_mx, final_my).

    Spiral positions (dx,dy): centre first, then cross, then diagonals.
    All positions are clamped to screen bounds.
    """
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
#  Acts when any backend reaches VOTE_THRESHOLD weighted votes.
#  Prevents single-backend false positives from immediately clicking.
# ═══════════════════════════════════════════════════════════════
def read_tooltip_voted():
    """
    Read the current SNAP (tooltip screenshot) using up to 3 backends.

    Backends are run cheapest-first.  Each backend casts weighted votes for
    "confirm" or "deny".  We act as soon as the leading answer accumulates
    VOTE_THRESHOLD points — that way a very confident color scan (weight 2)
    returns immediately, but if color is ambiguous we bring in AI/OCR.

    False-positive protection: color alone needs ≥ 2 pts (VOTE_THRESHOLD).
    If somehow both confirm AND deny each get 1 pt from different backends,
    AI (weight 3) is the tiebreaker and will dominate.
    """
    scores = {"confirm": 0, "deny": 0}

    # ── Backend 1: color pixel scan — fast, runs always ─────────────────
    a = ask_color(SNAP)
    if a in scores:
        scores[a] += WEIGHT_COLOR
        if scores[a] >= VOTE_THRESHOLD:
            print(f"    [color✓{scores[a]}] → {a}"); return a

    # ── Backend 2: AI vision — accurate, slow, network call ─────────────
    if HAS_AI:
        a = ask_ai(SNAP)
        if a in scores:
            scores[a] += WEIGHT_AI
            winner = max(scores, key=scores.get)
            if scores[winner] >= VOTE_THRESHOLD:
                print(f"    [AI✓{scores[winner]}] → {winner}"); return winner

    # ── Backend 3: OCR — medium accuracy, local ──────────────────────────
    if HAS_TESS:
        a = ask_ocr(SNAP)
        if a in scores:
            scores[a] += WEIGHT_OCR

    # ── Return best-voted answer (or deny if nothing conclusive) ─────────
    if any(v > 0 for v in scores.values()):
        winner = max(scores, key=scores.get)
        print(f"    [voted {scores}] → {winner}"); return winner

    print("    [no vote] → deny"); return "deny"

# ═══════════════════════════════════════════════════════════════
#  STRATEGY 3: DOUBLE-CHECK CONFIRM BEFORE CLICKING
#  Re-hover same position, re-read tooltip.  Both reads must agree
#  on "confirm" before we commit to a click.
# ═══════════════════════════════════════════════════════════════
def double_check_confirm(mx, my, sw, sh):
    """
    After the first 'confirm' read, wait 60 ms, re-hover the same pixel,
    and re-read.  Returns True only if both reads say 'confirm'.

    Prevents acting on tooltip flicker or transient false green pixels.
    """
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
#  After each click we check whether the popup actually closed.
#  If it didn't, we retry (up to 3 times total).
# ═══════════════════════════════════════════════════════════════
def click_and_verify(mx, my):
    """
    Click (mx,my) up to 3 times, verifying popup closure after each attempt.
    Acquires AFK_LOCK so the spam loop pauses during the click.
    Returns True if popup closed, False if all retries failed.

    False-positive protection: if the popup stays open after a click, it
    means we either mis-clicked or clicked the wrong item — we try again.
    """
    open(LOCK,'w').close()
    for attempt in range(1, 4):
        xdo('mousemove', str(mx), str(my))
        time.sleep(random.uniform(0.03, 0.08))
        xdo('mousedown','1')
        time.sleep(random.uniform(0.06, 0.12))
        xdo('mouseup','1')
        time.sleep(0.45)   # brief settle before checking
        if not popup_open():
            print(f"    [click] popup closed after attempt {attempt} ✓")
            try: os.remove(LOCK)
            except: pass
            return True
        print(f"    [click] popup still open after attempt {attempt}, retrying…")
        time.sleep(0.25)
    try: os.remove(LOCK)
    except: pass
    print("    [click] all retries failed — popup refused to close")
    return False

# ═══════════════════════════════════════════════════════════════
#  QUICK POPUP PRESENCE CHECK  (gray-mass scan, no full analysis)
# ═══════════════════════════════════════════════════════════════
def popup_open():
    """
    FAST poll check — called every 0.35 s so must decode a SMALL image.

    JartexNetwork always centers the Afk Grinding popup near the screen
    center.  A 400 × 300 crop around center catches it reliably without
    the pure-Python PNG decode cost of a full-screen capture (~0.07 s
    vs ~1 s for full 1024×576).  The larger full-screen decode is only
    done once inside find_afk_strip() when a popup is confirmed.
    """
    sw,sh=screen_wh()
    # 400 × 300 window centered on screen — covers full popup with margin
    ox=sw//2-200; oy=sh//2-150
    scrot(ox, oy, 400, 300)
    r=decode_png(SNAP)
    if r is None: return False
    rows,_,_,bpp=r
    return count_color(rows,bpp,C_GRAY[0],C_GRAY[1])>1200

# ═══════════════════════════════════════════════════════════════
#  PRESCAN — find which slots have items WITHOUT hovering
# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
#  STRATEGY 5: TWO-TIER PRESCAN WITH CONFIDENCE LEVELS
#  HIGH  (≥ PRESCAN_HIGH colorful pixels): item definitely present
#  MED   (PRESCAN_MED … PRESCAN_HIGH-1): possible item, hover anyway
#  EMPTY (< PRESCAN_MED): skip entirely
#
#  Using a 14×14 sample box (up from 12×12) catches more item texture
#  pixels even when the hover offset is slightly off-centre.
#  n_rows is hardcoded to 3 — JartexNetwork always sends 3 AFK rows.
# ═══════════════════════════════════════════════════════════════
def prescan_strip(strip):
    """
    Returns (high_slots, med_slots) — two lists of (row,col) tuples.

    high_slots: very likely have items (process first)
    med_slots : might have items (process after, accept 'empty' silently)

    Falls back to (all_slots, []) if the decode fails.
    """
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

            # 14×14 sample box (±7 px) — larger than before for better coverage
            half = 7
            x0 = max(0, cx - half);  x1 = min(iw, cx + half)
            y0 = max(0, cy - half);  y1 = min(ih, cy + half)

            colorful = 0
            for iy in range(y0, y1):
                rd = img_rows[iy]
                for ix in range(x0, x1):
                    rr=rd[ix*bpp]; gg=rd[ix*bpp+1]; bb=rd[ix*bpp+2]
                    if max(rr,gg,bb) - min(rr,gg,bb) > 28:   # slightly lower threshold
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


# ═══════════════════════════════════════════════════════════════
#  SWEEP — brings all 5 strategies together
# ═══════════════════════════════════════════════════════════════
def sweep_strip(strip):
    """
    Full multi-strategy AFK sweep:

    S1 — Two-tier prescan  : HIGH slots first, MED slots after.
    S2 — Spiral hover      : if center hover misses, try 8 surrounding points.
    S3 — Voted tooltip read: color(2pt) → AI(3pt) → OCR(1pt), need ≥2 pts.
    S4 — Double-check      : re-hover + re-read before committing to a click.
    S5 — Click-verify-retry: after click, check popup closed; retry up to 3×.

    MED slots are processed last and never double-checked (they may be empty;
    a single 'confirm' from voted read is enough to click, but a single 'deny'
    or 'empty' is silently skipped without logging a failure).

    Sweep aborts at SWEEP_TIMEOUT to avoid server kick during a long scan.
    """
    sl, st, sr, sb, slot_w, slot_h = strip
    sw, sh = screen_wh()
    sweep_start = time.time()

    # ── S1: Two-tier prescan ─────────────────────────────────────────────
    high_slots, med_slots = prescan_strip(strip)
    total_items = len(high_slots) + len(med_slots)
    print(f"  sweep_strip: {len(high_slots)} HIGH + {len(med_slots)} MED  "
          f"[slot_w={slot_w} slot_h={slot_h}]")

    def process_slot(row, col, is_high):
        """Returns True if this slot was successfully clicked."""
        nonlocal sweep_start

        elapsed = time.time() - sweep_start
        if elapsed > SWEEP_TIMEOUT:
            print(f"  ⚠ timeout at {elapsed:.1f}s — aborting sweep")
            return "timeout"

        # Screen coords of slot centre
        sx = sl + col * slot_w + slot_w // 2
        sy = st + row * slot_h + slot_h // 2

        label = f"row{row+1}c{col+1}[{'H' if is_high else 'M'}]"

        # ── S2: Hover + spiral fallback ──────────────────────────────────
        found, mx, my = hover_spiral(sx, sy, sw, sh)

        if not found:
            # No tooltip even after spiral — truly empty slot
            print(f"  {label}: no tooltip after spiral → skip")
            return False

        # ── S3: Voted tooltip reading ────────────────────────────────────
        answer = read_tooltip_voted()
        print(f"  {label}: {answer}")

        if answer == "confirm":
            if is_high:
                # ── S4: Double-check before clicking (HIGH slots only) ───
                ok = double_check_confirm(mx, my, sw, sh)
                if not ok:
                    return False

            # ── S5: Click with popup-close verification ──────────────────
            success = click_and_verify(mx, my)
            elapsed = time.time() - sweep_start
            _log(f"{'SOLVED' if success else 'CLICK-FAIL'} "
                 f"{label} conf={'H' if is_high else 'M'} t={elapsed:.2f}s")

            if success:
                time.sleep(random.uniform(0.8, 1.4))  # brief rest
                return True
            else:
                # Click failed; continue scanning remaining slots
                print(f"  {label}: click failed — continuing scan")
                return False

        elif answer == "deny":
            return False

        # "empty" or unknown
        return False

    # Process HIGH-confidence slots first
    for row, col in high_slots:
        result = process_slot(row, col, is_high=True)
        if result == "timeout":
            _log(f"TIMEOUT items={total_items} t={time.time()-sweep_start:.2f}s")
            return False
        if result is True:
            return True

    # Process MEDIUM-confidence slots (no double-check, lighter logging)
    for row, col in med_slots:
        result = process_slot(row, col, is_high=False)
        if result == "timeout":
            _log(f"TIMEOUT items={total_items} t={time.time()-sweep_start:.2f}s")
            return False
        if result is True:
            return True

    _log(f"FAILED items={total_items} t={time.time()-sweep_start:.2f}s")
    return False

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
#  MAIN LOOP
# ═══════════════════════════════════════════════════════════════
def main():
    mode = ("AI+color" if HAS_AI else "") + ("+OCR" if HAS_TESS else "") or "color-only"
    print(f"[ AFK solver ] mode: {mode}")
    print( "[ AFK solver ] watching for Afk Grinding popup...\n")

    idle=0
    while os.path.exists(FLAG):
        try:
            # Fast check — is any big gray region visible?
            if not popup_open():
                idle+=1
                if idle%40==0: print("[ AFK solver ] still watching...")
                time.sleep(POLL)
                continue

            # Confirm it's the Afk Grinding strip and get exact coords
            strip = find_afk_strip()
            if strip is None:
                time.sleep(POLL)
                continue

            sl,st,sr,sb,slot_w,slot_h=strip
            print(f"[ AFK solver ] Afk strip found — width={sr-sl}px  slot_w={slot_w}px  slot_h={slot_h}px")

            # Short human-like pause before reacting
            time.sleep(random.uniform(REACT_MIN, REACT_MAX))

            idle=0
            solved = sweep_strip(strip)

            if solved:
                print("[ AFK solver ] ✓ solved! Back to watching...\n")
            else:
                # Didn't find it — retry quickly (server may still be animating)
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

    # Pause while AFK solver is in the middle of clicking
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
