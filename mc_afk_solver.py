#!/usr/bin/env python3
"""
mc_afk_solver.py — Modular AFK-farm automation for JartexNetwork OneBlock.

Workflow:
  1. Monitor title strip of the Minecraft window for the "Afk Grinding" popup.
  2. On detection: delete /tmp/mc_spamming to halt mc_spam_2.sh immediately.
  3. Sweep the 9×3 inventory grid (slots 0-26) with a 120 ms hover settle.
  4. Capture a 150×100 px tooltip crop per slot; apply HSV mask for #55FF55.
  5. First slot with green pixel density above threshold → left-click.
  6. Wait 800 ms, recreate /tmp/mc_spamming, relaunch mc_spam_2.sh.
  7. Archive slot crops; if no slot found after full sweep → failsafe abort.

Usage:
    python3 mc_afk_solver.py               # continuous (default)
    python3 mc_afk_solver.py --once        # solve one popup, then exit
    python3 mc_afk_solver.py --dry-run     # sweep + report, no click
    python3 mc_afk_solver.py --calibrate-only  # print grid geometry and exit
    python3 mc_afk_solver.py --timeout N   # popup wait timeout in seconds
    python3 mc_afk_solver.py --script PATH # override grinder script path
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple, List

import cv2
import numpy as np

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("afk_solver")

# ── Tunables ──────────────────────────────────────────────────────────────────

MONITOR_POLL_SECONDS   = 0.25   # popup check interval
HOVER_SETTLE_MS        = 120    # ms to wait after cursor lands on a slot
GLIDE_DURATION_MS      = 80     # ms to spend gliding between slots
GLIDE_STEPS            = 8      # sub-steps per cursor glide
CLICK_RECOVERY_MS      = 800    # ms after click for server UI to clear
SWEEP_TIMEOUT_SECONDS  = 15     # abort sweep if this many seconds pass
GREEN_PIXEL_THRESHOLD  = 30     # min #55FF55 pixels to confirm a slot

# ── Dell Inspiron 3521 / Minecraft GUI scale 2 hardcoded profile ──────────────
# Measured precisely from 68 reference screenshots (1366×694 windowed Minecraft).
# Used as the primary fallback when dynamic calibration cannot parse assets.
#
# GUI panel:  cols 510–853, rows 182–507
# Slot size:  36 px (32 px inner + 2 px dark border each side)
# Slot margin from panel edge: X=10 px, Y=30 px (to outer border of slot 0)
# Slot col centres: gui_left + 28 + col*36   (for col 0–8)
# Slot row centres: gui_top  + 48 + row*36   (for row 0–2)
DELL_GUI_LEFT   = 510
DELL_GUI_TOP    = 182
DELL_SLOT_SIZE  = 36
DELL_SLOT_X_OFF = 28   # gui_left  → first slot centre X
DELL_SLOT_Y_OFF = 48   # gui_top   → first slot centre Y
# Panel detection pixels: these coords land in the grey header strip and
# should be BGR ≈ (198,198,198) when the Afk Grinding chest is open.
POPUP_PROBE_PTS = [
    (680, 193),
    (600, 193),
    (750, 193),
    (680, 205),
]
POPUP_GREY_MIN = 160   # each channel must be above this when GUI open
POPUP_GREY_MAX = 220   # and below this

# HSV range for #55FF55 (Minecraft "Click to Confirm" bright green text)
HSV_GREEN_LOWER = np.array([35, 100, 100], dtype=np.uint8)
HSV_GREEN_UPPER = np.array([85, 255, 255], dtype=np.uint8)

# Flag-file paths
FLAG_FILE      = "/tmp/mc_spamming"
SOLVER_PID_FILE = "/tmp/mc_solver.pid"

# Runtime capture dir
CAPTURE_DIR = Path("/tmp/mc_afk_captures")
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

# Asset directory (reference screenshots for calibration)
ASSET_DIR = Path(__file__).parent / "attached_assets"


# ── CalibrationEngine ─────────────────────────────────────────────────────────

class CalibrationEngine:
    """Derives the 9×3 inventory grid geometry by scanning attached_assets/."""

    GRID_COLS = 9
    GRID_ROWS = 3
    SLOT_COUNT = GRID_COLS * GRID_ROWS  # 27

    # Minecraft inventory GUI colours (BGR) used to locate the chest panel
    _INVENTORY_BG_BGR = (198, 198, 198)   # light grey panel background
    _BORDER_DARK_BGR  = (85,  85,  85)    # dark border pixel

    def __init__(self, asset_dir: Path = ASSET_DIR) -> None:
        self.asset_dir = asset_dir
        self._slot_centres: Optional[List[Tuple[int, int]]] = None
        self._screen_w: int = 1366
        self._screen_h: int = 768
        self._gui_left: int = 0
        self._gui_top:  int = 0
        self._slot_size: int = 36
        self._title_box: Optional[Tuple[int, int, int, int]] = None  # x,y,w,h

    # ── Public ─────────────────────────────────────────────────────────────

    def calibrate(self) -> None:
        """Scan every PNG in asset_dir to derive grid layout."""
        pngs = sorted(self.asset_dir.glob("*.png"))
        if not pngs:
            log.warning("No PNGs in %s — using fallback geometry", self.asset_dir)
            self._build_fallback()
            return

        results = []
        for path in pngs:
            img = cv2.imread(str(path))
            if img is None:
                continue
            r = self._analyse_frame(img)
            if r:
                results.append(r)

        if not results:
            log.warning("Could not detect GUI in any asset — using fallback")
            self._build_fallback()
            return

        # Median across all frames for robustness
        h, w = cv2.imread(str(pngs[0])).shape[:2]
        self._screen_w = w
        self._screen_h = h
        self._gui_left  = int(np.median([r[0] for r in results]))
        self._gui_top   = int(np.median([r[1] for r in results]))
        self._slot_size = int(np.median([r[2] for r in results]))

        self._build_grid()
        log.info(
            "Calibrated from %d frames: screen %dx%d | gui_left=%d gui_top=%d "
            "slot_size=%dpx | %d slot centres",
            len(results), w, h,
            self._gui_left, self._gui_top, self._slot_size,
            len(self._slot_centres),
        )

    @property
    def slot_centres(self) -> List[Tuple[int, int]]:
        if self._slot_centres is None:
            self.calibrate()
        return self._slot_centres

    @property
    def title_box(self) -> Tuple[int, int, int, int]:
        """Bounding box (x, y, w, h) to poll for the popup title."""
        if self._title_box is None:
            self.calibrate()
        return self._title_box

    def print_geometry(self) -> None:
        """Print derived grid geometry to stdout (--calibrate-only)."""
        self.calibrate()
        print(f"\n{'─'*56}")
        print(f"  Screen   : {self._screen_w} × {self._screen_h}")
        print(f"  GUI left : {self._gui_left}   GUI top : {self._gui_top}")
        print(f"  Slot size: {self._slot_size} px")
        print(f"  Title box: {self.title_box}")
        print(f"{'─'*56}")
        for i, (cx, cy) in enumerate(self.slot_centres):
            col = i % self.GRID_COLS
            row = i // self.GRID_COLS
            print(f"  slot [{i:2d}]  row={row} col={col}  centre=({cx:4d}, {cy:4d})")
        print(f"{'─'*56}\n")

    # ── Private ────────────────────────────────────────────────────────────

    def _analyse_frame(self, img: np.ndarray) -> Optional[Tuple[int, int, int]]:
        """Return (gui_left, gui_top, slot_size) or None.

        Uses the Minecraft inventory panel colour (#C6C6C6 = BGR 198,198,198)
        to locate the grey panel, then finds slot borders via the dark inner
        background (#8B8B8B = BGR 139,139,139) column runs.
        """
        h, w = img.shape[:2]

        # ── Find rows that are dominated by panel grey (#C6C6C6) ──────────
        grey_mask_2d = (
            (np.abs(img[:, :, 0].astype(np.int16) - 198) < 14) &
            (np.abs(img[:, :, 1].astype(np.int16) - 198) < 14) &
            (np.abs(img[:, :, 2].astype(np.int16) - 198) < 14)
        )
        grey_rows = np.where(grey_mask_2d.any(axis=1))[0]
        grey_cols = np.where(grey_mask_2d.any(axis=0))[0]
        if len(grey_rows) < 20 or len(grey_cols) < 50:
            return None

        gui_top  = int(grey_rows[0])
        gui_left = int(grey_cols[0])

        # ── Locate slot pitch via dark border columns (#373737 ≈ BGR 55,55,55)
        # Sample a row 38 px below gui_top (inside first slot row)
        sample_y = gui_top + 38
        if sample_y >= h:
            return None
        line = img[sample_y, gui_left:gui_left + 400]
        dark = np.where(line.mean(axis=1) < 90)[0]
        if len(dark) < 2:
            return None
        gaps = np.diff(dark)
        large = gaps[gaps > 20]
        slot_size = int(np.median(large)) if len(large) >= 2 else 36
        slot_size = max(28, min(48, slot_size))

        return gui_left, gui_top, slot_size

    def _build_grid(self) -> None:
        """Build 27 slot centres using the precise measured offsets.

        At GUI scale 2 (verified on 68 reference frames):
          - 2 px dark border + 32 px inner = 36 px per slot
          - X margin from gui_left to first slot outer border = 10 px
          - Y offset from gui_top  to first slot outer border = 30 px
          - Slot centre X = gui_left + 28 + col * slot_size   (10+18=28)
          - Slot centre Y = gui_top  + 48 + row * slot_size   (30+18=48)
        """
        centres = []
        for row in range(self.GRID_ROWS):
            for col in range(self.GRID_COLS):
                cx = self._gui_left + DELL_SLOT_X_OFF + col * self._slot_size
                cy = self._gui_top  + DELL_SLOT_Y_OFF + row * self._slot_size
                centres.append((cx, cy))
        self._slot_centres = centres

        # Title / header strip: the grey panel area above the first slot row
        # (rows gui_top .. gui_top+29, full panel width = 9 slots × slot_size)
        panel_w = self.GRID_COLS * self._slot_size          # 324 px
        self._title_box = (
            self._gui_left + 10,   # skip panel left border
            self._gui_top  + 4,    # skip panel top border
            panel_w,
            26,                    # header height before first slot
        )

    def _build_fallback(self) -> None:
        """Hardcoded profile for Dell Inspiron 3521, Minecraft GUI scale 2,
        windowed at 1366×694. Pixel-measured from 68 reference screenshots."""
        self._screen_w  = 1366
        self._screen_h  = 694
        self._gui_left  = DELL_GUI_LEFT
        self._gui_top   = DELL_GUI_TOP
        self._slot_size = DELL_SLOT_SIZE
        self._build_grid()
        log.info(
            "Using hardcoded Dell Inspiron 3521 / GUI scale 2 profile "
            "(gui_left=%d, gui_top=%d, slot_size=%d)",
            self._gui_left, self._gui_top, self._slot_size,
        )


# ── WindowFinder ──────────────────────────────────────────────────────────────

class WindowFinder:
    """Locates the Minecraft window and verifies X11 focus before input."""

    TARGET_CLASSES = ("Minecraft", "net-minecraft-client-main-GameWindow",
                      "com-mojang", "minecraft")

    def get_window_id(self) -> Optional[str]:
        for cls in self.TARGET_CLASSES:
            try:
                result = subprocess.run(
                    ["xdotool", "search", "--class", cls],
                    capture_output=True, text=True, timeout=3,
                )
                ids = result.stdout.strip().split()
                if ids:
                    return ids[-1]
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        return None

    def is_focused(self) -> bool:
        """Return True if the Minecraft window currently has X11 focus."""
        wid = self.get_window_id()
        if not wid:
            return False
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip() == wid
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def focus(self) -> bool:
        """Bring the Minecraft window to front. Return True on success."""
        wid = self.get_window_id()
        if not wid:
            log.warning("Cannot find Minecraft window")
            return False
        try:
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", wid],
                timeout=3, check=True,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired,
                subprocess.CalledProcessError):
            return False


# ── ScreenCapture ─────────────────────────────────────────────────────────────

class ScreenCapture:
    """Thin wrapper around mss for low-overhead screen grabs.

    mss.mss() is constructed lazily so that --calibrate-only works without
    an X11 display.
    """

    def __init__(self) -> None:
        self._sct = None

    def _sct_(self):
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        return self._sct

    def grab_region(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        """Return a BGR numpy array for the given screen region."""
        mon = {"left": x, "top": y, "width": w, "height": h}
        raw = self._sct_().grab(mon)
        frame = np.frombuffer(raw.raw, dtype=np.uint8).reshape(raw.height, raw.width, 4)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


# ── GuiMonitor ────────────────────────────────────────────────────────────────

class GuiMonitor:
    """Polls the Minecraft window for the Afk Grinding chest GUI.

    Detection method: sample POPUP_PROBE_PTS pixel coordinates that land
    inside the grey panel header (#C6C6C6 = BGR 198,198,198).  When the
    chest GUI is open, ≥3 of those pixels must be in the grey range
    [POPUP_GREY_MIN, POPUP_GREY_MAX] on all three channels.  When the
    game scene is visible instead, those same pixels are arbitrary colours
    from the world and will not match.

    Probe points and thresholds are defined as module-level constants and
    are pixel-measured from the Dell Inspiron 3521 / GUI scale 2 setup.
    """

    NEEDED_MATCHES = 3   # out of 4 probe points must pass

    def __init__(self, capture: ScreenCapture,
                 title_box: Tuple[int, int, int, int]) -> None:
        self.capture   = capture
        self.title_box = title_box   # kept for --calibrate-only display

    def popup_visible(self) -> bool:
        """Return True when the Afk Grinding panel grey pixels are detected."""
        # Grab a 1-pixel-tall strip at each probe row in one mss call per row
        # (mss overhead is low, this is fast enough at 0.25 s poll rate).
        matches = 0
        for (px, py) in POPUP_PROBE_PTS:
            frame = self.capture.grab_region(px, py, 1, 1)   # 1×1 BGR pixel
            b, g, r = int(frame[0, 0, 0]), int(frame[0, 0, 1]), int(frame[0, 0, 2])
            if (POPUP_GREY_MIN <= b <= POPUP_GREY_MAX and
                    POPUP_GREY_MIN <= g <= POPUP_GREY_MAX and
                    POPUP_GREY_MIN <= r <= POPUP_GREY_MAX):
                matches += 1
        return matches >= self.NEEDED_MATCHES


# ── TooltipAnalyzer ───────────────────────────────────────────────────────────

class TooltipAnalyzer:
    """Captures a 150×100 tooltip crop and applies the green HSV mask."""

    CROP_W = 150
    CROP_H = 100

    def __init__(self, capture: ScreenCapture) -> None:
        self.capture = capture

    def green_pixel_count(
        self,
        slot_cx: int,
        slot_cy: int,
        save_path: Optional[Path] = None,
    ) -> int:
        """Return number of #55FF55 pixels in the tooltip zone near this slot."""
        # Tooltip renders to the right of the cursor; fall back left if near edge
        x = slot_cx + 12
        y = slot_cy - 10
        # Clamp to plausible screen region
        x = max(0, x)
        y = max(0, y)

        crop_bgr = self.capture.grab_region(x, y, self.CROP_W, self.CROP_H)

        if save_path:
            cv2.imwrite(str(save_path), crop_bgr)

        hsv  = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, HSV_GREEN_LOWER, HSV_GREEN_UPPER)
        return int(np.sum(mask > 0))


# ── GridNavigator ─────────────────────────────────────────────────────────────

class GridNavigator:
    """Moves the cursor smoothly through the 9×3 slot grid."""

    def __init__(self) -> None:
        self._mouse = None

    def _mouse_(self):
        if self._mouse is None:
            import pyautogui
            pyautogui.FAILSAFE = False
            self._mouse = pyautogui
        return self._mouse

    def move_to(self, x: int, y: int) -> None:
        """Glide cursor to (x, y) over GLIDE_DURATION_MS ms."""
        try:
            import pyautogui
            cur_x, cur_y = pyautogui.position()
        except Exception:
            cur_x, cur_y = x, y

        step_s = (GLIDE_DURATION_MS / 1000.0) / max(GLIDE_STEPS, 1)
        for step in range(1, GLIDE_STEPS + 1):
            t = step / GLIDE_STEPS
            nx = int(cur_x + (x - cur_x) * t)
            ny = int(cur_y + (y - cur_y) * t)
            self._mouse_().moveTo(nx, ny, duration=0)
            time.sleep(step_s)

    def click(self, x: int, y: int) -> None:
        self._mouse_().click(x, y, button="left")

    def park(self) -> None:
        """Move cursor to safe neutral corner."""
        self._mouse_().moveTo(10, 10, duration=0)


# ── ProcessController ─────────────────────────────────────────────────────────

class ProcessController:
    """Manages the flag file IPC and grinder script lifecycle."""

    def __init__(self, flag: str = FLAG_FILE, script: str = "") -> None:
        self.flag   = flag
        self.script = script or str(Path(__file__).parent / "mc_spam_2.sh")

    def halt_farm(self) -> None:
        try:
            os.remove(self.flag)
            log.info("Flag file deleted — grinder halted")
        except FileNotFoundError:
            log.debug("Flag file already absent")

    def resume_farm(self) -> None:
        """Recreate flag file and relaunch the grinder (grinder-only mode)."""
        Path(self.flag).touch()
        log.info("Flag file recreated")
        if os.path.isfile(self.script):
            env = {**os.environ, "MC_GRINDER_ONLY": "1"}
            subprocess.Popen(
                ["bash", self.script],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            log.info("Grinder relaunched: %s", self.script)
        else:
            log.warning("Grinder script not found at %s", self.script)

    def farm_is_running(self) -> bool:
        return os.path.exists(self.flag)


# ── AssetSanitiser ────────────────────────────────────────────────────────────

class AssetSanitiser:
    """Moves per-run slot crops to a timestamped archive directory."""

    def __init__(self, capture_dir: Path = CAPTURE_DIR) -> None:
        self.capture_dir = capture_dir
        self._run_dir: Optional[Path] = None

    def run_dir(self) -> Path:
        if self._run_dir is None:
            ts = int(time.time())
            self._run_dir = self.capture_dir / f"run_{ts}"
            self._run_dir.mkdir(parents=True, exist_ok=True)
        return self._run_dir

    def slot_path(self, index: int) -> Path:
        return self.capture_dir / f"slot_{index:02d}.png"

    def archive(self) -> None:
        """Move all slot_NN.png files into the run archive."""
        crops = list(self.capture_dir.glob("slot_*.png"))
        if not crops:
            return
        dest = self.run_dir()
        for f in crops:
            shutil.move(str(f), str(dest / f.name))
        log.info("Archived %d crops → %s", len(crops), dest)
        self._run_dir = None  # reset for next run


# ── AfkSolver ─────────────────────────────────────────────────────────────────

class AfkSolver:
    """Main orchestrator."""

    def __init__(
        self,
        calibration:  CalibrationEngine,
        capture:      ScreenCapture,
        monitor:      GuiMonitor,
        navigator:    GridNavigator,
        analyzer:     TooltipAnalyzer,
        controller:   ProcessController,
        sanitiser:    AssetSanitiser,
        window:       WindowFinder,
        dry_run:      bool = False,
    ) -> None:
        self.calib      = calibration
        self.capture    = capture
        self.monitor    = monitor
        self.nav        = navigator
        self.analyzer   = analyzer
        self.controller = controller
        self.sanitiser  = sanitiser
        self.window     = window
        self.dry_run    = dry_run

    # ── Popup wait ─────────────────────────────────────────────────────────

    def wait_for_popup(self, timeout: Optional[float] = None) -> bool:
        """Block until the Afk Grinding popup is detected (or timeout)."""
        deadline = time.monotonic() + timeout if timeout else None
        log.info("Monitoring for popup (poll every %.2fs)…", MONITOR_POLL_SECONDS)
        while True:
            if self.monitor.popup_visible():
                log.info("Popup detected")
                return True
            if deadline and time.monotonic() >= deadline:
                log.info("Popup wait timed out after %.0fs", timeout)
                return False
            time.sleep(MONITOR_POLL_SECONDS)

    # ── Solve one popup ─────────────────────────────────────────────────────

    def solve(self) -> bool:
        """Sweep the grid, click the green slot. Return True on success."""

        # 1. Verify window focus before any input
        if not self.window.is_focused():
            log.info("Minecraft not focused — attempting to raise window")
            if not self.window.focus():
                log.error("Cannot focus Minecraft window — aborting solve")
                return False

        # 2. Halt the grinder
        self.controller.halt_farm()
        time.sleep(0.15)

        slots = self.calib.slot_centres
        confirm_idx: Optional[int] = None
        confirm_xy:  Optional[Tuple[int, int]] = None
        sweep_start  = time.monotonic()

        dry_run_results: List[Tuple[int, int]] = []  # (slot, green_px)

        log.info("Beginning grid sweep (%d slots)…", len(slots))

        for idx, (cx, cy) in enumerate(slots):

            # Global sweep timeout failsafe
            if time.monotonic() - sweep_start > SWEEP_TIMEOUT_SECONDS:
                log.error(
                    "Sweep timeout (%.0fs) — aborting after %d/%d slots",
                    SWEEP_TIMEOUT_SECONDS, idx, len(slots),
                )
                self._failsafe()
                self.sanitiser.archive()
                return False

            # Glide to slot centre
            self.nav.move_to(cx, cy)

            # Settle: let the game render the tooltip
            time.sleep(HOVER_SETTLE_MS / 1000.0)

            # Capture & analyse
            save_path = self.sanitiser.slot_path(idx)
            green_px  = self.analyzer.green_pixel_count(cx, cy, save_path=save_path)

            log.debug("  slot [%2d] (%4d,%4d)  green_px=%d", idx, cx, cy, green_px)

            if self.dry_run:
                dry_run_results.append((idx, green_px))
                continue

            if green_px >= GREEN_PIXEL_THRESHOLD:
                log.info("  ✓ CONFIRM  slot [%d]  green_px=%d", idx, green_px)
                confirm_idx = idx
                confirm_xy  = (cx, cy)
                break

        # ── Dry-run report ────────────────────────────────────────────────
        if self.dry_run:
            self._print_dry_run_report(dry_run_results)
            self.nav.park()
            self.controller.resume_farm()
            self.sanitiser.archive()
            return True

        # ── Failsafe: no slot found ───────────────────────────────────────
        if confirm_xy is None:
            log.error(
                "FAILSAFE — no green slot found after sweeping all %d slots",
                len(slots),
            )
            self._failsafe()
            self.controller.resume_farm()
            self.sanitiser.archive()
            return False

        # 3. Click
        log.info("Clicking slot [%d] at (%d, %d)", confirm_idx, *confirm_xy)
        self.nav.click(*confirm_xy)

        # 4. Recovery wait
        time.sleep(CLICK_RECOVERY_MS / 1000.0)

        # 5. Resume grinder
        self.controller.resume_farm()

        # 6. Archive crops
        self.sanitiser.archive()

        return True

    # ── Failsafe ────────────────────────────────────────────────────────────

    def _failsafe(self) -> None:
        """Park cursor at screen boundary and log diagnostic."""
        self.nav.park()
        log.error(
            "Failsafe triggered — cursor parked at (10,10). "
            "Check /tmp/mc_afk_captures/ for tooltip crops. "
            "Run --calibrate-only to verify grid geometry."
        )

    # ── Dry-run report ──────────────────────────────────────────────────────

    @staticmethod
    def _print_dry_run_report(results: List[Tuple[int, int]]) -> None:
        sep = "─" * 58
        print(f"\n{'─'*20}  DRY-RUN SCAN REPORT  {'─'*16}")
        print(f"  {'Slot':>5}   {'Green px':>8}   {'Verdict'}")
        print(sep)
        best_idx, best_green = -1, -1
        for idx, green in results:
            verdict = "✓ CONFIRM" if green >= GREEN_PIXEL_THRESHOLD else "  empty  "
            print(f"  [{idx:2d}]   {green:>8}   {verdict}")
            if green > best_green:
                best_green = green
                best_idx   = idx
        print(sep)
        confirms = sum(1 for _, g in results if g >= GREEN_PIXEL_THRESHOLD)
        print(f"  Scanned {len(results)}/27 slots — {confirms} confirm")
        if best_green >= GREEN_PIXEL_THRESHOLD:
            print(f"  Best confirm → slot [{best_idx}]  green_px={best_green}")
        else:
            print("  WARNING: no confirm slot detected — check calibration")
        print()

    # ── Main loops ──────────────────────────────────────────────────────────

    def run_once(self, timeout: Optional[float] = None) -> bool:
        """Wait for one popup, solve it, return success flag."""
        found = self.wait_for_popup(timeout=timeout)
        if not found:
            return False
        return self.solve()

    def run_continuous(self) -> None:
        """Loop forever: wait for popup, solve, repeat."""
        log.info("Continuous mode — Ctrl-C to exit")
        while True:
            self.wait_for_popup()
            self.solve()


# ── Factory ───────────────────────────────────────────────────────────────────

def build_solver(script: str = "", dry_run: bool = False) -> AfkSolver:
    calib      = CalibrationEngine()
    capture    = ScreenCapture()
    sanitiser  = AssetSanitiser()
    window     = WindowFinder()

    # Calibrate immediately so title_box is available
    calib.calibrate()

    monitor    = GuiMonitor(capture, calib.title_box)
    navigator  = GridNavigator()
    analyzer   = TooltipAnalyzer(capture)
    controller = ProcessController(script=script)

    return AfkSolver(
        calibration=calib,
        capture=capture,
        monitor=monitor,
        navigator=navigator,
        analyzer=analyzer,
        controller=controller,
        sanitiser=sanitiser,
        window=window,
        dry_run=dry_run,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AFK-popup solver for JartexNetwork OneBlock",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Solve one popup then exit",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Sweep all slots and print a scan report — no click sent",
    )
    parser.add_argument(
        "--calibrate-only", action="store_true",
        help="Print auto-derived grid geometry and exit",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, metavar="SECONDS",
        help="Popup wait timeout for --once / --dry-run (default: 30)",
    )
    parser.add_argument(
        "--script", default="", metavar="PATH",
        help="Path to grinder script to halt/resume (default: mc_spam_2.sh)",
    )
    args = parser.parse_args()

    # Write our PID so mc_spam_2.sh can kill us on stop
    Path(SOLVER_PID_FILE).write_text(str(os.getpid()))

    if args.calibrate_only:
        calib = CalibrationEngine()
        calib.print_geometry()
        return

    solver = build_solver(script=args.script, dry_run=args.dry_run)

    try:
        if args.once or args.dry_run:
            ok = solver.run_once(timeout=args.timeout)
            sys.exit(0 if ok else 1)
        else:
            solver.run_continuous()
    except KeyboardInterrupt:
        log.info("Interrupted — exiting")
    finally:
        # Clean up PID file
        try:
            os.remove(SOLVER_PID_FILE)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
