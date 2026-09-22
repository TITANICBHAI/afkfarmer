# Deferred Visual Fallback

## Status

Planned only. OCR, computer vision, and pixel/perceptual comparison are not
implemented in the current automation.

## Purpose

The supplied screenshots can provide a second source of evidence when a target
Android build exposes incomplete or unstable UI Automator nodes. This fallback
must support diagnosis and confidence checking without replacing semantic UI
state as the source of truth.

## Planned evidence order

1. UI Automator XML: resource ID, exact text, content description, enabled and
   clickable state, and live node bounds.
2. Screenshot evidence: a timestamped capture saved for the transition or
   failure.
3. Optional visual fallback:
   - OCR for required visible labels;
   - computer-vision landmarks for dialogs, banners, buttons, or selected
     controls;
   - tolerant color/region or perceptual checks for distinctive visual cues.

The automation must never tap, advance a stage, or report success solely
because a visual match passed. A disagreement between XML and visual evidence
must remain a failure or require manual takeover.

## Planned state specification

Each future visual check should reference:

- the semantic state name;
- the supplied reference image, when one exists;
- required OCR strings;
- optional visual regions or landmarks;
- scale, keyboard, status-bar, and color tolerances;
- a confidence threshold and the action for low confidence.

Reference images are guides, not exact pixel templates. Device resolution,
font scale, navigation mode, keyboard visibility, app version, and transient
loading indicators can all change the screenshot.

## Planned test and privacy boundaries

- Unit tests will use synthetic screenshots or cropped fixtures for OCR and
  region logic rather than contacting a device.
- Visual checks will be tested as secondary evidence against the semantic
  classifier.
- Runtime screenshots and OCR output may contain account identifiers and must
  stay in protected runtime evidence paths.
- Dependencies will not be added until device evidence shows that XML
  observation is insufficient and the chosen technique is justified.