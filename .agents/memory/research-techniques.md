---
name: Research techniques in mc_farm.sh
description: 10 web-researched GUI automation techniques added to the bash section of mc_farm.sh; critical implementation gotchas.
---

## What was added
A 480-line section between Python solver launch and the spam loop (~line 1716 onward in mc_farm.sh) containing 10 research-sourced bash functions plus upgrades to all 3 spam loop actions.

## Critical gotchas (bugs that were caught and fixed)

### ImageMagick txt: field indices
`convert img.png txt:-` produces lines like `x,y: (R,G,B,A)  #RRGGBB  name`.
With `awk -F'[:(,) ]+'`: $1=x, $2=y, **$3=R, $4=G, $5=B**, $6=A.
Using $4,$5,$6 gives G,B,A — wrong. Always use $3,$4,$5 for RGB.

### xdotool --polar semantics
`xdotool mousemove_relative --polar angle distance` is a DIRECTIONAL move (angle=0 means up, clockwise). It is NOT arc-around-a-point.
For camera panning: choose a fixed heading once, then vary the per-step distance (cosine-eased). Do NOT pass delta angles each step — that produces incoherent drift.

### getmouselocation race condition
Two separate `xdotool getmouselocation` calls can sample different positions if the pointer moves between them. Always capture to one variable, then parse both X and Y from that string.

### lognormal Box-Muller correctness
The formula is correct: `u1=rand(); u2=rand(); z=sqrt(-2*log(u1))*cos(2π*u2); v=exp(mu+sigma*z)`.
Clamp v to [0.004, 0.200] (4ms–200ms). Safe to call inside while-read pipes because each awk invocation seeds from shell $RANDOM.

## Sources
- vincentbavitz/bezmouse, Vinyzu/cursory (Bézier)
- Pointergeist/PHC-mouse-movement-gen (sigma-lognormal)
- IEEE "Bot Detection Using Mouse Movements" 2023 (lognormal timing, overshoot)
- xdotool(1) man page (--polar, --sync, --window, restore, chaining)
- world-playground-deceit.net/blog/2025/07/… (xinput test-xi2 record/replay)
- superuser.com/q/576949, SO/q/27359798 (ImageMagick txt: pixel format)
- imagemagick.org/script/compare.php (compare -metric MAE)
