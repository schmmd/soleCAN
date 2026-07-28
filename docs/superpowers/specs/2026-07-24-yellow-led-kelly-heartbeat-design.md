# Yellow LED as Kelly heartbeat — design

**Date:** 2026-07-24
**Target:** `esp32-s3` firmware, RejsaCAN board only (`LED_IS_DUAL_GPIO`)
**Status:** approved design, pre-implementation

## Goal

Repurpose the RejsaCAN yellow LED (`WARN_LED_PIN`, GPIO 11) so it does useful
work during normal operation. Today it is a warning channel that fires only at
boot-time faults, so on a healthy device it is dark essentially forever. Make it
the **Kelly serial heartbeat** — the twin of the blue **CAN heartbeat** — on
builds where the Kelly monitor is compiled in, while preserving the one warning
worth keeping.

## Background

On the dual-LED RejsaCAN board the three LEDs are:

- **Green** — hard-wired power indicator; firmware can't drive it. Lit whenever
  the board has power, including deep sleep.
- **Blue** (`ACTIVITY_LED_PIN`, GPIO 10) — CAN activity: flickers while J1939
  frames arrive (`updateLed()`, driven by `g_last_frame_ms`).
- **Yellow** (`WARN_LED_PIN`, GPIO 11) — warning channel. Two states, both rare
  and boot-time: fast blink = CAN driver failed to init; slow blink = no WiFi
  (AP failed *and* STA not connected).

The Kelly e-hydraulic pump monitor is opt-in (`-DENABLE_KELLY`) and RejsaCAN-only
(`ENABLE_KELLY` `#error`s on any other board). It polls the Kelly KLS controller
on UART1 and folds telemetry into `/json`. Its liveness is currently observable
only by reading `kelly_dbg` counters in `/json` — there is no at-a-glance signal
that the serial link is up and streaming.

Yellow is the natural home for that signal: Kelly is RejsaCAN-only, and RejsaCAN
is exactly the board that has a separate yellow LED to spare.

## Decision

- **Drop CAN-init-fail** from yellow entirely. It was redundant: if CAN never
  initializes, blue never blinks and `can.initialized` is already exposed in
  `/json` and on the serial console.
- **Preserve No-WiFi** slow-blink as a warning on all dual-LED builds.
- **On `ENABLE_KELLY` builds**, yellow additionally becomes the Kelly heartbeat.
- **Priority when both apply — No-WiFi wins.** WiFi-down and Kelly-streaming are
  unrelated and can be true simultaneously. When both are true, yellow
  slow-blinks the No-WiFi warning; the Kelly heartbeat shows only when WiFi is
  up (the normal operating case, so the heartbeat is visible essentially all the
  time). This honors "preserve No-WiFi": the warning is never masked. An
  intentionally-headless Kelly rig (WiFi off on purpose) shows the warning
  instead of the heartbeat, which is acceptable.

## Behavior

Applies to `LED_IS_DUAL_GPIO` (RejsaCAN). Blue and green are unchanged.

**Non-Kelly builds** (yellow, priority order top to bottom):

| Condition | Yellow |
|---|---|
| No WiFi (`!g_ap_running && WiFi.status() != WL_CONNECTED`) | slow blink |
| otherwise | off |

**Kelly builds** (`#if defined(ENABLE_KELLY)`, priority order):

| Condition | Yellow |
|---|---|
| No WiFi | slow blink |
| Kelly frame arriving | activity flicker |
| otherwise | off |

CAN-init-fail no longer drives yellow in either build.

### Timing / thresholds

- No-WiFi slow blink: existing `WARN_BLINK_SLOW_MS` (500 ms), unchanged.
- Kelly activity flicker: mirror the blue channel — toggle every `LED_BLINK_MS`
  (50 ms) while active, where "active" means a Kelly frame arrived within
  `LED_ACTIVE_MS` (200 ms).

### Kelly "activity" source

Trigger on **every good checksummed frame**, not only full 48-byte block
decodes — a frame-level heartbeat flickers continuously while the link streams
and better reflects "serial link is alive." Use the millis timestamp of the last
good frame. `g_kelly_frames_ok` is the existing good-frame counter; if no
millis timestamp for it exists yet, add one (`g_kelly_last_frame_ms`, set
wherever `g_kelly_frames_ok` is incremented). `g_kelly_last_block_ms` (last full
block) already exists but is block-granular, so it is not the chosen source.

## Implementation

Single function, `updateLed()` in `esp32-s3/src/main.cpp`, `LED_IS_DUAL_GPIO`
branch. The yellow (warn) section computes `warn_period_ms`:

- Remove the `if (!g_can_initialized) warn_period_ms = WARN_BLINK_FAST_MS;`
  clause (drops CAN-init-fail).
- Keep the No-WiFi clause setting `warn_period_ms = WARN_BLINK_SLOW_MS`.
- When `warn_period_ms == 0` (WiFi OK) and `ENABLE_KELLY` is defined, drive
  yellow from the Kelly activity timer instead of forcing it LOW — same
  active/toggle pattern the blue channel uses, against `g_kelly_last_frame_ms`.
- When `warn_period_ms > 0`, the No-WiFi blink runs as today and takes the pin
  (Kelly heartbeat suppressed) — this is the "No-WiFi wins" priority.

No new mutex or task; `updateLed()` already runs on the loop task and reads these
globals directly. Non-`LED_IS_DUAL_GPIO` boards (NeoPixel Feather, no-LED
LilyGo) are untouched.

## Documentation

Update the LED status legend comment block above `updateLed()` in
`main.cpp` (the "Dual-LED boards" section) to reflect: yellow slow blink = no
WiFi; yellow flicker = Kelly traffic (Kelly builds); CAN-init-fail removed. If
the RejsaCAN LED behavior is described in `esp32-s3/README.md`, update it to
match.

## Testing

No CI/unit tests exist for the firmware; validation is on the bench. The
existing `esp32-s3/device-test.py` does not cover LED state and this change does
not add a harness for it. Manual bench verification on a Kelly-wired RejsaCAN:

1. WiFi up, Kelly streaming → yellow flickers with the serial traffic; blue
   flickers with CAN independently.
2. Kelly disconnected (no frames) → yellow off (WiFi up).
3. WiFi down (bad creds / out of range), Kelly streaming → yellow slow-blinks
   the No-WiFi warning; heartbeat suppressed. Restore WiFi → flicker returns.
4. Non-Kelly build → yellow slow-blinks only on No-WiFi, otherwise off;
   CAN-init-fail no longer blinks yellow.
