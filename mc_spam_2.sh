#!/bin/bash
# mc_spam_2.sh — Active grinding loop for JartexNetwork OneBlock.
#
# IPC contract:
#   Start : touch /tmp/mc_spamming  (done by this script on first run)
#   Halt  : rm /tmp/mc_spamming     (done by mc_afk_solver.py on GUI intercept)
#   Resume: touch /tmp/mc_spamming  (done by mc_afk_solver.py after solve)
#           bash mc_spam_2.sh       (re-launched by solver in background)
#
# Toggle: running the script a second time while active stops it.

FLAG_FILE="/tmp/mc_spamming"
LOG_FILE="/tmp/mc_spam_2.log"

_ts() { date '+%H:%M:%S'; }
_log() { echo "$(_ts)  $*" | tee -a "$LOG_FILE"; }

# ---------------------------------------------------------------------------
# Toggle guard — second invocation stops a running session
# ---------------------------------------------------------------------------
if [ -f "$FLAG_FILE" ]; then
    rm "$FLAG_FILE"
    _log "STOP  flag removed — grinding loop will exit on next tick"
    exit 0
fi

touch "$FLAG_FILE"
_log "START grinding loop (PID $$)"

# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------
START_TIME=$SECONDS
LAST_ROTATE=$SECONDS
LAST_VIBRATE=$SECONDS
LAST_JUMP=$SECONDS

NEXT_ROTATE_INTERVAL=$((35 + RANDOM % 40))
NEXT_VIBRATE_INTERVAL=$((18 + RANDOM % 22))
NEXT_JUMP_INTERVAL=$((45 + RANDOM % 60))

# Short initial settle — gives the solver time to start its monitor loop
sleep 0.8

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
while [ -f "$FLAG_FILE" ]; do

    # --- FATIGUE MODEL ---
    # Swing delay grows slowly with elapsed time to mimic human tiredness.
    ELAPSED=$((SECONDS - START_TIME))
    FATIGUE_DELAY=$(( (ELAPSED / 60) * (RANDOM % 12) ))
    if [ $FATIGUE_DELAY -gt 75 ]; then
        FATIGUE_DELAY=75
    fi

    ROLL=$((RANDOM % 100))

    # --- ACTION 1: SMOOTH CAMERA DRIFT (≈3% chance, throttled) ---
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

        # Occasionally reset fatigue clock so sessions feel irregular
        [ $((RANDOM % 8)) -eq 0 ] && START_TIME=$SECONDS

        sleep $(awk -v ms="$((90 + RANDOM % 130))" 'BEGIN{print ms/1000}')
        continue
    fi

    # --- ACTION 2: MICRO-VIBRATION (≈5% chance, throttled) ---
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

    # --- ACTION 3: OCCASIONAL JUMP (≈2% chance, throttled) ---
    if [ $ROLL -ge 8 ] && [ $ROLL -lt 10 ] && \
       [ $((SECONDS - LAST_JUMP)) -ge $NEXT_JUMP_INTERVAL ]; then
        [ -f "$FLAG_FILE" ] || continue
        xdotool key space
        LAST_JUMP=$SECONDS
        NEXT_JUMP_INTERVAL=$((45 + RANDOM % 60))
        sleep $(awk -v ms="$((200 + RANDOM % 150))" 'BEGIN{print ms/1000}')
        continue
    fi

    # --- ACTION 4: STANDARD LEFT-CLICK ATTACK (default path) ---
    [ -f "$FLAG_FILE" ] || break

    xdotool mousedown 1
    hold_ms=$((28 + RANDOM % 38))
    sleep $(awk -v ms="$hold_ms" 'BEGIN{print ms/1000}')
    xdotool mouseup 1

    swing_ms=$((620 + RANDOM % 55 + FATIGUE_DELAY))

    # Occasional deliberate micro-pause between swings
    if [ $((RANDOM % 12)) -eq 0 ]; then
        swing_ms=$((swing_ms + 50 + RANDOM % 90))
    fi

    sleep $(awk -v ms="$swing_ms" 'BEGIN{print ms/1000}')

done

_log "STOP  flag absent — grinding loop exited cleanly"
