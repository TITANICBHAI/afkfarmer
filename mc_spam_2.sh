#!/bin/bash
# mc_spam_2.sh — Single entry point for the full AFK-farm suite.
#
# ONE command starts everything:
#   bash mc_spam_2.sh          → launches the AFK solver + the grinding loop
#   bash mc_spam_2.sh          → (run again while active) stops both cleanly
#
# IPC:
#   /tmp/mc_spamming   — flag file; solver deletes it to pause the grinder,
#                        recreates it to resume.
#   /tmp/mc_solver.pid — solver PID; used to stop it on toggle-off.
#
# Environment:
#   MC_GRINDER_ONLY=1  — set by the solver when it relaunches the grinder
#                        after a solve; prevents a second solver from spawning.
#   ANTHROPIC_API_KEY  — optional; forwarded to the solver for AI detection.

FLAG_FILE="/tmp/mc_spamming"
SOLVER_PID_FILE="/tmp/mc_solver.pid"
SOLVER_LOG="/tmp/mc_afk_solver.log"
GRIND_LOG="/tmp/mc_spam_2.log"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOLVER_SCRIPT="$SCRIPT_DIR/mc_afk_solver.py"

_ts()  { date '+%H:%M:%S'; }
_log() { echo "$(_ts)  $*" | tee -a "$GRIND_LOG"; }

# ── Dependency check ─────────────────────────────────────────────────────────
_check_deps() {
    local missing=()
    command -v xdotool  &>/dev/null || missing+=("xdotool")
    command -v python3  &>/dev/null || missing+=("python3")
    python3 -c "import mss, cv2, numpy" &>/dev/null || \
        missing+=("python-deps (run: pip install mss opencv-python numpy pyautogui)")

    if [ ${#missing[@]} -gt 0 ]; then
        echo "ERROR: missing dependencies:"
        for m in "${missing[@]}"; do echo "  • $m"; done
        echo ""
        echo "Run the one-time setup:"
        echo "  sudo apt install -y xdotool"
        echo "  pip install mss opencv-python numpy pyautogui"
        exit 1
    fi
}

# ── STOP path ────────────────────────────────────────────────────────────────
if [ -f "$FLAG_FILE" ]; then
    rm -f "$FLAG_FILE"
    _log "STOP  grinding flag removed"

    if [ -f "$SOLVER_PID_FILE" ]; then
        SPID=$(cat "$SOLVER_PID_FILE")
        if kill -0 "$SPID" 2>/dev/null; then
            kill "$SPID"
            _log "STOP  solver (PID $SPID) terminated"
        fi
        rm -f "$SOLVER_PID_FILE"
    fi

    _log "STOP  all processes halted — session ended"
    exit 0
fi

# ── START path ───────────────────────────────────────────────────────────────
_check_deps

touch "$FLAG_FILE"
_log "START session (grinder PID $$)"

# Launch the AFK solver in the background — but only if this is the initial
# start, NOT a solver-triggered grinder resume (MC_GRINDER_ONLY=1).
if [ -z "${MC_GRINDER_ONLY:-}" ] && [ -f "$SOLVER_SCRIPT" ]; then
    python3 "$SOLVER_SCRIPT" >> "$SOLVER_LOG" 2>&1 &
    SOLVER_PID=$!
    echo "$SOLVER_PID" > "$SOLVER_PID_FILE"
    _log "START solver launched (PID $SOLVER_PID) — log: $SOLVER_LOG"
elif [ -n "${MC_GRINDER_ONLY:-}" ]; then
    _log "RESUME grinder only (solver still running)"
else
    _log "WARN  solver script not found at $SOLVER_SCRIPT — running grinder only"
fi

# Short initial settle — gives the solver time to complete calibration
# before the grinder starts sending mouse events.
sleep 1.2

# ── Runtime state ─────────────────────────────────────────────────────────────
START_TIME=$SECONDS
LAST_ROTATE=$SECONDS
LAST_VIBRATE=$SECONDS
LAST_JUMP=$SECONDS

NEXT_ROTATE_INTERVAL=$((35 + RANDOM % 40))
NEXT_VIBRATE_INTERVAL=$((18 + RANDOM % 22))
NEXT_JUMP_INTERVAL=$((45 + RANDOM % 60))

# ── Main grinding loop ────────────────────────────────────────────────────────
while [ -f "$FLAG_FILE" ]; do

    # --- FATIGUE MODEL ---
    ELAPSED=$((SECONDS - START_TIME))
    FATIGUE_DELAY=$(( (ELAPSED / 60) * (RANDOM % 12) ))
    [ $FATIGUE_DELAY -gt 75 ] && FATIGUE_DELAY=75

    ROLL=$((RANDOM % 100))

    # --- ACTION 1: SMOOTH CAMERA DRIFT (≈3%, throttled) ---
    if [ $ROLL -lt 3 ] && [ $((SECONDS - LAST_ROTATE)) -ge $NEXT_ROTATE_INTERVAL ]; then
        rot_x=$(( (RANDOM % 181) - 90 ))
        rot_y=$(( (RANDOM % 31) - 15 ))
        steps=$((3 + RANDOM % 4))
        step_x=$((rot_x / steps))
        step_y=$((rot_y / steps))

        for ((s=0; s<steps; s++)); do
            [ -f "$FLAG_FILE" ] || break
            xdotool mousemove_relative -- $step_x $step_y
            sleep 0.012
        done

        LAST_ROTATE=$SECONDS
        NEXT_ROTATE_INTERVAL=$((35 + RANDOM % 40))
        [ $((RANDOM % 8)) -eq 0 ] && START_TIME=$SECONDS
        sleep $(awk -v ms="$((90 + RANDOM % 130))" 'BEGIN{print ms/1000}')
        continue
    fi

    # --- ACTION 2: MICRO-VIBRATION (≈5%, throttled) ---
    if [ $ROLL -ge 3 ] && [ $ROLL -lt 8 ] && \
       [ $((SECONDS - LAST_VIBRATE)) -ge $NEXT_VIBRATE_INTERVAL ]; then
        cycles=$((2 + RANDOM % 3))
        for ((i=0; i<cycles; i++)); do
            [ -f "$FLAG_FILE" ] || break
            dx=$(( (RANDOM % 5) - 2 ))
            dy=$(( (RANDOM % 5) - 2 ))
            xdotool mousemove_relative -- $dx $dy
            sleep 0.018
            xdotool mousemove_relative -- $(( -dx )) $(( -dy ))
        done
        LAST_VIBRATE=$SECONDS
        NEXT_VIBRATE_INTERVAL=$((18 + RANDOM % 22))
        continue
    fi

    # --- ACTION 3: OCCASIONAL JUMP (≈2%, throttled) ---
    if [ $ROLL -ge 8 ] && [ $ROLL -lt 10 ] && \
       [ $((SECONDS - LAST_JUMP)) -ge $NEXT_JUMP_INTERVAL ]; then
        [ -f "$FLAG_FILE" ] || continue
        xdotool key space
        LAST_JUMP=$SECONDS
        NEXT_JUMP_INTERVAL=$((45 + RANDOM % 60))
        sleep $(awk -v ms="$((200 + RANDOM % 150))" 'BEGIN{print ms/1000}')
        continue
    fi

    # --- ACTION 4: STANDARD LEFT-CLICK ATTACK (default) ---
    [ -f "$FLAG_FILE" ] || break

    xdotool mousedown 1
    hold_ms=$((28 + RANDOM % 38))
    sleep $(awk -v ms="$hold_ms" 'BEGIN{print ms/1000}')
    xdotool mouseup 1

    swing_ms=$((620 + RANDOM % 55 + FATIGUE_DELAY))
    [ $((RANDOM % 12)) -eq 0 ] && swing_ms=$((swing_ms + 50 + RANDOM % 90))
    sleep $(awk -v ms="$swing_ms" 'BEGIN{print ms/1000}')

done

_log "STOP  grinding loop exited cleanly"
