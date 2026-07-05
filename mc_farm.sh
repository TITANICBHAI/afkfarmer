#!/bin/bash
# Flags to control the loop
FLAG_FILE="/tmp/mc_spamming"

if [ -f "$FLAG_FILE" ]; then
    rm "$FLAG_FILE"
    echo "Spamming STOPPED"
else
    touch "$FLAG_FILE"
    echo "Spamming STARTED"
    
    sleep 1 
    
    START_TIME=$SECONDS
    LAST_ROTATE=$SECONDS
    LAST_VIBRATE=$SECONDS
    
    NEXT_ROTATE_INTERVAL=$((30 + RANDOM % 45))
    NEXT_VIBRATE_INTERVAL=$((15 + RANDOM % 25))

    while [ -f "$FLAG_FILE" ]; do
        
        # --- CALCULATE FATIGUE ---
        ELAPSED=$((SECONDS - START_TIME))
        FATIGUE_DELAY=$(( (ELAPSED / 60) * (RANDOM % 15) ))
        
        if [ $FATIGUE_DELAY -gt 80 ]; then 
            FATIGUE_DELAY=80
        fi

        BEHAVIOR_ROLL=$((RANDOM % 100))

        # --- ACTION 1: SMOOTH CAMERA ROTATION ---
        if [ $BEHAVIOR_ROLL -lt 3 ] && [ $((SECONDS - LAST_ROTATE)) -ge $NEXT_ROTATE_INTERVAL ]; then
            rot_x=$(( (RANDOM % 201) - 100 ))
            rot_y=$(( (RANDOM % 41) - 20 ))
            
            steps=5
            for ((s=0; s<steps; s++)); do
                xdotool mousemove_relative -- $((rot_x / steps)) $((rot_y / steps))
                sleep 0.01
            done
            
            LAST_ROTATE=$SECONDS
            NEXT_ROTATE_INTERVAL=$((30 + RANDOM % 45))
            
            if [ $((RANDOM % 10)) -eq 0 ]; then
                START_TIME=$SECONDS
            fi
            
            sleep $(awk -v ms="$((100 + RANDOM % 150))" 'BEGIN {print ms / 1000}')
            continue
        fi

        # --- ACTION 2: SCREEN VIBRATION ---
        if [ $BEHAVIOR_ROLL -ge 3 ] && [ $BEHAVIOR_ROLL -lt 8 ] && [ $((SECONDS - LAST_VIBRATE)) -ge $NEXT_VIBRATE_INTERVAL ]; then
            vib_cycles=$((2 + RANDOM % 4))
            for ((i=0; i<vib_cycles; i++)); do
                shk_x=$(( (RANDOM % 7) - 3 ))
                shk_y=$(( (RANDOM % 7) - 3 ))
                xdotool mousemove_relative -- $shk_x $shk_y
                sleep 0.02
                xdotool mousemove_relative -- $(( -shk_x )) $(( -shk_y ))
            done
            
            LAST_VIBRATE=$SECONDS
            NEXT_VIBRATE_INTERVAL=$((15 + RANDOM % 25))
            continue
        fi

        # --- ACTION 3: STANDARD ATTACK ---
        xdotool mousedown 1
        hold_ms=$((30 + RANDOM % 41))
        sleep $(awk -v ms="$hold_ms" 'BEGIN {print ms / 1000}')
        xdotool mouseup 1
        
        swing_ms=$((625 + RANDOM % 51 + FATIGUE_DELAY))
        
        if [ $((RANDOM % 10)) -eq 0 ]; then
            pause_ms=$((50 + RANDOM % 101))
            swing_ms=$((swing_ms + pause_ms))
        fi
        
        sleep_time=$(awk -v ms="$swing_ms" 'BEGIN {print ms / 1000}')
        sleep "$sleep_time"
    done
fi
