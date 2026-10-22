#!/bin/bash

# Mouse button: 1 = Left Click (Breaks blocks), 3 = Right Click
BUTTON=1

LOCK_FILE="/tmp/mc_breaker.lock"

# Toggle logic: if a live instance exists, release the button, remove the lock
# file and stop. A stale lock (its process is gone) is cleaned up and the button
# released, then the script starts normally instead of needing a second press.
if [ -f "$LOCK_FILE" ]; then
    PID=$(cat "$LOCK_FILE" 2>/dev/null)
    rm -f "$LOCK_FILE"
    xdotool mouseup "$BUTTON"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID" 2>/dev/null
        exit 0
    fi
fi

# Save Process ID to lock file
echo $$ > "$LOCK_FILE"

# Hold the mouse button down
xdotool mousedown "$BUTTON"

# Keep process alive until toggled off
while true; do
    sleep 1
done
