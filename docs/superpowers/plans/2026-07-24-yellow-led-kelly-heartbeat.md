# Yellow LED as Kelly Heartbeat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On RejsaCAN builds, repurpose the yellow LED so it flickers as a Kelly serial heartbeat (twin of the blue CAN heartbeat), while dropping the redundant CAN-init-fail warning and preserving the No-WiFi warning.

**Architecture:** A single decode point already increments `g_kelly_frames_ok` per good 16-byte Kelly reply. Add a millis timestamp there. Then rework the yellow-channel logic in `updateLed()` (the `LED_IS_DUAL_GPIO` branch) so it: (1) no longer blinks for CAN-init-fail, (2) still slow-blinks for No-WiFi with top priority, (3) otherwise, on `ENABLE_KELLY` builds, mirrors the blue channel's active/toggle pattern against the new Kelly timestamp.

**Tech Stack:** C++ (Arduino-ESP32 / ESP-IDF TWAI), PlatformIO. Single file: `esp32-s3/src/main.cpp`. Docs in `esp32-s3/README.md`.

## Global Constraints

- **Board scope:** All firmware behavior changes are inside the `#if LED_IS_DUAL_GPIO` branch of `updateLed()`. The NeoPixel (Feather) and no-LED (LilyGo) branches must remain byte-for-byte unchanged.
- **Kelly is opt-in and RejsaCAN-only:** `ENABLE_KELLY` `#error`s on non-RejsaCAN boards. All references to Kelly globals (`g_kelly_frames_ok`, `g_kelly_last_frame_ms`) inside `updateLed()` MUST be wrapped in `#if defined(ENABLE_KELLY)`, because those globals only exist under that guard.
- **No-WiFi wins:** When WiFi is down AND Kelly is streaming, yellow shows the No-WiFi slow blink; the Kelly heartbeat is only reachable when `warn_period_ms == 0`.
- **Timing constants (already defined, reuse verbatim):** `WARN_BLINK_SLOW_MS` (500), `LED_BLINK_MS` (50), `LED_ACTIVE_MS` (200). Do not introduce new constants.
- **No unit-test harness exists for the firmware.** Verification is a successful compile of the affected build envs plus a manual bench checklist. Do not invent a test framework.
- **Preserve confidence markers and comment style** of the surrounding code.

---

### Task 1: Firmware — yellow LED becomes the Kelly heartbeat

**Files:**
- Modify: `esp32-s3/src/main.cpp` — Kelly globals block (~line 402-417), Kelly decode point (`main.cpp:1458`), `updateLed()` yellow section (`main.cpp:934-955`), and the LED legend comment block (`main.cpp:887-892`).

**Interfaces:**
- Consumes (existing globals): `g_kelly_frames_ok` (`uint32_t`, incremented per good reply), `g_ap_running` (`bool`), `WiFi.status()`, `WARN_LED_PIN`, `WARN_BLINK_SLOW_MS`, `LED_BLINK_MS`, `LED_ACTIVE_MS`.
- Produces: new global `uint32_t g_kelly_last_frame_ms` (millis of last good Kelly reply; `0` = none yet), defined under `#if defined(ENABLE_KELLY)`.

- [ ] **Step 1: Add the `g_kelly_last_frame_ms` global**

In `esp32-s3/src/main.cpp`, the Kelly debug-counter block declares (around line 416):

```cpp
uint32_t    g_kelly_blocks_ok   = 0;      // full 48-byte blocks decoded (a card update)
uint32_t    g_kelly_last_block_ms    = 0; // millis() of last full decode
```

Immediately after `g_kelly_last_block_ms`, add:

```cpp
uint32_t    g_kelly_last_frame_ms    = 0; // millis() of last good 16-byte reply (yellow LED heartbeat)
```

- [ ] **Step 2: Timestamp each good Kelly frame**

At the good-reply point in `kellyPoll()` (`main.cpp:1458`), the code reads:

```cpp
        // Good reply — cache this slot.
        g_kelly_frames_ok++;
        memcpy(&block[cur * 16], &rxbuf[i + 2], 16);
```

Add the timestamp assignment right after the counter increment:

```cpp
        // Good reply — cache this slot.
        g_kelly_frames_ok++;
        g_kelly_last_frame_ms = now;   // drives the yellow LED heartbeat
        memcpy(&block[cur * 16], &rxbuf[i + 2], 16);
```

(`now` is the local `millis()` snapshot already in scope in `kellyPoll()`.)

- [ ] **Step 3: Rework the yellow channel in `updateLed()`**

Replace the yellow-warning block in the `#if LED_IS_DUAL_GPIO` branch. The current block (`main.cpp:934-955`) is:

```cpp
    // Yellow warning channel. Off when healthy; periods chosen so the two
    // failure modes are distinguishable at a glance.
    uint32_t warn_period_ms = 0;
    if (!g_can_initialized) {
        warn_period_ms = WARN_BLINK_FAST_MS;
#if !defined(NO_WIFI)
    } else if (!g_ap_running && WiFi.status() != WL_CONNECTED) {
        warn_period_ms = WARN_BLINK_SLOW_MS;
#endif
    }
    static uint32_t warn_last_toggle = 0;
    static bool     warn_on = false;
    if (warn_period_ms > 0) {
        if (now - warn_last_toggle >= warn_period_ms) {
            warn_last_toggle = now;
            warn_on = !warn_on;
        }
        digitalWrite(WARN_LED_PIN, warn_on ? HIGH : LOW);
    } else {
        digitalWrite(WARN_LED_PIN, LOW);
        warn_on = false;
    }
```

Replace it entirely with:

```cpp
    // Yellow channel. Slow-blinks the one warning worth keeping (No-WiFi);
    // otherwise, on Kelly builds, mirrors the blue channel as a Kelly serial
    // heartbeat. No-WiFi wins when both apply. CAN-init failure is no longer
    // shown here — it surfaces as "blue never blinks" plus can.initialized
    // in /json and on the serial console.
    uint32_t warn_period_ms = 0;
#if !defined(NO_WIFI)
    if (!g_ap_running && WiFi.status() != WL_CONNECTED) {
        warn_period_ms = WARN_BLINK_SLOW_MS;
    }
#endif
    static uint32_t warn_last_toggle = 0;
    static bool     warn_on = false;
    if (warn_period_ms > 0) {
        if (now - warn_last_toggle >= warn_period_ms) {
            warn_last_toggle = now;
            warn_on = !warn_on;
        }
        digitalWrite(WARN_LED_PIN, warn_on ? HIGH : LOW);
    } else {
        warn_on = false;
#if defined(ENABLE_KELLY)
        // Kelly heartbeat: same active/toggle pattern as the blue CAN channel,
        // driven by the last good 16-byte reply instead of a CAN frame.
        bool kelly_active = (g_kelly_frames_ok > 0) &&
                            (now - g_kelly_last_frame_ms < LED_ACTIVE_MS);
        static uint32_t kelly_led_toggle = 0;
        static bool     kelly_led_on = false;
        if (kelly_active) {
            if (now - kelly_led_toggle >= LED_BLINK_MS) {
                kelly_led_toggle = now;
                kelly_led_on = !kelly_led_on;
            }
            digitalWrite(WARN_LED_PIN, kelly_led_on ? HIGH : LOW);
        } else {
            digitalWrite(WARN_LED_PIN, LOW);
            kelly_led_on = false;
        }
#else
        digitalWrite(WARN_LED_PIN, LOW);
#endif
    }
```

Leave the blue-activity block (`main.cpp:957-971`) and everything below unchanged.

- [ ] **Step 4: Update the LED legend comment block**

The comment above `updateLed()` (`main.cpp:887-892`) currently reads:

```cpp
// Dual-LED boards (RejsaCAN-ESP32-S3) split the state across two pins:
//   Yellow fast blink — CAN driver failed to initialize
//   Yellow slow blink — No Wi-Fi
//   Yellow off        — Network OK
//   Blue blink        — CAN frames arriving
//   Blue off          — No frames recently (green power LED still shows alive)
```

Replace those six lines with:

```cpp
// Dual-LED boards (RejsaCAN-ESP32-S3) split the state across two pins:
//   Yellow slow blink — No Wi-Fi (takes priority over the Kelly heartbeat)
//   Yellow flicker    — Kelly serial traffic arriving (ENABLE_KELLY builds only)
//   Yellow off        — Network OK, no Kelly traffic (or non-Kelly build)
//   Blue blink        — CAN frames arriving
//   Blue off          — No frames recently (green power LED still shows alive)
```

- [ ] **Step 5: Compile the plain RejsaCAN build (no Kelly)**

Run (native PlatformIO under Python 3.11–3.13, per CLAUDE.md, or via Docker):

```bash
cd esp32-s3 && pio run -e rejsacan
```

Expected: `SUCCESS`. This confirms the `#else` (Kelly-disabled) path compiles and that removing the `g_can_initialized` yellow clause didn't break the build.

- [ ] **Step 6: Compile the Kelly-enabled RejsaCAN build**

Run:

```bash
cd esp32-s3 && PLATFORMIO_BUILD_FLAGS=-DENABLE_KELLY pio run -e rejsacan
```

Expected: `SUCCESS`. This confirms the `#if defined(ENABLE_KELLY)` heartbeat path compiles and that `g_kelly_frames_ok` / `g_kelly_last_frame_ms` are in scope inside `updateLed()`.

- [ ] **Step 7: Sanity-compile the other two boards (guard against collateral damage)**

Run:

```bash
cd esp32-s3 && pio run -e adafruit_feather_s3 && pio run -e lilygo_t2can
```

Expected: both `SUCCESS`. Confirms the NeoPixel and no-LED branches are untouched and still build.

- [ ] **Step 8: Commit**

```bash
git add esp32-s3/src/main.cpp
git commit -m "Yellow LED: Kelly serial heartbeat on RejsaCAN

Drop the redundant CAN-init-fail blink; keep No-WiFi slow blink (top
priority). On ENABLE_KELLY builds, yellow now flickers with Kelly serial
traffic, mirroring the blue CAN-activity channel via a new
g_kelly_last_frame_ms timestamp."
```

**Manual bench verification (run before shipping a flashed device; not part of the automated build):**

1. WiFi up, Kelly streaming → yellow flickers with Kelly traffic; blue flickers with CAN independently.
2. Kelly disconnected (no replies) → yellow off (WiFi up).
3. WiFi down (bad creds / out of range), Kelly streaming → yellow slow-blinks No-WiFi; Kelly heartbeat suppressed. Restore WiFi → flicker returns.
4. Plain `rejsacan` build (no `-DENABLE_KELLY`) → yellow slow-blinks only on No-WiFi, otherwise off; a forced CAN-init failure no longer blinks yellow.

---

### Task 2: Docs — update the RejsaCAN LED table and status column

**Files:**
- Modify: `esp32-s3/README.md` — board table row (`README.md:21`) and the RejsaCAN LED table (`README.md:177-185`).

**Interfaces:**
- Consumes: the behavior implemented in Task 1. No code.
- Produces: nothing consumed downstream.

- [ ] **Step 1: Update the board-summary table Status LED cell**

`README.md:21` currently reads:

```
| RejsaCAN-ESP32-S3 v3.x | `rejsacan` | GPIO 14 | GPIO 13 | Yellow on GPIO 11 (warnings), Blue on GPIO 10 (CAN activity) |
```

Change the Status LED cell to:

```
| RejsaCAN-ESP32-S3 v3.x | `rejsacan` | GPIO 14 | GPIO 13 | Yellow on GPIO 11 (No-WiFi / Kelly heartbeat), Blue on GPIO 10 (CAN activity) |
```

- [ ] **Step 2: Update the RejsaCAN LED table**

`README.md:177-185` currently reads:

```
**RejsaCAN-ESP32-S3** (yellow + blue, plus a hard-wired green power LED):

| Pattern | Meaning |
|---|---|
| Yellow fast blink (10 Hz) | CAN driver failed to initialize |
| Yellow slow blink (2 Hz) | Booted, waiting for WiFi |
| Yellow off | Network OK |
| Blue blink | CAN frames arriving on the bus |
| Blue off | No frames recently (green power LED still confirms the board is alive) |
```

Replace that block with:

```
**RejsaCAN-ESP32-S3** (yellow + blue, plus a hard-wired green power LED):

| Pattern | Meaning |
|---|---|
| Yellow slow blink (2 Hz) | Booted, waiting for WiFi (takes priority over the Kelly heartbeat) |
| Yellow flicker | Kelly serial traffic arriving — `ENABLE_KELLY` builds only |
| Yellow off | Network OK, no Kelly traffic (or a non-Kelly build) |
| Blue blink | CAN frames arriving on the bus |
| Blue off | No frames recently (green power LED still confirms the board is alive) |

A CAN-init failure no longer blinks yellow: it shows as blue never blinking,
plus `can.initialized` in `/json` and on the serial console.
```

- [ ] **Step 3: Commit**

```bash
git add esp32-s3/README.md
git commit -m "docs: RejsaCAN yellow LED is now the Kelly heartbeat / No-WiFi"
```

---

## Self-Review

**Spec coverage:**
- Drop CAN-init-fail → Task 1 Step 3 (removed the `!g_can_initialized` clause) + Task 2 (docs). ✓
- Preserve No-WiFi → Task 1 Step 3 (No-WiFi clause kept, top priority). ✓
- Kelly heartbeat on `ENABLE_KELLY` only → Task 1 Steps 1-3 under `#if defined(ENABLE_KELLY)`. ✓
- No-WiFi wins priority → Task 1 Step 3 (heartbeat only in the `warn_period_ms == 0` else-branch). ✓
- Frame-level activity source (`g_kelly_frames_ok` + new `g_kelly_last_frame_ms`), not block-level → Task 1 Steps 1-2. ✓
- Mirror blue timing (`LED_BLINK_MS` / `LED_ACTIVE_MS`) → Task 1 Step 3. ✓
- Docs (legend comment + README) → Task 1 Step 4, Task 2. ✓
- Non-dual boards untouched → Global Constraints + Task 1 Step 7 sanity compile. ✓
- No unit-test harness; bench verification → Task 1 verification checklist. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases" — all code shown verbatim. ✓

**Type consistency:** `g_kelly_last_frame_ms` is `uint32_t` at declaration (Step 1), assigned `now` (a `millis()`/`uint32_t`) at Step 2, and read as `uint32_t` in the `now - g_kelly_last_frame_ms < LED_ACTIVE_MS` comparison (Step 3). `g_kelly_frames_ok` used consistently as the `> 0` liveness gate, matching how blue gates on `g_frames_rx > 0`. ✓
