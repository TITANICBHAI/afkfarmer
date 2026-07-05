#!/usr/bin/env python3
"""
join_training_data.py
=====================
Join the script's attempt log (attached_assets/attempts.jsonl) with the
mod's ground-truth event log (afkverify_events.jsonl) to produce:

  training_data.jsonl  — one labeled record per matched popup
  weights.json         — calibrated per-backend voting weights for mc_farm.sh
  stdout               — precision / recall / F1 accuracy report

How it works
------------
The mod writes a "popup_shown" event with the real confirm_row/confirm_col
every time the AFK popup is opened, plus a "passed"/"failed"/"timeout" outcome.
The script writes one attempt record per popup with every slot's per-backend vote.

The two logs are joined by timestamp proximity (the script never talks to the
server, so it never knows the mod's popup_id).  The --tolerance flag controls
the maximum allowed gap in seconds; the default of 1.0 s covers normal latency.

After joining, for every slot that was inspected we know the ground truth:
  confirm  →  slot at (confirm_row, confirm_col)
  deny     →  every other slot

Each backend vote (color / HSV / AI / OCR / template) is then compared to
the ground truth and scored:
  TP  voted confirm, truth confirm
  FP  voted confirm, truth deny
  TN  voted deny/empty, truth deny
  FN  voted deny/empty, truth confirm

Weight formula (optimised for precision — avoiding false confirms):
  weight = max(1, min(5, round(precision * 5)))

Fallback (no mod data): if --events is omitted, user_feedback from the
attempts log is used as a weaker signal (only covers the clicked slot).

stdlib only — no pip, no install.

Usage
-----
python3 join_training_data.py \\
    --events /path/to/server/afkverify_events.jsonl \\
    [--attempts attached_assets/attempts.jsonl] \\
    [--out     attached_assets/training_data.jsonl] \\
    [--weights attached_assets/weights.json] \\
    [--tolerance 1.0] \\
    [--player  YourIGN]
"""

import argparse, json, math, os, sys, time
from collections import defaultdict

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_ATTEMPTS = os.path.join('attached_assets', 'attempts.jsonl')
DEFAULT_OUT      = os.path.join('attached_assets', 'training_data.jsonl')
DEFAULT_WEIGHTS  = os.path.join('attached_assets', 'weights.json')
DEFAULT_TOLERANCE = 1.0   # seconds
DEFAULT_KEEP_SCREENSHOTS = 10   # used only with --prune-screenshots

BACKENDS = ['color', 'hsv', 'ai', 'ocr', 'template']

# ── Hardcoded defaults (must match mc_farm.sh) ────────────────────────────────
DEFAULT_WEIGHT = {'color': 2, 'hsv': 2, 'ai': 3, 'ocr': 1, 'template': 2}
MIN_SAMPLES_TO_OVERRIDE = 10   # need ≥10 labelled slots per backend to trust the number


# ═══════════════════════════════════════════════════════════════════════════════
#  LOAD
# ═══════════════════════════════════════════════════════════════════════════════

def load_jsonl(path, label='file'):
    records = []
    try:
        with open(path) as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"  [warn] {label} line {i}: JSON error — {e}", file=sys.stderr)
    except FileNotFoundError:
        print(f"  [warn] {label} not found: {path}", file=sys.stderr)
    return records


def load_events(path):
    """
    Load mod events from afkverify_events.jsonl.

    Returns:
      shown    — list of popup_shown events (sorted by timestamp_epoch)
      outcomes — dict popup_id → outcome event (passed/failed/timeout)
    """
    raw = load_jsonl(path, 'events')
    shown    = []
    outcomes = {}
    for ev in raw:
        t = ev.get('event')
        if t == 'popup_shown':
            shown.append(ev)
        elif t in ('passed', 'failed', 'timeout'):
            pid = ev.get('popup_id')
            if pid:
                outcomes[pid] = ev
    shown.sort(key=lambda e: e.get('timestamp_epoch', 0))
    return shown, outcomes


def load_attempts(path, player=None):
    """
    Load script attempt records from attempts.jsonl.
    Optionally filter to a single player name.
    """
    raw = load_jsonl(path, 'attempts')
    if player:
        raw = [r for r in raw if r.get('player_name') == player]
    raw.sort(key=lambda r: r.get('timestamp_epoch', 0))
    return raw


# ═══════════════════════════════════════════════════════════════════════════════
#  JOIN
# ═══════════════════════════════════════════════════════════════════════════════

def join_by_timestamp(attempts, shown_events, outcomes, tolerance):
    """
    Match each attempt record to the nearest popup_shown event within
    ±tolerance seconds.  Each mod event is consumed at most once.

    Returns:
      matched   — list of (attempt, shown_event, outcome_or_None)
      unmatched — list of attempt records with no close mod event
    """
    used = set()
    matched   = []
    unmatched = []

    for attempt in attempts:
        at = attempt.get('timestamp_epoch', 0)
        best_ev   = None
        best_dist = float('inf')
        best_idx  = None

        for i, ev in enumerate(shown_events):
            if i in used:
                continue
            et = ev.get('timestamp_epoch', 0)
            dist = abs(at - et)
            if dist <= tolerance and dist < best_dist:
                best_dist = dist
                best_ev   = ev
                best_idx  = i

        if best_ev is not None:
            used.add(best_idx)
            pid     = best_ev.get('popup_id')
            outcome = outcomes.get(pid)
            matched.append((attempt, best_ev, outcome))
        else:
            unmatched.append(attempt)

    return matched, unmatched


# ═══════════════════════════════════════════════════════════════════════════════
#  SCORING
# ═══════════════════════════════════════════════════════════════════════════════

def score_backends(matched):
    """
    For every matched (attempt, shown_event) pair, iterate over all
    inspected slots and score each backend's vote against ground truth.

    Returns:
      stats — dict backend → {'tp':int, 'fp':int, 'tn':int, 'fn':int, 'abstain':int}
    """
    stats = {b: {'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0, 'abstain': 0}
             for b in BACKENDS}

    for attempt, shown, outcome in matched:
        cr = shown.get('confirm_row', -1)
        cc = shown.get('confirm_col', -1)
        if cr < 0 or cc < 0:
            continue

        for slot in attempt.get('slots_inspected', []):
            row = slot.get('row', -1)
            col = slot.get('col', -1)
            if row < 0 or col < 0:
                continue

            truth = 'confirm' if (row == cr and col == cc) else 'deny'

            votes = {
                'color'   : slot.get('color_vote'),
                'hsv'     : slot.get('hsv_vote'),
                'ai'      : slot.get('ai_vote'),
                'ocr'     : slot.get('ocr_vote'),
                'template': slot.get('template_pre'),
            }

            for backend, vote in votes.items():
                s = stats[backend]
                # Normalise: None / 'empty' / anything not confirm|deny → abstain
                if vote not in ('confirm', 'deny'):
                    s['abstain'] += 1
                    continue
                if vote == 'confirm':
                    if truth == 'confirm': s['tp'] += 1
                    else:                  s['fp'] += 1
                else:  # vote == 'deny'
                    if truth == 'deny':    s['tn'] += 1
                    else:                  s['fn'] += 1

    return stats


def score_from_feedback(attempts):
    """
    Fallback scoring when mod events are unavailable.
    Uses user_feedback='correct'/'incorrect' and the clicked slot only.
    Much weaker — can only evaluate what the script decided to click,
    not what it decided to skip.
    """
    stats = {b: {'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0, 'abstain': 0}
             for b in BACKENDS}

    for attempt in attempts:
        fb = attempt.get('user_feedback')
        if fb not in ('correct', 'incorrect'):
            continue

        clicked_slot = next(
            (s for s in attempt.get('slots_inspected', []) if s.get('clicked')),
            None
        )
        if clicked_slot is None:
            continue

        # Script clicked this slot saying 'confirm'; feedback tells us if it was right
        truth = 'confirm' if fb == 'correct' else 'deny'

        votes = {
            'color'   : clicked_slot.get('color_vote'),
            'hsv'     : clicked_slot.get('hsv_vote'),
            'ai'      : clicked_slot.get('ai_vote'),
            'ocr'     : clicked_slot.get('ocr_vote'),
            'template': clicked_slot.get('template_pre'),
        }
        for backend, vote in votes.items():
            s = stats[backend]
            if vote not in ('confirm', 'deny'):
                s['abstain'] += 1
                continue
            if vote == 'confirm':
                if truth == 'confirm': s['tp'] += 1
                else:                  s['fp'] += 1
            else:
                if truth == 'deny':    s['tn'] += 1
                else:                  s['fn'] += 1

    return stats


def _prf(s):
    """Compute precision, recall, F1 from a stats dict."""
    tp, fp, fn = s['tp'], s['fp'], s['fn']
    prec   = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    if prec is not None and recall is not None and (prec + recall) > 0:
        f1 = 2 * prec * recall / (prec + recall)
    else:
        f1 = None
    n_labelled = tp + fp + fn + s['tn']
    return prec, recall, f1, n_labelled


# ═══════════════════════════════════════════════════════════════════════════════
#  WEIGHT CALIBRATION
# ═══════════════════════════════════════════════════════════════════════════════

def calibrate_weights(stats):
    """
    Derive integer voting weights (1–5) from per-backend precision.

    We optimise for precision — the cost of a false confirm (clicking a deny
    slot and getting kicked) is much higher than a false deny (missing the
    confirm slot, which the server treats as a timeout, not a kick).

    Formula:  weight = max(1, min(5, round(precision * 5)))

    Backends with fewer than MIN_SAMPLES_TO_OVERRIDE non-abstain labels keep
    their hardcoded default weight.
    """
    weights    = {}
    accuracies = {}

    for b in BACKENDS:
        s = stats[b]
        prec, recall, f1, n = _prf(s)
        tp, fp, fn, tn = s['tp'], s['fp'], s['fn'], s['tn']
        acc = (tp + tn) / (tp + fp + fn + tn) if (tp + fp + fn + tn) > 0 else None
        accuracies[b] = {
            'precision': prec,
            'recall'   : recall,
            'f1'       : f1,
            'accuracy' : acc,
            'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
            'abstain'  : s['abstain'],
            'n_labelled': n,
        }

        n_active = tp + fp + fn + tn
        if n_active < MIN_SAMPLES_TO_OVERRIDE or prec is None:
            weights[b] = DEFAULT_WEIGHT[b]
        else:
            weights[b] = max(1, min(5, round(prec * 5)))

    return weights, accuracies


# ═══════════════════════════════════════════════════════════════════════════════
#  OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def write_weights_file(path, weights, accuracies, n_popups, mode):
    payload = {
        'schema'    : 'afk_weights_v1',
        'updated'   : time.strftime('%Y-%m-%dT%H:%M:%S'),
        'mode'      : mode,
        'n_popups'  : n_popups,
        # Flat weights consumed by mc_farm.sh
        'color'     : weights.get('color',    DEFAULT_WEIGHT['color']),
        'hsv'       : weights.get('hsv',      DEFAULT_WEIGHT['hsv']),
        'ai'        : weights.get('ai',       DEFAULT_WEIGHT['ai']),
        'ocr'       : weights.get('ocr',      DEFAULT_WEIGHT['ocr']),
        # accuracy sub-object: human-readable only, not parsed by the script
        'accuracy'  : {
            b: {
                'precision': round(v['precision'], 4) if v['precision'] is not None else None,
                'recall'   : round(v['recall'],    4) if v['recall']    is not None else None,
                'f1'       : round(v['f1'],        4) if v['f1']        is not None else None,
                'accuracy' : round(v['accuracy'],  4) if v['accuracy']  is not None else None,
                'tp': v['tp'], 'fp': v['fp'], 'fn': v['fn'], 'tn': v['tn'],
                'n_labelled': v['n_labelled'],
            }
            for b, v in accuracies.items()
        },
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(payload, f, indent=2)
    print(f"\n[ weights ] Written → {path}")


def prune_matched_screenshots(matched, attempts_dir, keep=DEFAULT_KEEP_SCREENSHOTS):
    """
    Delete popup screenshot PNGs, but ONLY for attempts that were
    successfully matched to a mod ground-truth event AND already written
    into training_data.jsonl — their pixel data has served its purpose
    once the ground-truth labels are captured there.

    Never touches:
      - screenshots for unmatched attempts (no mod event found — may still
        be joinable later against a different/later mod log export)
      - the most recent `keep` matched screenshots (kept as a rolling
        sample for manual visual sanity-checks)

    Returns the number of files deleted.
    """
    matched_shots = sorted(
        attempt.get('screenshot') for attempt, _shown, _outcome in matched
        if attempt.get('screenshot')
    )
    excess = len(matched_shots) - keep
    if excess <= 0:
        return 0
    deleted = 0
    for name in matched_shots[:excess]:
        fp = os.path.join(attempts_dir, name)
        try:
            os.remove(fp)
            deleted += 1
        except OSError:
            pass
    return deleted


def write_training_data_file(path, matched):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    n = 0
    with open(path, 'w') as f:
        for attempt, shown, outcome in matched:
            rec = {
                'attempt'        : attempt,
                'ground_truth'   : {
                    'popup_id'   : shown.get('popup_id'),
                    'confirm_row': shown.get('confirm_row'),
                    'confirm_col': shown.get('confirm_col'),
                    'confirm_slot': shown.get('confirm_slot'),
                    'deny_slots' : shown.get('deny_slots'),
                },
                'outcome_event'  : outcome,
            }
            f.write(json.dumps(rec, separators=(',', ':')) + '\n')
            n += 1
    print(f"[ training ] Written {n} records → {path}")
    return n


def print_report(weights, accuracies, n_matched, n_unmatched, mode):
    sep  = '═' * 66
    sep2 = '─' * 66
    print(f"\n{sep}")
    print(f"  AFK Farm — Backend Accuracy Report")
    print(f"  Mode       : {mode}")
    print(f"  Matched    : {n_matched} popup(s)")
    print(f"  Unmatched  : {n_unmatched} attempt(s) (no mod event within tolerance)")
    print(sep2)

    hdr = f"  {'Backend':<10} {'Prec':>6} {'Rec':>6} {'F1':>6} {'Acc':>6}  "
    hdr += f"{'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4}  {'Weight':>7}  {'Note'}"
    print(hdr)
    print(sep2)

    for b in BACKENDS:
        a = accuracies[b]
        w = weights.get(b, DEFAULT_WEIGHT.get(b, '?'))
        dw = DEFAULT_WEIGHT.get(b, '?')

        def pct(v): return f'{v:.0%}' if v is not None else '  —  '

        note = ''
        if a['n_labelled'] < MIN_SAMPLES_TO_OVERRIDE:
            note = f'(default — only {a["n_labelled"]} samples)'
        elif w != dw:
            arrow = '↑' if w > dw else '↓'
            note = f'{arrow} was {dw}'

        row = (f"  {b:<10} {pct(a['precision']):>6} {pct(a['recall']):>6} "
               f"{pct(a['f1']):>6} {pct(a['accuracy']):>6}  "
               f"{a['tp']:>4} {a['fp']:>4} {a['fn']:>4} {a['tn']:>4}  "
               f"   {w} → mc  {note}")
        print(row)

    print(sep2)
    print(f"  WEIGHT_COLOR={weights.get('color',2)}  WEIGHT_HSV={weights.get('hsv',2)}  "
          f"WEIGHT_AI={weights.get('ai',3)}  WEIGHT_OCR={weights.get('ocr',1)}")
    print(f"  (written to weights.json; mc_farm.sh loads it automatically)")
    print(sep)


def print_summary(weights, accuracies, n_matched, mode):
    """
    Compact single-line accuracy summary for quick health checks.

    Printed when --summary is passed.  No files are written.
    Format:
      [n=42 | mode] color:87% hsv:91% ai:100% ocr:74% template:89%
      weights → color=2 hsv=2 ai=3 ocr=1 template=2
    """
    def pct(v):
        return f'{v:.0%}' if v is not None else '—'

    parts = []
    for b in BACKENDS:
        a = accuracies[b]
        p = pct(a['precision'])
        w = weights.get(b, DEFAULT_WEIGHT.get(b, '?'))
        flag = ''
        if a['n_labelled'] < MIN_SAMPLES_TO_OVERRIDE:
            flag = '*'   # asterisk = not enough samples, using default weight
        parts.append(f"{b}:{p}{flag}(w={w})")

    wline = '  '.join(
        f"{b}={weights.get(b, DEFAULT_WEIGHT.get(b,'?'))}" for b in BACKENDS
    )
    sep = '─' * 66
    print(sep)
    print(f"  AFK accuracy check  [{n_matched} popup(s) | {mode}]")
    print(f"  " + "  ".join(parts))
    print(f"  weights → {wline}")
    print(f"  (* = <{MIN_SAMPLES_TO_OVERRIDE} samples, weight is hardcoded default)")
    print(sep)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description='Join script attempt log with mod events; calibrate backend weights.')
    ap.add_argument('--events',    default=None,            help='Path to afkverify_events.jsonl')
    ap.add_argument('--attempts',  default=DEFAULT_ATTEMPTS, help='Path to attempts.jsonl')
    ap.add_argument('--out',       default=DEFAULT_OUT,      help='Output training_data.jsonl path')
    ap.add_argument('--weights',   default=DEFAULT_WEIGHTS,  help='Output weights.json path')
    ap.add_argument('--tolerance', type=float, default=DEFAULT_TOLERANCE,
                    help='Max timestamp gap in seconds for joining (default 1.0)')
    ap.add_argument('--player',    default=None,
                    help='Filter attempts to this player name (optional)')
    ap.add_argument('--summary',   action='store_true',
                    help='Print a compact accuracy table to stdout and exit (no files written). '
                         'Useful for a quick mid-session health check.')
    ap.add_argument('--prune-screenshots', action='store_true',
                    help='After a successful join, delete popup screenshot PNGs for attempts '
                         'that were matched to a mod event and written into training_data.jsonl, '
                         f'keeping the {DEFAULT_KEEP_SCREENSHOTS} most recent. Unmatched attempts '
                         'are never pruned — their ground truth is not captured anywhere yet. '
                         'Off by default; nothing is ever deleted unless you pass this flag.')
    ap.add_argument('--keep-screenshots', type=int, default=DEFAULT_KEEP_SCREENSHOTS,
                    help=f'How many matched screenshots to keep when --prune-screenshots is set '
                         f'(default {DEFAULT_KEEP_SCREENSHOTS})')
    args = ap.parse_args()

    # ── Load attempts ─────────────────────────────────────────────────────────
    attempts = load_attempts(args.attempts, player=args.player)
    if not attempts:
        print(f"No attempt records found in {args.attempts} — nothing to do.")
        return

    print(f"[ load ] {len(attempts)} attempt record(s) from {args.attempts}")
    if args.player:
        print(f"[ load ] filtered to player: {args.player}")

    # ── Mode A: full join with mod event log ──────────────────────────────────
    if args.events:
        shown, outcomes = load_events(args.events)
        print(f"[ load ] {len(shown)} popup_shown event(s)  "
              f"{len(outcomes)} outcome event(s) from {args.events}")

        matched, unmatched = join_by_timestamp(
            attempts, shown, outcomes, tolerance=args.tolerance)
        print(f"[ join ] {len(matched)} matched  {len(unmatched)} unmatched "
              f"(tolerance={args.tolerance}s)")

        if not matched:
            print("No matches — check that both logs come from the same session "
                  "and try --tolerance 2.0 or higher.")
            return

        stats = score_backends(matched)
        mode  = 'mod_events'
        n_matched   = len(matched)
        n_unmatched = len(unmatched)

        if not args.summary:
            write_training_data_file(args.out, matched)

            if args.prune_screenshots:
                attempts_dir = os.path.dirname(os.path.abspath(args.attempts))
                deleted = prune_matched_screenshots(
                    matched, attempts_dir, keep=args.keep_screenshots)
                print(f"[ prune ] deleted {deleted} matched screenshot(s) "
                      f"(kept {args.keep_screenshots} most recent matched + "
                      f"all unmatched — their ground truth isn't saved anywhere yet)")

    # ── Mode B: user-feedback fallback (no mod events) ────────────────────────
    else:
        print("[ mode ] No --events file; falling back to user_feedback labels.")
        print("         Accuracy estimates are weaker (clicked slots only).")
        stats = score_from_feedback(attempts)
        mode  = 'user_feedback'
        matched     = []
        n_matched   = sum(1 for a in attempts if a.get('user_feedback') in ('correct','incorrect'))
        n_unmatched = len(attempts) - n_matched

    # ── Calibrate weights ─────────────────────────────────────────────────────
    weights, accuracies = calibrate_weights(stats)

    # ── Write outputs / print report ──────────────────────────────────────────
    if args.summary:
        # --summary: compact stdout table only, no file writes
        print_summary(weights, accuracies, n_matched, mode)
    else:
        write_weights_file(args.weights, weights, accuracies, n_matched, mode)
        print_report(weights, accuracies, n_matched, n_unmatched, mode)


if __name__ == '__main__':
    main()
