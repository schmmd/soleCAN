# Runtime STA WiFi configuration — design

**Date:** 2026-07-23
**Target:** `esp32-s3` firmware (all boards; `NO_WIFI` builds excluded)
**Status:** approved design, pre-implementation

## Goal

Let an operator change the station (home/shop network) WiFi credentials the
board joins **without reflashing** — via a small web form served by the device,
applied live (no reboot). Scope is deliberately limited to the STA credentials
(`WIFI_SSID` / `WIFI_PASS`). The AP identity (`AP_SSID` / `AP_PASS`) and
`MDNS_NAME` stay compile-time.

## Background

Today all five WiFi identity strings are compile-time `#define`s
(`src/main.cpp` defaults, optionally overridden by `wifi_overrides.h` generated
from env vars by `inject_build_overrides.py`). Changing the network the board
joins requires a rebuild + reflash. The board runs `WIFI_AP_STA`: its own
soft-AP (`tractor` @ 192.168.4.1, WPA2 via `AP_PASS`, wildcard-DNS captive
portal) is always up alongside the STA join, so the AP is a reliable recovery
path even when STA credentials are wrong.

**Critical constraint (from the existing comment at the WiFi bring-up):** when
the STA is enabled but its SSID is not found, the station scans every channel
forever, which makes the shared-radio soft-AP beacon hop channels and drop out.
Because the AP is both the recovery path and what the operator is connected to
when submitting the form, applying an unreachable SSID could strand the board.
The design guards against this with a pre-apply scan.

## Storage

ESP32 **NVS via the `Preferences` library**, namespace `wifi`, keys `ssid` and
`pass`. NVS lives in its own flash partition and survives app reflashes (only
`esptool erase_flash` clears it); it does not depend on the SD card. Rejected
alternatives: an SD config file (couples WiFi to optional hardware), a custom
partition (overkill).

## Credential source-of-truth

Two runtime globals replace the compile-time STA reads:

- `char g_sta_ssid[33]`, `char g_sta_pass[64]`.
- `bool staConfigured()` → `g_sta_ssid[0] != '\0'`.

At boot, before WiFi bring-up: open NVS `wifi`; if the `ssid` key exists, load
`g_sta_ssid`/`g_sta_pass` from it; otherwise seed them from the compiled
`WIFI_SSID`/`WIFI_PASS` defaults. The compiled defaults thus remain the initial
value and the "never provisioned" fallback.

Replace the STA-credential read sites with these globals + `staConfigured()`:
- the two `sizeof(WIFI_SSID) > 1` join checks (bring-up and `/config` JSON),
- `WiFi.begin(...)` at bring-up,
- the `/config` `sta.ssid` / `sta.pass_set` fields and the serial bring-up log.

`AP_SSID`, `AP_PASS`, `MDNS_NAME`, and the `WIFI_SSID`/`WIFI_PASS` defaults are
otherwise untouched.

## Web endpoints

Firmware-served on the existing `WebServer` (port 80). **Not** added to the
shared `dashboard.html`, so credentials never reach the BLE-mirrored asset.

### `GET /wifi`

A minimal embedded (PROGMEM) HTML form:
- shows the current STA SSID and `password set: yes/no` — **never** the stored
  password (mirrors the existing `pass_set` pattern),
- fields: new SSID, new STA password, and **AP password** (the gate),
- Save button; the page also renders the result/error of the last POST.

### `POST /wifi`

Form-encoded body: `ssid`, `pass`, `ap_pass`. Processing order:

1. **AP-password gate.** Compare `ap_pass` to the compiled `AP_PASS` with a
   length-independent (constant-time) compare. On mismatch → `401`, no scan, no
   save, running config untouched.
2. **Validation.** `ssid` length 0-32; `pass` length 0 or 8-63 (WPA2). An empty
   `ssid` is the explicit "disable STA / AP-only" case (skips the scan in
   step 3). Invalid → `400`, running config untouched.
3. **Scan guard.** For a non-empty SSID, `WiFi.scanNetworks()` and require the
   SSID to appear in the results. Not found → `422`, running config untouched
   (this is the strand-the-AP guard).
4. **Persist + apply.** Write `g_sta_ssid`/`g_sta_pass`, save to NVS, then live
   re-join: `WiFi.mode(WIFI_AP_STA)` (or `WIFI_AP` when cleared to AP-only),
   `WiFi.disconnect(false)`, and `WiFi.begin(g_sta_ssid, g_sta_pass)` for a
   configured SSID. Respond `200` with a short status. The existing
   `WiFi.onEvent` handler logs `GOT_IP` / `DISCONNECTED` and updates the
   `g_sta_*` counters; mDNS re-announces on the new IP — no reboot.

All of POST /wifi runs on the loop task (like every other handler), so the NVS
write and `WiFi.begin` are single-threaded with the rest of `loop()`. The
`WiFi.scanNetworks()` blocks the loop for ~2-3 s; acceptable for a rare manual
action, and it does not transmit on the CAN bus.

## Security / threat model

Consistent with the otherwise-unauthenticated dashboard, but the STA-credential
write is gated by the **AP password**: a caller must supply `AP_PASS` to change
credentials. This restricts rewrites to someone who knows the AP secret — which
notably blocks STA-side LAN users who joined the home network but don't know
`AP_PASS`. Worst case of a successful change is knocking the board off the home
network, which the always-up AP recovers. The stored STA password is
write-only and never echoed.

## Testing

`device-test.py` additions (WiFi stage, no bench hardware needed beyond the
device):
- `GET /wifi` returns `200` and the form HTML.
- `POST /wifi` with a wrong `ap_pass` → `401`; `GET /config` still shows the old
  SSID (no change).
- `POST /wifi` with the correct `ap_pass` but a bogus SSID → `422` (scan guard);
  config unchanged.
- `POST /wifi` clearing the SSID (empty, correct `ap_pass`) → `200`, STA
  disabled — then restore the original via a second POST so the test is
  non-destructive to the bench network join.

Manual: a real round-trip on the bench — join the AP, set a valid SSID, confirm
`/config` shows the new SSID and the board gets an IP on the target network,
without a reboot (uptime does not reset).

## Out of scope

AP SSID/password and mDNS-name runtime config; a UI in the mirrored dashboard;
multiple stored networks; WPS/SmartConfig provisioning.
