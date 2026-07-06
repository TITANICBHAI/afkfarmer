#!/usr/bin/env python3
"""
mc_afk_solver.py — Modular AFK-Grinding verification solver for JartexNetwork OneBlock.

Workflow:
  1. Monitor a title-bar strip for the "Afk Grinding" ChestGui popup.
  2. Kill mc_farm.sh (via flag-file deletion) the instant it appears.
  3. Sweep all 27 chest slots; capture a tooltip crop after each hover.
  4. HSV-mask for bright-green (#55FF55) "Click to Confirm" text.
  5. Left-click the matching slot, wait for packet clearance, then resume.
  6. Log diagnostics; bail gracefully if no green slot is found.
"""

import os
import sys
import glob
import time
import shutil
import logging
import subprocess
import tempfile
import datetime
from pathlib import Path
from typing import Optional, Tuple, List

import numpy as np
import cv2
import mss
import mss.tools

# ---------------------------------------------------------------------------
# X11 input backend — loaded lazily on first use so that --calibrate-only
# and import-time syntax checks work in headless (no-display) environments.
# ---------------------------------------------------------------------------

_input_ready = False
_pyautogui_mod = None
_xlib_dpy = None
_xlib_root = None
_xlib_X = None
_xlib_xtest = None


def _ensure_input() -> None:
    """Initialise whichever input backend is available (called before any move/click)."""
    global _input_ready, _pyautogui_mod, _xlib_dpy, _xlib_root, _xlib_X, _xlib_xtest
    if _input_ready:
        return

    try:
        import pyautogui as _pg
        _pg.FAILSAFE = False
        _pyautogui_mod = _pg
        _input_ready = True
        log.debug("Input backend: pyautogui")
        return
    except Exception:
        pass

    try:
        from Xlib import display as _xd, X as _xX
        from Xlib.ext import xtest as _xt
        _xlib_dpy  = _xd.Display()
        _xlib_root = _xlib_dpy.screen().root
        _xlib_X    = _xX
        _xlib_xtest = _xt
        _input_ready = True
        log.debug("Input backend: python-xlib")
        return
    except Exception:
        pass

    raise RuntimeError(
        "No X11 input backend available.  "
        "Install pyautogui or python-xlib and ensure DISPLAY is set."
    )


def _move_to(x: int, y: int) -> None:
    _ensure_input()
    if _pyautogui_mod:
        _pyautogui_mod.moveTo(x, y, duration=0.03)
    else:
        _xlib_root.warp_pointer(x, y)
        _xlib_dpy.sync()


def _left_click(x: int, y: int) -> None:
    _ensure_input()
    if _pyautogui_mod:
        _pyautogui_mod.click(x, y, button="left")
    else:
        _move_to(x, y)
        _xlib_xtest.fake_input(_xlib_dpy, _xlib_X.ButtonPress, 1)
        _xlib_dpy.sync()
        time.sleep(0.05)
        _xlib_xtest.fake_input(_xlib_dpy, _xlib_X.ButtonRelease, 1)
        _xlib_dpy.sync()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mc_afk_solver")

# ---------------------------------------------------------------------------
# Constants & tunables
# ---------------------------------------------------------------------------
FLAG_FILE = "/tmp/mc_spamming"
FARM_SCRIPT = str(Path(__file__).parent / "mc_spam_2.sh")

TARGET_WINDOW_CLASS = "Minecraft"        # xdotool class substring to match
MONITOR_POLL_SECONDS = 0.25             # how often to check for GUI popup
HOVER_SETTLE_MS = 120                   # ms to wait after moving to each slot
CLICK_RECOVERY_MS = 800                 # ms to wait after clicking

CHEST_COLS = 9
CHEST_ROWS = 3
CHEST_SLOTS = CHEST_COLS * CHEST_ROWS   # 27

TOOLTIP_W = 150                         # width of tooltip crop
TOOLTIP_H = 100                         # height of tooltip crop

# HSV bounds for bright-green Minecraft formatting colour #55FF55
GREEN_HSV_LO = np.array([35, 100, 100], dtype=np.uint8)
GREEN_HSV_HI = np.array([85, 255, 255], dtype=np.uint8)
GREEN_PIXEL_THRESHOLD = 30              # minimum green pixels to confirm match

# HSV bounds for bright-red Minecraft formatting colour #FF5555 ("Do not click").
# Red wraps around hue=0 in OpenCV so two ranges are needed.
RED_HSV_LO_A = np.array([0,   140, 140], dtype=np.uint8)
RED_HSV_HI_A = np.array([10,  255, 255], dtype=np.uint8)
RED_HSV_LO_B = np.array([170, 140, 140], dtype=np.uint8)
RED_HSV_HI_B = np.array([180, 255, 255], dtype=np.uint8)
RED_PIXEL_THRESHOLD  = 20               # minimum red pixels to flag as decoy

# Confidence scoring: a slot is only clicked when the green score is at least
# this many times larger than the red score, preventing ambiguous matches.
GREEN_RED_RATIO_MIN  = 2.0

ASSETS_DIR = Path(__file__).parent / "attached_assets"
CAPTURE_DIR = Path(tempfile.gettempdir()) / "mc_afk_captures"

# ---------------------------------------------------------------------------
# 1. Calibration Engine
#    Loads reference screenshots from attached_assets/ and calculates the
#    exact chest-GUI geometry so nothing is hard-coded.
# ---------------------------------------------------------------------------

class CalibrationEngine:
    """
    Dynamically derives the 9×3 inventory slot grid from reference screenshots.

    Strategy
    --------
    * Scan every PNG in attached_assets/ for the characteristic Minecraft
      inventory GUI border colour (near #8b8b8b, the standard stone-grey).
    * Find the leftmost / topmost / rightmost / bottommost contiguous rectangle
      that contains a horizontal run of ~9 evenly-spaced cells in the upper
      third.
    * Derive slot-centre coordinates from the border geometry.
    """

    # Minecraft GUI border colour range in BGR
    GUI_BORDER_BGR_LO = np.array([120, 120, 120], dtype=np.uint8)
    GUI_BORDER_BGR_HI = np.array([200, 200, 200], dtype=np.uint8)

    # Title bar background is very dark: approximately #3c3c3c
    TITLE_BG_BGR_LO = np.array([40, 40, 40], dtype=np.uint8)
    TITLE_BG_BGR_HI = np.array([80, 80, 80], dtype=np.uint8)

    def __init__(self) -> None:
        self.screen_w: int = 0
        self.screen_h: int = 0
        self.gui_left: int = 0
        self.gui_top: int = 0
        self.gui_right: int = 0
        self.gui_bottom: int = 0
        self.slot_size: int = 18          # fall-back; overwritten after calibration
        self.slot_centers: List[Tuple[int, int]] = []
        self.title_strip: Tuple[int, int, int, int] = (0, 0, 0, 0)  # x,y,w,h

    # ------------------------------------------------------------------
    def calibrate(self) -> bool:
        """Return True if calibration succeeded from at least one asset."""
        pngs = sorted(glob.glob(str(ASSETS_DIR / "*.png")))
        if not pngs:
            log.warning("No reference assets found in %s", ASSETS_DIR)
            return self._apply_fallback()

        results: List[dict] = []
        for path in pngs:
            r = self._analyse_image(path)
            if r:
                results.append(r)

        if not results:
            log.warning("Could not derive geometry from any asset — using fallback")
            return self._apply_fallback()

        # Average the measured geometry across all valid reference frames
        avg = lambda key: int(round(sum(r[key] for r in results) / len(results)))

        self.screen_w  = avg("screen_w")
        self.screen_h  = avg("screen_h")
        self.gui_left  = avg("gui_left")
        self.gui_top   = avg("gui_top")
        self.gui_right = avg("gui_right")
        self.slot_size = avg("slot_size")

        self._build_slot_centers()
        log.info(
            "Calibration: screen=%dx%d  gui_left=%d  gui_top=%d  "
            "gui_right=%d  slot_size=%d  (%d reference frames used)",
            self.screen_w, self.screen_h,
            self.gui_left, self.gui_top, self.gui_right,
            self.slot_size, len(results),
        )
        return True

    # ------------------------------------------------------------------
    def _analyse_image(self, path: str) -> Optional[dict]:
        """Extract GUI geometry from a single reference PNG."""
        img = cv2.imread(path)
        if img is None:
            return None

        h, w = img.shape[:2]

        # --- Locate the GUI title bar (dark rectangle, upper-centre region) ---
        # Look in the middle 60% of width and top 70% of height only
        x0 = int(w * 0.20)
        x1 = int(w * 0.80)
        y0 = int(h * 0.10)
        y1 = int(h * 0.70)
        roi = img[y0:y1, x0:x1]

        mask_dark = cv2.inRange(roi, self.TITLE_BG_BGR_LO, self.TITLE_BG_BGR_HI)
        # Dilate to bridge small gaps
        kernel = np.ones((3, 3), np.uint8)
        mask_dark = cv2.dilate(mask_dark, kernel, iterations=2)

        contours, _ = cv2.findContours(mask_dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        # Keep only wide, shallow rectangles (title bar shape)
        title_rects = []
        for c in contours:
            rx, ry, rw, rh = cv2.boundingRect(c)
            aspect = rw / max(rh, 1)
            if aspect > 3 and rw > w * 0.10:
                title_rects.append((rx + x0, ry + y0, rw, rh))

        if not title_rects:
            return None

        # Pick widest
        title_rects.sort(key=lambda r: r[2], reverse=True)
        tx, ty, tw, _ = title_rects[0]

        # GUI left edge ≈ title bar left; GUI right ≈ title bar right
        gui_left  = tx
        gui_right = tx + tw

        # --- Locate the chest-slot row immediately below the title bar ---
        # The slot row is light-grey (#8b8b8b).  Scan downward from ty+20
        gui_width = gui_right - gui_left
        if gui_width < 40:
            return None

        slot_size = self._estimate_slot_size(img, gui_left, gui_right, ty)
        if slot_size < 10:
            return None

        # GUI top ≈ ty - 5 (a few pixels of padding above title)
        gui_top = max(0, ty - 8)

        return {
            "screen_w": w,
            "screen_h": h,
            "gui_left": gui_left,
            "gui_top": gui_top,
            "gui_right": gui_right,
            "slot_size": slot_size,
        }

    # ------------------------------------------------------------------
    def _estimate_slot_size(self, img: np.ndarray, gl: int, gr: int, title_y: int) -> int:
        """
        Estimate individual slot pixel size.

        Scans horizontal luminance variance below the title bar to find the
        repeating cell boundaries.  Falls back to (grid_width / 9).
        """
        h, w = img.shape[:2]
        grid_w = gr - gl

        # Fallback: divide available width by 9 columns
        naive = int(round(grid_w / CHEST_COLS))

        # Scan a horizontal strip starting ~20px below title
        y_scan = min(title_y + 25, h - 5)
        row = img[y_scan, gl:gr, :]        # shape: (grid_w, 3)
        gray_row = row.mean(axis=1)        # luminance per pixel

        # Find local minima (slot dividers are slightly darker)
        dividers = []
        for i in range(2, len(gray_row) - 2):
            if gray_row[i] < gray_row[i-2] - 5 and gray_row[i] < gray_row[i+2] - 5:
                dividers.append(i)

        if len(dividers) >= 8:
            gaps = [dividers[i+1] - dividers[i] for i in range(min(8, len(dividers)-1))]
            estimated = int(round(sum(gaps) / len(gaps)))
            if 12 <= estimated <= 40:
                return estimated

        return naive

    # ------------------------------------------------------------------
    def _build_slot_centers(self) -> None:
        """Populate self.slot_centers[0..26] in left-to-right, top-to-bottom order."""
        # The chest grid starts ~18px below the title bar top
        chest_top = self.gui_top + 20

        # First slot x-centre is gui_left + (slot_size / 2) + 1px border
        first_x = self.gui_left + self.slot_size // 2 + 1

        centers = []
        for row in range(CHEST_ROWS):
            for col in range(CHEST_COLS):
                cx = first_x + col * self.slot_size
                cy = chest_top + row * self.slot_size + self.slot_size // 2
                centers.append((cx, cy))

        self.slot_centers = centers

        # Title-detection strip: thin horizontal band across GUI title area
        tw = self.gui_right - self.gui_left
        self.title_strip = (self.gui_left, self.gui_top, tw, 20)

    # ------------------------------------------------------------------
    def _apply_fallback(self) -> bool:
        """
        Hard-coded fallback geometry derived from the reference screenshots
        (1024×576, GUI centred ~x=383..632).
        """
        log.info("Applying hard-coded fallback geometry (1024×576 reference)")
        self.screen_w  = 1024
        self.screen_h  = 576
        self.gui_left  = 383
        self.gui_top   = 131
        self.gui_right = 633
        self.slot_size = 18
        self._build_slot_centers()
        return True


# ---------------------------------------------------------------------------
# 2. Window Finder
#    Confirms the target application window is active before sending input.
# ---------------------------------------------------------------------------

class WindowFinder:
    def __init__(self, window_class: str = TARGET_WINDOW_CLASS) -> None:
        self.window_class = window_class.lower()

    def get_window_id(self) -> Optional[str]:
        """Return the X11 window ID of the first matching window, or None."""
        try:
            out = subprocess.check_output(
                ["xdotool", "search", "--class", self.window_class],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            ids = out.strip().splitlines()
            return ids[0] if ids else None
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    def is_focused(self) -> bool:
        """Return True if the target window currently has keyboard focus."""
        wid = self.get_window_id()
        if not wid:
            return False
        try:
            active = subprocess.check_output(
                ["xdotool", "getactivewindow"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            return active == wid
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def focus(self) -> bool:
        """Bring the target window to focus. Return True on success."""
        wid = self.get_window_id()
        if not wid:
            log.error("Target window '%s' not found", self.window_class)
            return False
        try:
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", wid],
                check=True, stderr=subprocess.DEVNULL,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False


# ---------------------------------------------------------------------------
# 3. Process Controller
#    Manages /tmp/mc_spamming and the mc_farm.sh background process.
# ---------------------------------------------------------------------------

class ProcessController:
    def __init__(self, flag: str = FLAG_FILE, script: str = FARM_SCRIPT) -> None:
        self.flag   = flag
        self.script = script

    def halt_farm(self) -> None:
        """Delete the flag file — mc_farm.sh loop exits on its next iteration."""
        if os.path.exists(self.flag):
            os.remove(self.flag)
            log.info("Flag file deleted — mc_farm.sh halted")
        else:
            log.debug("Flag file was already absent")

    def resume_farm(self) -> None:
        """Re-create the flag file and re-launch mc_farm.sh in the background."""
        Path(self.flag).touch()
        log.info("Flag file recreated")
        if os.path.isfile(self.script):
            subprocess.Popen(
                ["bash", self.script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            log.info("mc_farm.sh relaunched")
        else:
            log.warning("mc_farm.sh not found at %s — skipping relaunch", self.script)

    def farm_is_running(self) -> bool:
        return os.path.exists(self.flag)


# ---------------------------------------------------------------------------
# 4. Capture Helper
#    Thin wrapper around mss for region screenshots.
#    The mss context is opened lazily on the first grab call so that
#    --calibrate-only works in headless environments with no X11 display.
# ---------------------------------------------------------------------------

class ScreenCapture:
    def __init__(self) -> None:
        self._sct: Optional[mss.base.MSSBase] = None

    def _get_sct(self) -> "mss.base.MSSBase":
        if self._sct is None:
            self._sct = mss.MSS()
        return self._sct

    def grab_region(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        """Return a BGR numpy array for the given screen region."""
        mon = {"left": x, "top": y, "width": w, "height": h}
        raw = self._get_sct().grab(mon)
        arr = np.array(raw, dtype=np.uint8)[:, :, :3]
        return arr

    def grab_full(self) -> np.ndarray:
        sct = self._get_sct()
        mon = sct.monitors[1]  # primary monitor
        raw = sct.grab(mon)
        return np.array(raw, dtype=np.uint8)[:, :, :3]

    def save(self, arr: np.ndarray, path: str) -> None:
        cv2.imwrite(path, arr)


# ---------------------------------------------------------------------------
# 5. GUI Monitor
#    Background loop; calls back when "Afk Grinding" popup is detected.
# ---------------------------------------------------------------------------

class GuiMonitor:
    """
    Watches a thin horizontal strip at the top of the GUI region.

    Detection strategy: look for a sudden appearance of the dark Minecraft
    title-bar background colour (#3c3c3c) in a region that is normally game
    world pixels (colourful / varying).  Uses a pixel-density threshold so
    a single speckle does not trigger falsely.
    """

    TITLE_BG_LO = np.array([35, 35, 35], dtype=np.uint8)
    TITLE_BG_HI = np.array([85, 85, 85], dtype=np.uint8)
    DARK_PIXEL_THRESHOLD = 0.35   # fraction of strip pixels that must be dark

    def __init__(self, calib: CalibrationEngine, capture: ScreenCapture) -> None:
        self.calib   = calib
        self.capture = capture
        self._running = False

    def wait_for_gui(self, timeout: Optional[float] = None) -> bool:
        """
        Block until the "Afk Grinding" GUI is detected (or timeout expires).
        Returns True if detected, False if timed out.
        """
        tx, ty, tw, th = self.calib.title_strip
        deadline = (time.monotonic() + timeout) if timeout else None
        log.info("Monitoring title strip %s for Afk Grinding popup…", self.calib.title_strip)

        while True:
            if deadline and time.monotonic() > deadline:
                return False

            strip = self.capture.grab_region(tx, ty, tw, max(th, 15))
            if self._is_title_visible(strip):
                log.info("Afk Grinding GUI detected!")
                return True

            time.sleep(MONITOR_POLL_SECONDS)

    def _is_title_visible(self, strip: np.ndarray) -> bool:
        mask = cv2.inRange(strip, self.TITLE_BG_LO, self.TITLE_BG_HI)
        density = np.sum(mask > 0) / max(mask.size, 1)
        return density >= self.DARK_PIXEL_THRESHOLD


# ---------------------------------------------------------------------------
# 6. Tooltip Analyzer
#    HSV-masks a tooltip crop for both the bright-green "Click to Confirm"
#    and the bright-red "Do not click" Minecraft formatting colours.
#    A slot is only confirmed when green pixels exceed the threshold AND the
#    green-to-red confidence ratio clears GREEN_RED_RATIO_MIN, preventing
#    false-positive clicks on decoy items.
# ---------------------------------------------------------------------------

from typing import NamedTuple


class TooltipClassification(NamedTuple):
    """Result of a single tooltip scan."""
    slot_idx:     int
    green_pixels: int
    red_pixels:   int
    is_confirm:   bool   # green threshold met AND ratio clears minimum
    is_decoy:     bool   # red threshold met (regardless of green)
    confidence:   float  # green / (green + red + 1); 1.0 = pure green, 0.0 = no signal

    def __str__(self) -> str:
        verdict = "CONFIRM" if self.is_confirm else ("DECOY" if self.is_decoy else "empty")
        return (
            f"slot {self.slot_idx:02d}  "
            f"green={self.green_pixels:4d}  red={self.red_pixels:4d}  "
            f"conf={self.confidence:.2f}  → {verdict}"
        )


class TooltipAnalyzer:
    def __init__(self, capture: ScreenCapture) -> None:
        self.capture = capture
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def classify(
        self,
        slot_x: int,
        slot_y: int,
        slot_idx: int,
        save_crop: bool = True,
    ) -> TooltipClassification:
        """
        Capture a TOOLTIP_W × TOOLTIP_H crop to the right of the cursor,
        convert to HSV, and score both the green ("Click to Confirm") and
        red ("Do not click") channels.

        Returns a TooltipClassification with all scores and the final verdict.
        """
        crop_x = slot_x + 5
        crop_y = slot_y - TOOLTIP_H // 2

        crop_bgr = self.capture.grab_region(crop_x, crop_y, TOOLTIP_W, TOOLTIP_H)

        if save_crop:
            fname = str(CAPTURE_DIR / f"slot_{slot_idx:02d}.png")
            self.capture.save(crop_bgr, fname)

        crop_hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)

        # --- Green channel ---
        green_mask  = cv2.inRange(crop_hsv, GREEN_HSV_LO, GREEN_HSV_HI)
        green_count = int(np.sum(green_mask > 0))

        # --- Red channel (two HSV ranges because red wraps at hue=0) ---
        red_mask_a  = cv2.inRange(crop_hsv, RED_HSV_LO_A, RED_HSV_HI_A)
        red_mask_b  = cv2.inRange(crop_hsv, RED_HSV_LO_B, RED_HSV_HI_B)
        red_count   = int(np.sum((red_mask_a | red_mask_b) > 0))

        # --- Confidence ratio ---
        confidence  = green_count / (green_count + red_count + 1)

        # --- Verdict ---
        green_ok   = green_count >= GREEN_PIXEL_THRESHOLD
        ratio_ok   = (red_count == 0) or (green_count / max(red_count, 1) >= GREEN_RED_RATIO_MIN)
        is_confirm = green_ok and ratio_ok
        is_decoy   = red_count >= RED_PIXEL_THRESHOLD

        result = TooltipClassification(
            slot_idx=slot_idx,
            green_pixels=green_count,
            red_pixels=red_count,
            is_confirm=is_confirm,
            is_decoy=is_decoy,
            confidence=confidence,
        )
        log.debug("%s", result)
        return result

    # ------------------------------------------------------------------
    def has_green_confirm(
        self,
        slot_x: int,
        slot_y: int,
        slot_idx: int,
        save_crop: bool = True,
    ) -> bool:
        """Convenience wrapper — returns True only for a clean confirm verdict."""
        return self.classify(slot_x, slot_y, slot_idx, save_crop).is_confirm


# ---------------------------------------------------------------------------
# 7. Grid Navigator
#    Sweeps slots 0-26, calls TooltipAnalyzer after each hover settle.
# ---------------------------------------------------------------------------

class GridNavigator:
    def __init__(
        self,
        calib: CalibrationEngine,
        analyzer: TooltipAnalyzer,
        finder: WindowFinder,
    ) -> None:
        self.calib    = calib
        self.analyzer = analyzer
        self.finder   = finder

    def find_and_click_target(self) -> bool:
        """
        Sweep all 27 slots.  For each slot:
          • Score green ("Click to Confirm") and red ("Do not click") channels.
          • Skip slots whose tooltip is flagged as a decoy (red dominant).
          • Click the first slot that passes both the green threshold and the
            green-to-red confidence ratio check.
        Returns True on a confirmed click, False if no valid slot was found.
        """
        if not self.finder.focus():
            log.warning("Could not focus target window — proceeding anyway")

        best: Optional[TooltipClassification] = None

        for idx, (cx, cy) in enumerate(self.calib.slot_centers):
            log.debug("Moving to slot %02d  @ (%d, %d)", idx, cx, cy)
            _move_to(cx, cy)
            time.sleep(HOVER_SETTLE_MS / 1000.0)

            result = self.analyzer.classify(cx, cy, idx)

            if result.is_decoy and not result.is_confirm:
                log.info("Slot %02d — DECOY (red=%d, green=%d) — skipping",
                         idx, result.red_pixels, result.green_pixels)
                continue

            if result.is_confirm:
                log.info(
                    "Slot %02d — CONFIRM  green=%d  red=%d  conf=%.2f — clicking (%d, %d)",
                    idx, result.green_pixels, result.red_pixels, result.confidence, cx, cy,
                )
                _left_click(cx, cy)
                return True

            # Track the highest-confidence partial hit in case all slots fail
            if best is None or result.confidence > best.confidence:
                best = result

        # No clean confirm found — log the best candidate for diagnostics
        if best is not None:
            log.warning(
                "Sweep complete — no CONFIRM found.  "
                "Best candidate: slot %02d  green=%d  red=%d  conf=%.2f",
                best.slot_idx, best.green_pixels, best.red_pixels, best.confidence,
            )
        return False


# ---------------------------------------------------------------------------
# 8. Asset Sanitiser
#    Backs up per-run tooltip crops to a timestamped folder then clears them.
# ---------------------------------------------------------------------------

class AssetSanitiser:
    def __init__(self, capture_dir: Path = CAPTURE_DIR) -> None:
        self.capture_dir = capture_dir

    def archive_and_clear(self) -> None:
        """Move captured crops into a timestamped subdirectory."""
        pngs = list(self.capture_dir.glob("slot_*.png"))
        if not pngs:
            return

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive = self.capture_dir / f"run_{ts}"
        archive.mkdir(parents=True, exist_ok=True)

        for p in pngs:
            shutil.move(str(p), str(archive / p.name))

        log.info("Archived %d crop(s) → %s", len(pngs), archive)

    def hard_clear(self) -> None:
        """Delete all slot crops without archiving (call on failed runs)."""
        for p in self.capture_dir.glob("slot_*.png"):
            p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 9. Safe Exit helpers
# ---------------------------------------------------------------------------

def _safe_exit(reason: str, capture: ScreenCapture) -> None:
    """
    Park the cursor at the top-left safe boundary and log a diagnostic
    message before exiting.  Never leaves residual mouse-clicks pending.
    """
    log.error("SAFE EXIT — %s", reason)
    try:
        _move_to(10, 10)
    except Exception:
        pass
    sys.exit(1)


# ---------------------------------------------------------------------------
# 10. Main Solver Orchestrator
# ---------------------------------------------------------------------------

class AfkSolver:
    def __init__(self, script: Optional[str] = None) -> None:
        self.calib    = CalibrationEngine()
        self.capture  = ScreenCapture()
        self.finder   = WindowFinder()
        self.proc     = ProcessController(script=script or FARM_SCRIPT)
        self.sanitiser = AssetSanitiser()
        self.monitor: Optional[GuiMonitor] = None
        self.analyzer: Optional[TooltipAnalyzer] = None
        self.navigator: Optional[GridNavigator] = None

    # ------------------------------------------------------------------
    def setup(self) -> None:
        log.info("=== mc_afk_solver starting up ===")
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

        # Calibrate grid geometry from reference assets
        self.calib.calibrate()

        self.monitor   = GuiMonitor(self.calib, self.capture)
        self.analyzer  = TooltipAnalyzer(self.capture)
        self.navigator = GridNavigator(self.calib, self.analyzer, self.finder)

    # ------------------------------------------------------------------
    def run_forever(self) -> None:
        """Main event loop — runs until the process is killed."""
        self.setup()

        while True:
            # --- Step 1: Wait for the Afk Grinding GUI ---
            detected = self.monitor.wait_for_gui()
            if not detected:
                continue

            # --- Step 2: Halt grinding script immediately ---
            self.proc.halt_farm()
            time.sleep(0.05)   # brief yield so the bash loop notices the flag

            # --- Step 3 & 4: Sweep grid and click matching slot ---
            success = self.navigator.find_and_click_target()

            if success:
                log.info("Click dispatched — waiting %dms for UI to close", CLICK_RECOVERY_MS)
                time.sleep(CLICK_RECOVERY_MS / 1000.0)

                # --- Step 5a: Resume farm ---
                self.proc.resume_farm()

                # --- Step 5b: Archive slot crops ---
                self.sanitiser.archive_and_clear()

                log.info("Cycle complete — resuming monitor loop")

            else:
                # --- Step 5 (fail-safe): No green slot found ---
                log.error(
                    "TIMEOUT — swept all %d slots without finding a green "
                    "confirm tooltip.  Aborting input tasks.", CHEST_SLOTS
                )
                try:
                    _move_to(10, 10)
                except Exception:
                    pass

                # Clean up crops from the failed run
                self.sanitiser.hard_clear()

                # Resume the farm so the player is not left stuck
                self.proc.resume_farm()

                log.warning("Grinder resumed after failed solve — continuing monitor loop")

    # ------------------------------------------------------------------
    def run_once(self, timeout: float = 30.0) -> bool:
        """
        Single-shot mode: wait up to `timeout` seconds for the popup,
        solve it once, then return True/False.  Useful for testing.
        """
        self.setup()

        log.info("Single-shot mode (timeout=%.1fs)", timeout)
        detected = self.monitor.wait_for_gui(timeout=timeout)

        if not detected:
            log.warning("No GUI popup appeared within %.1fs", timeout)
            return False

        self.proc.halt_farm()
        time.sleep(0.05)

        success = self.navigator.find_and_click_target()

        if success:
            time.sleep(CLICK_RECOVERY_MS / 1000.0)
            self.proc.resume_farm()
            self.sanitiser.archive_and_clear()
            log.info("Single-shot solve succeeded")
        else:
            _move_to(10, 10)
            self.sanitiser.hard_clear()
            self.proc.resume_farm()
            log.error("Single-shot solve failed — no green slot found")

        return success


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="mc_afk_solver — AFK Grinding verification auto-clicker"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single detection/solve cycle and exit",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Timeout for --once mode (default: 30s)",
    )
    parser.add_argument(
        "--calibrate-only",
        action="store_true",
        help="Print calibrated geometry and exit (for debugging)",
    )
    parser.add_argument(
        "--script",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "Path to the grinding bash script to halt/resume "
            f"(default: mc_spam_2.sh next to this file)"
        ),
    )
    args = parser.parse_args()

    solver = AfkSolver(script=args.script)

    if args.calibrate_only:
        solver.calib.calibrate()
        print("\n--- Calibrated Geometry ---")
        print(f"  Screen  : {solver.calib.screen_w} × {solver.calib.screen_h}")
        print(f"  GUI left: {solver.calib.gui_left}   right: {solver.calib.gui_right}")
        print(f"  GUI top : {solver.calib.gui_top}")
        print(f"  Slot px : {solver.calib.slot_size}")
        print(f"  Title strip (x,y,w,h): {solver.calib.title_strip}")
        print(f"\n  Slot centres (0–{CHEST_SLOTS - 1}):")
        for i, (x, y) in enumerate(solver.calib.slot_centers):
            print(f"    [{i:2d}]  ({x}, {y})")
        return

    if args.once:
        ok = solver.run_once(timeout=args.timeout)
        sys.exit(0 if ok else 1)

    solver.run_forever()


if __name__ == "__main__":
    main()
