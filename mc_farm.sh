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
HOVER_WAIT = 0.14   # wait after moving mouse (MC tooltip appears within 1-2 ticks = 50-100ms)
REACT_MIN  = 0.28   # human-like pause before starting to solve (min)
REACT_MAX  = 0.95   # human-like pause before starting to solve (max)

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

    # ── Slot size: 9 slots fill ~90% of popup width ───────────────────
    slot = int(pw * 0.90 / 9)
    slot = max(10, min(slot, 60))

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
    #  The "Inventory" label uses MC's pure-white text (R≥250,G≥250,B≥250).
    #  Items in the AFK grid don't have such pure-white pixels (they're colored).
    #  Scan rows in 42-52% of popup height for a white-pixel fraction > 8%.
    #  This works even when the Inventory separator row is the SAME gray shade
    #  as slot rows (making the dark-band method unreliable).
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
    if title_end_rel is not None:
        strip_top = pt + title_end_rel
    else:
        # MC title bar is ~14 px.  slot*0.45 ≈ 16 px at slot=35 — much
        # better than the old slot*1.1 ≈ 38 px which landed strip_top
        # 20 px BELOW actual row-0 item centers, causing prescan to miss them.
        strip_top = pt + int(slot * 0.45)

    if inv_sep_rel is not None:
        strip_bot = pt + inv_sep_rel
        print(f"  Popup {pw}×{ph}px  slot≈{slot}px  "
              f"title_end={title_end_rel}px  inv_sep={inv_sep_rel}px  [exact]")
    else:
        # Measured from screenshots: "Inventory" label always at 45.7% of ph.
        strip_bot = pt + int(ph * 0.46)
        print(f"  Popup {pw}×{ph}px  slot≈{slot}px  "
              f"title_end={title_end_rel}px  sep=not found [46% fallback]")

    if strip_bot <= strip_top: return None

    # Center 9 columns horizontally within the popup
    strip_left  = pl + (pw - 9*slot) // 2
    strip_right = strip_left + 9*slot

    return (strip_left, strip_top, strip_right, strip_bot, slot)

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

def read_tooltip(mx, my):
    """
    Capture the area where the tooltip appears, then try AI → OCR → color.

    From screenshots: the tooltip appears to the RIGHT of the hovered item
    and can extend well beyond the popup's right edge (even near screen edge).
    It sits roughly level with the cursor, slightly above.
    We capture a wide band (450px) that starts well to the left of the cursor
    so we also catch tooltips that open leftward on right-edge slots.
    """
    sw,sh=screen_wh()
    tx=max(0,  mx-100);  ty=max(0,  my-110)
    tw=min(sw-tx, 460);  th=min(sh-ty, 145)
    scrot(tx,ty,tw,th)

    # ── Fast gate: no tooltip background → empty slot, skip everything ──
    # This prevents AI/OCR calls on the ~18 empty slots out of 27.
    if not has_tooltip(SNAP):
        return "empty"

    # ── Tooltip IS present — now identify it ────────────────────────────
    if HAS_AI:
        a=ask_ai(SNAP)
        if a is not None: print(f"    [AI]    → {a}"); return a
    if HAS_TESS:
        a=ask_ocr(SNAP)
        if a is not None: print(f"    [OCR]   → {a}"); return a
    a=ask_color(SNAP)
    print(f"    [color] → {a}"); return a

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
def prescan_strip(strip):
    """
    Take ONE screenshot of the AFK strip, look for slots that contain items.

    Empty slots are pure MC gray (rgb ~198,198,198) with a slightly darker
    1-2px border. Slots with items have many colorful (non-gray) pixels.

    Colorfulness test: if max(R,G,B) - min(R,G,B) > 30 the pixel is clearly
    not a uniform gray shade.  We sample a central 12×12 box inside each slot
    to avoid counting the gray border edge.

    Returns a list of (row, col) pairs with items, in left-to-right, top-to-
    bottom order (same as the old sweep order).  Falls back to ALL slots if
    detection is inconclusive.
    """
    sl, st, sr, sb, slot = strip
    n_rows = max(1, (sb - st) // slot)
    sw, sh = screen_wh()

    # Screenshot only the AFK strip for speed
    px = max(0, sl);  py = max(0, st)
    pw = min(sw - px, sr - sl + 4)
    ph = min(sh - py, sb - st + 4)
    scrot(px, py, pw, ph)

    r = decode_png(SNAP)
    if r is None:
        print("  Prescan: decode failed — hovering all slots")
        return [(row, col) for row in range(n_rows) for col in range(9)]

    img_rows, iw, ih, bpp = r
    found = []

    for row in range(n_rows):
        for col in range(9):
            # Centre of slot in IMAGE coordinates (image starts at sl,st)
            cx = col * slot + slot // 2
            cy = row * slot + slot // 2

            # Sample a 12×12 window around the slot centre
            half = 6
            x0 = max(0, cx - half);  x1 = min(iw, cx + half)
            y0 = max(0, cy - half);  y1 = min(ih, cy + half)

            colorful = 0
            for iy in range(y0, y1):
                rd = img_rows[iy]
                for ix in range(x0, x1):
                    rr = rd[ix*bpp]; gg = rd[ix*bpp+1]; bb = rd[ix*bpp+2]
                    if max(rr,gg,bb) - min(rr,gg,bb) > 30:
                        colorful += 1
                    if colorful >= 8:
                        break
                if colorful >= 8:
                    break

            if colorful >= 8:
                found.append((row, col))

    total = 9 * n_rows
    print(f"  Prescan: {len(found)} item slots out of {total} — skipping {total-len(found)} empty")
    if not found:
        print("  Prescan found nothing colorful — hovering all slots as fallback")
        return [(row, col) for row in range(n_rows) for col in range(9)]
    return found


# ═══════════════════════════════════════════════════════════════
#  SWEEP THE AFK STRIP — prescan, then hover only item slots
# ═══════════════════════════════════════════════════════════════
def sweep_strip(strip):
    """
    strip = (left, top, right, bottom, slot_px)

    1. prescan_strip() takes ONE screenshot to find which of the 27 slots
       actually contain items (colorful pixels vs pure gray empty slots).
    2. Only those ~5-8 slots are hovered and tooltip-checked.

    OLD: 27 slots × 0.20s = 5.4s minimum traversal.
    NEW: ~6 slots × 0.14s = 0.84s traversal  (+ 1 prescan screenshot ~0.1s)

    Clicks the FIRST slot whose tooltip says CONFIRM.
    Returns True if solved, False if no confirm found.
    """
    sl, st, sr, sb, slot = strip

    item_slots = prescan_strip(strip)
    print(f"  Hovering {len(item_slots)} slot(s)")

    for row, col in item_slots:
        sx = sl + col*slot + slot//2
        sy = st + row*slot + slot//2

        xdo('mousemove', str(sx), str(sy))
        time.sleep(HOVER_WAIT + random.uniform(0, 0.05))

        answer = read_tooltip(sx, sy)

        if answer == "confirm":
            print(f"  row {row+1} col {col+1}: GREEN ✓ — clicking!")
            open(LOCK,'w').close()
            xdo('mousemove', str(sx), str(sy))
            time.sleep(random.uniform(0.03, 0.09))
            xdo('mousedown','1')
            time.sleep(random.uniform(0.07, 0.14))
            xdo('mouseup','1')
            time.sleep(random.uniform(1.9, 3.3))   # wait for popup to close
            try: os.remove(LOCK)
            except: pass
            return True

        elif answer == "deny":
            print(f"  row {row+1} col {col+1}: red ✗ — skip")
        # "empty" = prescan false-positive, just continue

    return False   # finished all item slots, nothing clicked

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

            sl,st,sr,sb,slot=strip
            print(f"[ AFK solver ] Afk strip found — width={sr-sl}px, slot≈{slot}px")

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
