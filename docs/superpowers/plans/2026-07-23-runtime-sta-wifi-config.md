# Runtime STA WiFi Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator change the board's station (home-network) WiFi SSID/password from a device-served web form, persisted in NVS and applied live without a reflash or reboot.

**Architecture:** Runtime globals `g_sta_ssid`/`g_sta_pass` become the single source of truth for the STA join, seeded at boot from NVS (`Preferences`) or, when unprovisioned, the compiled `WIFI_SSID`/`WIFI_PASS` defaults. A new `GET /wifi` form and `POST /wifi` handler (AP-password gated) write the globals + NVS and live-re-join via `WiFi.begin`. The always-up soft-AP is the recovery path. The acceptance test is a new `device-test.py` `/wifi` stage.

**Tech Stack:** Arduino-ESP32 (`WiFi.h`, `WebServer`, `Preferences.h`/NVS), C++; Python 3 `urllib` for the device-test stage.

## Global Constraints

- **Scope is STA credentials only.** `AP_SSID`, `AP_PASS`, `MDNS_NAME`, and the compiled `WIFI_SSID`/`WIFI_PASS` defaults are not changed. `#define AP_PASS` default is `"electricity"`; `#define AP_SSID`/`MDNS_NAME` default `"tractor"`.
- **All new firmware lives inside the existing `#if !defined(NO_WIFI)` region** (WiFi bring-up and HTTP handlers are already gated by it). NVS credential storage compiles regardless.
- **Never echo the stored STA password** anywhere (form, `/config`, logs) — presence only, mirroring the existing `sta.pass_set` pattern.
- **Do not add credentials or a config UI to `dashboard.html`** (it is mirrored to the Android app over BLE). The form is firmware-served HTML.
- **Testing model:** this repo has no host unit tests; `esp32-s3/device-test.py` (hardware-in-the-loop) is the suite. Firmware validation = `pio run -e rejsacan` builds clean, then the `/wifi` device-test stage passes against a flashed device.
- **Build:** `pio run -e rejsacan` (works with the repo's PlatformIO). **Flashing is manual on the host:** `pio run -e rejsacan -t upload` (Docker can't reach USB). Re-flash with the same env used before: `env WIFI_SSID="barnfiber" WIFI_PASS="$WIFI_PASSWORD" PLATFORMIO_BUILD_FLAGS="" pio run -e rejsacan -t upload`.
- **Accepted risk (from spec):** no pre-apply scan. A typo'd SSID makes the STA scan forever and degrades the soft-AP, and persists in NVS across reboot until corrected. Deliberate simplicity trade-off.

---

### Task 1: device-test `/wifi` acceptance stage (the failing test)

Written first: it fails against current firmware (no `/wifi` route → 404) and passes once Tasks 2-3 are flashed (validated in Task 4).

**Files:**
- Modify: `esp32-s3/device-test.py` (add `http_post` helper near `http_get`, add `stage_wifi`, add `--ap-pass` arg, call the stage in `main`)

**Interfaces:**
- Consumes: existing `http_request(host, path, method, timeout)` at `device-test.py:162`, `http_get` at `:174`, `fetch_json` at `:178`, the `check`/`report`/`section` helpers, and the `--host`/argparse setup near `:1201`.
- Produces: `http_post(host, path, fields: dict, timeout=6.0) -> (status, headers, body)`; `stage_wifi(args)`.

- [ ] **Step 1: Add the `http_post` helper**

After `http_get` (`device-test.py:175`), add:

```python
def http_post(host: str, path: str, fields: dict, timeout: float = 6.0):
    """POST form-urlencoded fields; returns (status, headers, body)."""
    url = f"http://{host}:{HTTP_PORT}{path}"
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
```

Ensure `import urllib.parse` is present near the other `urllib` imports at the top of the file (add it if only `urllib.request`/`urllib.error` are imported).

- [ ] **Step 2: Add the `--ap-pass` argument**

In the argparse block (near `device-test.py:1201`, alongside `--host`), add:

```python
    ap.add_argument("--ap-pass", default="electricity",
                    help="AP password (AP_PASS) — required to POST /wifi; "
                         "defaults to the compiled default")
```

- [ ] **Step 3: Add `stage_wifi`**

Add this function (place it near the other HTTP stages, e.g. after the mDNS stage):

```python
def stage_wifi(args) -> None:
    section("WiFi config (/wifi)")
    # Baseline: current STA SSID from /config (not /json — sta lives on /config).
    status, _, body = http_get(args.host, "/config")
    if not check(status == 200, "GET /config", f"HTTP {status}"):
        return
    orig_ssid = json.loads(body).get("wifi", {}).get("sta", {}).get("ssid", "")
    report("INFO", f"current STA SSID: {orig_ssid!r}")

    status, _, body = http_get(args.host, "/wifi")
    check(status == 200, "GET /wifi returns the form", f"HTTP {status}")
    check(b"<form" in body and b"ap_pass" in body,
          "form has an ap_pass field")

    # Wrong AP password is rejected and changes nothing.
    status, _, _ = http_post(args.host, "/wifi",
                             {"ssid": "shouldnotstick", "pass": "",
                              "ap_pass": "definitely-wrong"})
    check(status == 401, "POST /wifi with wrong ap_pass -> 401", f"HTTP {status}")
    now = json.loads(http_get(args.host, "/config")[2])["wifi"]["sta"]["ssid"]
    check(now == orig_ssid, "SSID unchanged after rejected POST",
          f"{now!r} != {orig_ssid!r}")

    # Malformed password (too short for WPA2, non-empty) is rejected.
    status, _, _ = http_post(args.host, "/wifi",
                             {"ssid": "somenet", "pass": "abc",
                              "ap_pass": args.ap_pass})
    check(status == 400, "POST /wifi with 3-char pass -> 400", f"HTTP {status}")

    # Clear to AP-only (empty SSID, correct ap_pass), then restore the original
    # so the bench network join is not left disabled.
    status, _, _ = http_post(args.host, "/wifi",
                             {"ssid": "", "pass": "", "ap_pass": args.ap_pass})
    check(status == 200, "POST /wifi clear SSID -> 200 (STA disabled)",
          f"HTTP {status}")
    time.sleep(1.0)
    now = json.loads(http_get(args.host, "/config")[2])["wifi"]["sta"]["ssid"]
    check(now == "", "STA SSID cleared", f"{now!r}")

    # Restore.
    status, _, _ = http_post(args.host, "/wifi",
                             {"ssid": orig_ssid, "pass": args.wifi_restore_pass,
                              "ap_pass": args.ap_pass}) if orig_ssid else (200, None, None)
    if orig_ssid:
        check(status == 200, "POST /wifi restore original SSID -> 200",
              f"HTTP {status}")
        report("INFO", f"restored STA SSID to {orig_ssid!r}")
    else:
        report("INFO", "no original SSID to restore (was AP-only)")
```

Add a companion arg for the restore password (the device never returns it, so the operator supplies it) in the argparse block:

```python
    ap.add_argument("--wifi-restore-pass", default="",
                    help="STA password to restore after the /wifi clear test "
                         "(the device never discloses the stored password)")
```

- [ ] **Step 4: Call the stage in `main`**

In the stage run order in `main` (where `stage_*` functions are invoked in sequence), add after the mDNS stage:

```python
    stage_wifi(args)
```

- [ ] **Step 5: Run against current firmware to confirm it fails**

Run (device on the network, current image without `/wifi`):

```bash
uv run python esp32-s3/device-test.py --host 192.168.1.180 2>&1 | sed -n '/WiFi config/,/==/p'
```

Expected: the `WiFi config (/wifi)` section shows `FAIL  GET /wifi returns the form — HTTP 404`. This confirms the test exercises the not-yet-built route.

- [ ] **Step 6: Commit**

```bash
git add esp32-s3/device-test.py
git commit -m "device-test: add /wifi acceptance stage"
```

---

### Task 2: NVS credential storage + STA source-of-truth

Replaces compile-time STA reads with runtime globals seeded from NVS-or-defaults. Behavior is identical to today when NVS is unprovisioned.

**Files:**
- Modify: `esp32-s3/src/main.cpp` (add `#include <Preferences.h>`; add globals + `loadStaCreds()`; call it in `setup()` before WiFi bring-up; replace STA reads at the bring-up `~:2865-2876` and in `handleConfig` `:1922-1925`)

**Interfaces:**
- Consumes: compiled `WIFI_SSID`/`WIFI_PASS` defaults (`main.cpp:66-70`); `g_sta_disconnects` (`:428`).
- Produces: `char g_sta_ssid[33]`, `char g_sta_pass[64]`; `bool staConfigured()`; `void loadStaCreds()`; `static Preferences g_prefs`.

- [ ] **Step 1: Add the include**

Near the other top includes (after `#include <WiFi.h>` at `main.cpp:19`), add:

```cpp
#include <Preferences.h>
```

- [ ] **Step 2: Add globals and the loader**

Near the other WiFi globals (just after `g_sta_last_disconnect_reason` at `main.cpp:429`), add:

```cpp
// Runtime STA credentials — the single source of truth for the station join.
// Seeded from NVS if provisioned, else from the compiled WIFI_SSID/WIFI_PASS
// defaults. Written by the /wifi POST handler.
char g_sta_ssid[33] = "";
char g_sta_pass[64] = "";
static Preferences g_prefs;

static inline bool staConfigured() { return g_sta_ssid[0] != '\0'; }

// NVS 'wifi' namespace overrides the compiled defaults; an absent 'ssid' key
// means "never provisioned", so fall back to the baked-in defaults (keeps the
// default build's behavior). Call once, before WiFi bring-up.
static void loadStaCreds() {
    g_prefs.begin("wifi", /*readOnly=*/true);
    if (g_prefs.isKey("ssid")) {
        g_prefs.getString("ssid", g_sta_ssid, sizeof(g_sta_ssid));
        g_prefs.getString("pass", g_sta_pass, sizeof(g_sta_pass));
    } else {
        strlcpy(g_sta_ssid, WIFI_SSID, sizeof(g_sta_ssid));
        strlcpy(g_sta_pass, WIFI_PASS, sizeof(g_sta_pass));
    }
    g_prefs.end();
}
```

- [ ] **Step 3: Call `loadStaCreds()` before WiFi bring-up**

In `setup()`, immediately before the `const bool join_sta = (sizeof(WIFI_SSID) > 1);` line at `main.cpp:2865`, insert:

```cpp
    loadStaCreds();
```

- [ ] **Step 4: Use the runtime globals at bring-up**

Replace the bring-up block at `main.cpp:2865-2876`:

```cpp
    const bool join_sta = (sizeof(WIFI_SSID) > 1);
    WiFi.mode(join_sta ? WIFI_AP_STA : WIFI_AP);
    g_ap_running = WiFi.softAP(AP_SSID, AP_PASS);
    if (join_sta) WiFi.begin(WIFI_SSID, WIFI_PASS);

    if (join_sta)
        Serial.printf("WiFi: AP \"%s\" %s; STA joining \"%s\" (pass %u chars)\r\n",
                      AP_SSID, g_ap_running ? "up" : "FAILED",
                      WIFI_SSID, (unsigned)(sizeof(WIFI_PASS) - 1));
    else
        Serial.printf("WiFi: AP \"%s\" %s; STA disabled (no WIFI_SSID baked in)\r\n",
                      AP_SSID, g_ap_running ? "up" : "FAILED");
```

with:

```cpp
    const bool join_sta = staConfigured();
    WiFi.mode(join_sta ? WIFI_AP_STA : WIFI_AP);
    g_ap_running = WiFi.softAP(AP_SSID, AP_PASS);
    if (join_sta) WiFi.begin(g_sta_ssid, g_sta_pass);

    if (join_sta)
        Serial.printf("WiFi: AP \"%s\" %s; STA joining \"%s\" (pass %u chars)\r\n",
                      AP_SSID, g_ap_running ? "up" : "FAILED",
                      g_sta_ssid, (unsigned)strlen(g_sta_pass));
    else
        Serial.printf("WiFi: AP \"%s\" %s; STA disabled (no SSID configured)\r\n",
                      AP_SSID, g_ap_running ? "up" : "FAILED");
```

- [ ] **Step 5: Use the runtime globals in `/config`**

Replace `main.cpp:1922-1925`:

```cpp
    const bool join_sta = (sizeof(WIFI_SSID) > 1);
    sta["ssid"]     = WIFI_SSID;                    // exactly what was compiled in
    sta["pass_set"] = (sizeof(WIFI_PASS) > 1);      // presence only, never the password
    sta["enabled"]  = join_sta;
```

with:

```cpp
    const bool join_sta = staConfigured();
    sta["ssid"]     = g_sta_ssid;                   // active STA SSID (NVS or default)
    sta["pass_set"] = (g_sta_pass[0] != '\0');      // presence only, never the password
    sta["enabled"]  = join_sta;
```

- [ ] **Step 6: Build**

Run: `pio run -e rejsacan`
Expected: `SUCCESS`. (The clang IDE diagnostics about `Arduino.h`/`WiFi` are pre-existing header-path noise, not build errors.)

- [ ] **Step 7: Commit**

```bash
git add esp32-s3/src/main.cpp
git commit -m "esp32-s3: make STA credentials runtime globals seeded from NVS"
```

---

### Task 3: `/wifi` form + POST handler

**Files:**
- Modify: `esp32-s3/src/main.cpp` (add `saveStaCreds`, `secretEquals`, `htmlEscape`, `handleWifiForm`, `handleWifiSave`; register the two routes near `:2887`)

**Interfaces:**
- Consumes: `g_sta_ssid`/`g_sta_pass`/`g_prefs`/`staConfigured()` (Task 2); `server` (`WebServer`), `noteHttpActivity()` (`:1848`), compiled `AP_PASS`.
- Produces: routes `GET /wifi` (`handleWifiForm`) and `POST /wifi` (`handleWifiSave`).

- [ ] **Step 1: Add persistence + helpers**

Add near `loadStaCreds()` (after it):

```cpp
static void saveStaCreds(const char* ssid, const char* pass) {
    g_prefs.begin("wifi", /*readOnly=*/false);
    g_prefs.putString("ssid", ssid);
    g_prefs.putString("pass", pass);
    g_prefs.end();
}

// Length-independent compare so a wrong AP password can't be timing-probed.
static bool secretEquals(const String& got, const char* want) {
    size_t wl = strlen(want);
    uint8_t diff = (uint8_t)(got.length() ^ wl);
    for (size_t i = 0; i < got.length(); i++)
        diff |= (uint8_t)got[i] ^ (uint8_t)(i < wl ? want[i] : 0);
    return diff == 0;
}

// Minimal HTML-escape for reflecting the current SSID into the form.
static String htmlEscape(const char* s) {
    String out;
    for (const char* p = s; *p; p++) {
        switch (*p) {
            case '&': out += "&amp;";  break;
            case '<': out += "&lt;";   break;
            case '>': out += "&gt;";   break;
            case '"': out += "&quot;"; break;
            default:  out += *p;       break;
        }
    }
    return out;
}
```

- [ ] **Step 2: Add the GET form handler**

Add near the other HTTP handlers (e.g. after `handleConfig`):

```cpp
// Human-facing STA WiFi form. Shows the current SSID and whether a password is
// set (never the password itself). Served on the always-up AP as well as STA,
// so it is reachable to fix a bad join.
void handleWifiForm() {
    noteHttpActivity();
    String body = F("<!doctype html><meta name=viewport "
                    "content='width=device-width,initial-scale=1'>"
                    "<title>WiFi setup</title><h2>Station WiFi</h2><p>Current SSID: <b>");
    body += staConfigured() ? htmlEscape(g_sta_ssid) : String("(none \xE2\x80\x94 AP only)");
    body += F("</b><br>Password set: ");
    body += g_sta_pass[0] ? F("yes") : F("no");
    body += F("</p><form method=post action=/wifi>"
              "<p>SSID (blank = AP only):<br><input name=ssid maxlength=32></p>"
              "<p>Password:<br><input name=pass type=password maxlength=63></p>"
              "<p>AP password (required):<br><input name=ap_pass type=password></p>"
              "<button type=submit>Save &amp; re-join</button></form>");
    server.send(200, "text/html", body);
}
```

- [ ] **Step 3: Add the POST handler**

```cpp
// Apply new STA credentials: AP-password gated, validated, persisted to NVS,
// then live re-join (no reboot). The soft-AP stays up throughout.
void handleWifiSave() {
    noteHttpActivity();
    if (!secretEquals(server.arg("ap_pass"), AP_PASS)) {
        server.send(401, "text/plain", "wrong AP password\n");
        return;
    }
    String ssid = server.arg("ssid");
    String pass = server.arg("pass");
    if (ssid.length() > 32) {
        server.send(400, "text/plain", "SSID too long (max 32)\n");
        return;
    }
    if (pass.length() != 0 && (pass.length() < 8 || pass.length() > 63)) {
        server.send(400, "text/plain",
                    "password must be empty or 8-63 chars (WPA2)\n");
        return;
    }

    strlcpy(g_sta_ssid, ssid.c_str(), sizeof(g_sta_ssid));
    strlcpy(g_sta_pass, pass.c_str(), sizeof(g_sta_pass));
    saveStaCreds(g_sta_ssid, g_sta_pass);

    WiFi.mode(staConfigured() ? WIFI_AP_STA : WIFI_AP);
    WiFi.disconnect(false);
    if (staConfigured()) WiFi.begin(g_sta_ssid, g_sta_pass);

    if (!slcan_open)
        Serial.printf("WiFi: STA reconfigured to \"%s\" (pass %u chars)\r\n",
                      g_sta_ssid, (unsigned)strlen(g_sta_pass));

    String msg = staConfigured()
        ? String("saved; re-joining \"") + g_sta_ssid + "\"\n"
        : String("saved; STA disabled (AP only)\n");
    server.send(200, "text/plain", msg);
}
```

- [ ] **Step 4: Register the routes**

After `server.on("/config", handleConfig);` at `main.cpp:2887`, add:

```cpp
    server.on("/wifi", HTTP_GET,  handleWifiForm);
    server.on("/wifi", HTTP_POST, handleWifiSave);
```

- [ ] **Step 5: Build**

Run: `pio run -e rejsacan`
Expected: `SUCCESS`.

- [ ] **Step 6: Commit**

```bash
git add esp32-s3/src/main.cpp
git commit -m "esp32-s3: add /wifi form + POST to set STA credentials at runtime"
```

---

### Task 4: Flash and validate end-to-end

**Files:** none (integration/verification)

- [ ] **Step 1: Flash the device**

Run (host, USB-connected):

```bash
cd esp32-s3
env WIFI_SSID="barnfiber" WIFI_PASS="$WIFI_PASSWORD" PLATFORMIO_BUILD_FLAGS="" \
    pio run -e rejsacan -t upload
```

Expected: upload completes; device reboots. Confirm reachable: `curl -s http://192.168.1.180/config | python3 -c "import sys,json;print(json.load(sys.stdin)['wifi']['sta']['ssid'])"` prints `barnfiber`.

- [ ] **Step 2: Run the `/wifi` device-test stage**

Run (supply the real STA password so the stage restores the join):

```bash
uv run python esp32-s3/device-test.py --host 192.168.1.180 \
    --ap-pass electricity --wifi-restore-pass "$WIFI_PASSWORD" \
    2>&1 | sed -n '/WiFi config/,/^==/p'
```

Expected: every `/wifi` check is `PASS` (GET form, 401 on wrong ap_pass, SSID unchanged, 400 on short pass, clear→200, cleared, restore→200).

- [ ] **Step 3: Manual live round-trip (no reboot)**

Note the current `uptime` from `/config`. Join the `tractor` AP (or from the LAN), `GET http://192.168.1.180/wifi`, submit a valid SSID + password + the AP password, and confirm within ~15 s that `/config` shows the new `sta.ssid`, `sta.status` reaches `connected`, and `uptime` did **not** reset (proving live re-join, not a reboot). Restore the original SSID via the form.

- [ ] **Step 4: Full regression run**

Run the full suite to confirm nothing else regressed:

```bash
uv run python esp32-s3/device-test.py --host 192.168.1.180 \
    --serial /dev/cu.usbmodem14401 \
    --inject-interface slcan --inject-channel /dev/cu.usbmodem14301 \
    --ack-interface canalystii --ack-channel 0 \
    --expect-sd --ble --sd-soak 60 \
    --ap-pass electricity --wifi-restore-pass "$WIFI_PASSWORD"
```

Expected: `Result: ship-ready`, including the new WiFi stage.

- [ ] **Step 5: Commit any doc updates**

If `esp32-s3/README.md` documents the HTTP endpoints, add a line for `GET/POST /wifi` (SSID/password, AP-password gated, applied live). Commit:

```bash
git add esp32-s3/README.md
git commit -m "esp32-s3: document the /wifi runtime STA config endpoint"
```

---

## Notes for the implementer

- `Preferences::isKey()` and `getString(key, char*, size_t)` are available in the Arduino-ESP32 core this project builds against; no extra library needed.
- The `\xE2\x80\x94` in the GET form is a UTF-8 em dash; keep it as bytes to avoid source-encoding issues.
- `server.arg("x")` returns an empty `String` for a missing field, which is the correct behavior for the optional `pass` and the "clear SSID" case.
- The POST handler intentionally does not scan (accepted risk); a bad SSID degrades the AP until corrected — this is documented in the spec.
