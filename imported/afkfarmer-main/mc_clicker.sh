#!/bin/bash

# --- CONFIGURATION ---
BASE_DELAY=150      # Base timing in ms (0.625s + buffer)
MIN_JITTER=-10      # Random timing variability
MAX_JITTER=35
BUTTON=1            # 3 = Right Click, 1 = Left Click

LOCK_FILE="/tmp/mc_clicker.lock"

# Random integer in [min, max]. `shuf -i` rejects negative ranges, which
# silently disabled the jitter and the mouse offsets.
rand_between() {
    local min=$1 max=$2
    echo $(( RANDOM % (max - min + 1) + min ))
}

# Toggle logic: if a live instance exists, stop it and exit. A lock file whose
# process is gone (crash, reboot) is stale: remove it and start normally instead
# of wasting a keypress on "stopping" nothing.
if [ -f "$LOCK_FILE" ]; then
    PID=$(cat "$LOCK_FILE" 2>/dev/null)
    rm -f "$LOCK_FILE"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID" 2>/dev/null
        exit 0
    fi
fi

# Save Process ID to lock file
echo $$ > "$LOCK_FILE"

COUNTER=0
OFFSET_X=0
OFFSET_Y=0

while true; do
    # Perform click
    xdotool click "$BUTTON"

    # Every ~8 clicks (roughly 5 seconds at ~0.65s per click)
    COUNTER=$((COUNTER + 1))
    if [ $COUNTER -ge 8 ]; then
        if [ $OFFSET_X -eq 0 ] && [ $OFFSET_Y -eq 0 ]; then
            # Generate a small random offset (-3 to +3 pixels)
            OFFSET_X=$(rand_between -3 3)
            OFFSET_Y=$(rand_between -2 2)
            xdotool mousemove_relative -- $OFFSET_X $OFFSET_Y
        else
            # Move back to initial position by reversing the offset
            RETURN_X=$(( -1 * OFFSET_X ))
            RETURN_Y=$(( -1 * OFFSET_Y ))
            xdotool mousemove_relative -- $RETURN_X $RETURN_Y

            # Reset offsets and counter
            OFFSET_X=0
            OFFSET_Y=0
            COUNTER=0
        fi
    fi

    # Calculate timing jitter
    JITTER=$(rand_between "$MIN_JITTER" "$MAX_JITTER")
    SLEEP_MS=$((BASE_DELAY + JITTER))
    SLEEP_SEC=$(awk -v ms="$SLEEP_MS" 'BEGIN { printf "%.3f", ms / 1000 }')

    sleep "$SLEEP_SEC"
done
