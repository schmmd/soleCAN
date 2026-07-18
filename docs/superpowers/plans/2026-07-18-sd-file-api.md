# SD-Card File Access API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HTTP endpoints on the ESP32-S3 firmware to list (`/sd/status`, `/sd/sessions`), download (`GET /sd/sessions/{id}` as tar), and delete (`DELETE /sd/sessions/{id}`) SD-card session logs while logging continues, per the approved spec `docs/superpowers/specs/2026-07-18-sd-file-api-design.md`.

**Architecture:** All firmware changes live in `embedded/esp32-s3/src/main.cpp` (the project keeps all firmware logic in this one file — follow that). A new FreeRTOS mutex serializes SD/SPI access between the existing writer task (core 0) and the new HTTP handlers (loop task, core 1). Tar downloads stream a USTAR archive chunk-by-chunk, taking the mutex per chunk so a multi-MB download never starves the writer. `/json` sheds five diagnostic `sd` fields, which move to `/sd/status`.

**Tech Stack:** Arduino-ESP32 (`WebServer` + `uri/UriBraces.h`, `SD.h`, FreeRTOS semaphores), ArduinoJson 7, Python 3 stdlib (`tarfile`, `urllib`) for the bench test.

## Global Constraints

- **Build check:** `cd /Users/michael/hack/solecan/embedded/esp32-s3 && pio run -e rejsacan` (the only `HAS_SD` board). PlatformIO on this machine runs under Homebrew Python 3.14 and prints a noisy telemetry traceback **after** the build result — ignore it; judge success by `SUCCESS` in the build output. If the build itself fails on Python-version grounds (CLAUDE.md gotcha), fall back to the Docker build documented in `embedded/esp32-s3/README.md`.
- **Hardware tests:** there are no unit tests in this repo. Verification per task = clean compile; end-to-end verification = the `device-test.py` stage added in Task 6, run later on bench hardware. Do not invent a host-side test harness.
- **`dashboard.html`:** edit only the repo-root copy — the copies under `android/` and `embedded/esp32-s3/src/` are build-time artifacts and gitignored.
- **Confidence markers:** not applicable here (no protocol decode changes) — do not touch decode tables.
- **Commits:** plain imperative subject (repo style, e.g. "Add support for writing to SD cards"), and end every commit message with:

  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01U7nFCAAzVU91MTpTVavdp1
  ```

- **Spec is authoritative** for behavior questions: `docs/superpowers/specs/2026-07-18-sd-file-api-design.md`.

---

### Task 1: SD mutex — serialize card access between writer task and HTTP

**Files:**
- Modify: `embedded/esp32-s3/src/main.cpp` (SD section, ~lines 422–772)

**Interfaces:**
- Produces: `static SemaphoreHandle_t g_sd_mutex` and RAII guard `struct SdLock` — every later task's HTTP handler takes `SdLock lock;` around SD calls. Writer task holds the mutex for each drain/flush/roll burst; `sdFail()` releases it before self-deleting so a latched error can never deadlock HTTP.

- [ ] **Step 1: Add the mutex and guard**

In `main.cpp`, directly after the `g_sd_active` definition (search for `volatile bool g_sd_active = false;`), add:

```cpp
// Serializes every SD/SPI touch between the writer task (core 0) and the
// /sd/* HTTP handlers (loop task, core 1). The writer holds it per
// drain/flush/roll burst; HTTP holds it per operation — and per read chunk
// while streaming a tar — so a download stalls logging only briefly and the
// PSRAM rings absorb it. sdFail() must release it before self-deleting.
static SemaphoreHandle_t g_sd_mutex = nullptr;

struct SdLock {
    SdLock()  { xSemaphoreTake(g_sd_mutex, portMAX_DELAY); }
    ~SdLock() { xSemaphoreGive(g_sd_mutex); }
};
```

- [ ] **Step 2: Create the mutex in `sdInit()`**

In `sdInit()` (search for `static void sdInit()`), make mutex creation the first act, folding into the existing failure branch:

```cpp
static void sdInit() {
    g_sd_mutex = xSemaphoreCreateMutex();
    if (!g_sd_mutex || !sdInitRing(g_sd_raw) || !sdInitRing(g_sd_json)) {
        g_sd.fail_op = "ring_alloc";
        g_sd.state   = "error";
        return;
    }
```

(The rest of `sdInit()` is unchanged.)

- [ ] **Step 3: Wrap the writer task's burst in the mutex**

Replace the body of the `for (;;)` loop in `sdWriterTask()` (search for `static void sdWriterTask(void*)`) so the whole burst — drain, roll, flush, free-check — runs under the mutex, and the idle delay runs outside it:

```cpp
    for (;;) {
        bool flush_due = (millis() - last_flush >= SD_FLUSH_MS);

        xSemaphoreTake(g_sd_mutex, portMAX_DELAY);
        bool did = sdDrainStream(g_sd_raw, flush_due, buf);
        did     |= sdDrainStream(g_sd_json, flush_due, buf);
        g_sd.kb_written = (uint32_t)(sd_total_bytes >> 10);

        sdRollIfDue(g_sd_raw);
        sdRollIfDue(g_sd_json);

        if (flush_due) {
            last_flush = millis();
            g_sd_raw.file.flush();
            g_sd_json.file.flush();
        }
        if (millis() - last_free >= SD_FREE_CHECK_MS) {
            last_free = millis();
            sdUpdateFree();
        }
        xSemaphoreGive(g_sd_mutex);

        if (!did) vTaskDelay(pdMS_TO_TICKS(20));   // both rings empty — yield
    }
```

Note `sdRecoverOrFail()`'s remount backoff (up to ~3.75 s) now runs holding the mutex — that is intentional: HTTP must not touch the card mid-remount.

- [ ] **Step 4: Release the mutex in `sdFail()`**

`sdFail()` self-deletes the writer task, which at that point holds the mutex (it is only ever called from inside the writer's locked burst). Add the release just before `vTaskDelete`, at the end of `sdFail()` (search for `static void sdFail(const char* op)`):

```cpp
    if (g_sd_raw.file)  g_sd_raw.file.close();
    if (g_sd_json.file) g_sd_json.file.close();
    xSemaphoreGive(g_sd_mutex);   // held by the writer's burst — free it or HTTP deadlocks
    vTaskDelete(nullptr);
```

- [ ] **Step 5: Build**

Run: `cd /Users/michael/hack/solecan/embedded/esp32-s3 && pio run -e rejsacan`
Expected: `SUCCESS` (ignore the post-build telemetry traceback).

- [ ] **Step 6: Commit**

```bash
git add embedded/esp32-s3/src/main.cpp
git commit -m "Add SD mutex serializing writer task and HTTP access"
```

---

### Task 2: `GET /sd/status` and `GET /sd/sessions`

**Files:**
- Modify: `embedded/esp32-s3/src/main.cpp` (HTTP handlers section, after `handleRoot()` ~line 1625; registration in `setup()` ~line 2205)

**Interfaces:**
- Consumes: `g_sd_mutex`/`SdLock` (Task 1); existing `g_sd`, `g_sd_raw`, `g_sd_json`, `g_sd_active`, `sdParseSession()`, `sdBasename()`.
- Produces: `handleSdStatus()`, `handleSdList()`, and helper `sdSendError(int, const char*)` (reused by Tasks 4–5). JSON shapes exactly as specced: status = flat object; list = `{"sessions":[{"id","active","bytes","files":[{"name","size"}]}]}`.

- [ ] **Step 1: Add the handlers**

In the HTTP handlers section, insert after `handleRoot()` (search for `void handleRoot()` and its closing brace) a new `#if defined(HAS_SD)` block:

```cpp
#if defined(HAS_SD)
// ── SD file access API ────────────────────────────────────────────────────────
// See docs/superpowers/specs/2026-07-18-sd-file-api-design.md. /sd/status is
// in-RAM only (poll freely); /sd/sessions, /sd/sessions/{id} GET (tar) and DELETE
// touch the card under g_sd_mutex and answer 503 unless a session is logging.

static void sdSendError(int code, const char* msg) {
    server.send(code, "application/json",
                String("{\"error\":\"") + msg + "\"}");
}

// Cheap logging status + the diagnostics that used to ride along in /json
// (raw_part, json_part, recoveries, fail_op, fail_kb). No mutex, no card I/O.
static void handleSdStatus() {
    JsonDocument doc;
    doc["state"] = g_sd.state;
    if (g_sd_active) {
        doc["session"]    = g_sd.session;
        doc["raw_part"]   = g_sd_raw.part;
        doc["json_part"]  = g_sd_json.part;
        doc["kb_written"] = g_sd.kb_written;
        doc["free_mb"]    = g_sd.free_mb;
    }
    if (g_sd_raw.dropped)  doc["raw_dropped"]  = g_sd_raw.dropped;
    if (g_sd_json.dropped) doc["json_dropped"] = g_sd_json.dropped;
    if (g_sd.recoveries)   doc["recoveries"]   = g_sd.recoveries;
    if (g_sd.fail_op[0]) {
        doc["fail_op"] = g_sd.fail_op;
        doc["fail_kb"] = g_sd.fail_kb;
    }
    String out;
    serializeJsonPretty(doc, out);
    server.send(200, "application/json", out);
}

// Session/file inventory: one mutex-guarded walk of the card. Only /sNNNNN
// directories are listed (same sdParseSession() filter as the reaper).
static void handleSdList() {
    if (!g_sd_active) { sdSendError(503, "sd_unavailable"); return; }
    JsonDocument doc;
    auto sessions = doc["sessions"].to<JsonArray>();
    {
        SdLock lock;
        File root = SD.open("/");
        if (!root) { sdSendError(500, "open_root"); return; }
        for (File e = root.openNextFile(); e; e = root.openNextFile()) {
            uint32_t n;
            if (e.isDirectory() && sdParseSession(sdBasename(e.name()), n)) {
                auto s = sessions.add<JsonObject>();
                s["id"]     = n;
                s["active"] = (n == g_sd.session);
                uint64_t total = 0;
                char path[16];
                snprintf(path, sizeof path, "/s%05lu", (unsigned long)n);
                auto files = s["files"].to<JsonArray>();
                File d = SD.open(path);
                if (d) {
                    for (File f = d.openNextFile(); f; f = d.openNextFile()) {
                        if (!f.isDirectory()) {
                            auto fo = files.add<JsonObject>();
                            fo["name"] = sdBasename(f.name());   // ArduinoJson 7 copies
                            fo["size"] = (uint32_t)f.size();
                            total += f.size();
                        }
                        f.close();
                    }
                    d.close();
                }
                s["bytes"] = total;
            }
            e.close();
        }
        root.close();
    }
    String out;
    serializeJsonPretty(doc, out);
    server.send(200, "application/json", out);
}
#endif  // HAS_SD
```

- [ ] **Step 2: Register the routes**

In `setup()`, after `server.on("/config", handleConfig);` (search for that exact line), add:

```cpp
#if defined(HAS_SD)
    server.on("/sd/status", HTTP_GET, handleSdStatus);
    server.on("/sd/sessions",   HTTP_GET, handleSdList);
#endif
```

(Non-SD boards keep no `/sd` routes at all — requests fall into the captive-portal `handleNotFound` redirect, which is the existing behavior for unknown paths.)

- [ ] **Step 3: Build**

Run: `cd /Users/michael/hack/solecan/embedded/esp32-s3 && pio run -e rejsacan`
Expected: `SUCCESS`. Also build a non-SD board to prove the `#if` guards hold: `pio run -e lilygo_t2can` → `SUCCESS`.

- [ ] **Step 4: Commit**

```bash
git add embedded/esp32-s3/src/main.cpp
git commit -m "Add /sd/status and /sd/sessions endpoints"
```

---

### Task 3: Slim `/json` `sd` object and dashboard label

**Files:**
- Modify: `embedded/esp32-s3/src/main.cpp` (inside `buildJson()`, search for `// SD session-logging status`)
- Modify: `dashboard.html` (repo root, ~line 388)

**Interfaces:**
- Consumes: nothing new.
- Produces: `/json`'s `sd` object now contains exactly `state`, `session`, `kb_written`, `free_mb`, `raw_dropped`, `json_dropped` (last two only when nonzero) — Task 6's test relies on this shape; the moved fields are already served by `/sd/status` (Task 2), so land Task 2 first.

- [ ] **Step 1: Trim `buildJson()`**

Replace the `sd` block inside `buildJson()` (the `#if defined(HAS_SD)` block, search for `// SD session-logging status`) with:

```cpp
#if defined(HAS_SD)
    // SD session-logging status — only what the dashboard renders. Full
    // diagnostics (parts, recoveries, fail_op) moved to /sd/status. Always
    // emitted (small, and the phone app mirrors the dashboard).
    {
        auto sd = doc["sd"].to<JsonObject>();
        sd["state"] = g_sd.state;
        if (g_sd_active) {
            sd["session"]    = g_sd.session;
            sd["kb_written"] = g_sd.kb_written;
            sd["free_mb"]    = g_sd.free_mb;
        }
        if (g_sd_raw.dropped)  sd["raw_dropped"]  = g_sd_raw.dropped;
        if (g_sd_json.dropped) sd["json_dropped"] = g_sd_json.dropped;
    }
#endif
```

- [ ] **Step 2: Drop the part suffix from the dashboard session label**

In `dashboard.html` (repo root — never the copies under `android/` or `embedded/esp32-s3/src/`), find:

```js
      var sess='s'+sd.session+(sd.raw_part?'.'+sd.raw_part:'');
```

and replace with:

```js
      var sess='s'+sd.session;
```

- [ ] **Step 3: Build (embeds the edited dashboard)**

Run: `cd /Users/michael/hack/solecan/embedded/esp32-s3 && pio run -e rejsacan`
Expected: `SUCCESS`.

- [ ] **Step 4: Commit**

```bash
git add embedded/esp32-s3/src/main.cpp dashboard.html
git commit -m "Move SD diagnostics from /json to /sd/status"
```

---

### Task 4: `GET /sd/sessions/{id}` — streamed USTAR tar download

**Files:**
- Modify: `embedded/esp32-s3/src/main.cpp` (extend the Task 2 `HAS_SD` handler block; add includes; register route)

**Interfaces:**
- Consumes: `SdLock`, `sdSendError()`, `sdParseSession()` machinery, `server.pathArg(0)` (from `UriBraces` routing).
- Produces: `handleSdSessionGet()`; helpers `tarFillHeader(uint8_t hdr[512], const char* name, uint32_t size)` and `sdClientWrite(WiFiClient&, const uint8_t*, size_t)` (reused verbatim nowhere else — keep them `static` beside the handler). Archive layout: members named `sNNNNN/<file>`, mode 0644, mtime 0, plus 1024-byte end-of-archive trailer; exact `Content-Length` sent up front.

- [ ] **Step 1: Add includes**

At the top of `main.cpp`, after `#include <WebServer.h>` (search for that line), add:

```cpp
#include <uri/UriBraces.h>
```

and with the other standard includes add:

```cpp
#include <vector>
```

- [ ] **Step 2: Add tar helpers and the GET handler**

Inside the Task 2 `#if defined(HAS_SD)` handler block, after `handleSdList()`, add:

```cpp
// Minimal USTAR header for one regular file. `name` ("s00042/can_00.asc") is
// far below the 100-byte field. mtime is 0 — the ESP32 has no RTC, matching
// the nominal date in the .asc header.
static void tarFillHeader(uint8_t hdr[512], const char* name, uint32_t size) {
    memset(hdr, 0, 512);
    strncpy((char*)hdr, name, 99);                 // name
    memcpy(hdr + 100, "0000644", 8);               // mode
    memcpy(hdr + 108, "0000000", 8);               // uid
    memcpy(hdr + 116, "0000000", 8);               // gid
    char oct[13];
    snprintf(oct, sizeof oct, "%011lo", (unsigned long)size);
    memcpy(hdr + 124, oct, 12);                    // size
    memcpy(hdr + 136, "00000000000", 12);          // mtime
    hdr[156] = '0';                                // typeflag: regular file
    memcpy(hdr + 257, "ustar", 6);                 // magic (NUL-terminated)
    memcpy(hdr + 263, "00", 2);                    // version
    memset(hdr + 148, ' ', 8);                     // checksum: spaces while summing
    uint32_t sum = 0;
    for (int i = 0; i < 512; i++) sum += hdr[i];
    snprintf(oct, sizeof oct, "%06lo", (unsigned long)sum);
    memcpy(hdr + 148, oct, 6);
    hdr[154] = '\0';
    hdr[155] = ' ';
}

// Push `n` bytes to the client, tolerating partial writes. Bounded retries so
// a wedged socket can't hang the loop task forever.
static bool sdClientWrite(WiFiClient& client, const uint8_t* p, size_t n) {
    int stalls = 0;
    while (n) {
        if (!client.connected()) return false;
        size_t w = client.write(p, n);
        if (w == 0) {
            if (++stalls > 50) return false;
            delay(2);
            continue;
        }
        stalls = 0;
        p += w;
        n -= w;
    }
    return true;
}

// Parse the {id} path segment as a plain decimal session number.
// Returns false on empty/garbage (→ 404, matching a nonexistent session).
static bool sdParseIdArg(uint32_t& id) {
    String arg = server.pathArg(0);
    if (arg.isEmpty()) return false;
    char* end = nullptr;
    id = strtoul(arg.c_str(), &end, 10);
    return end && *end == '\0';
}

// GET /sd/sessions/{id} — the whole session directory as one uncompressed
// USTAR stream. Member sizes freeze at header time (the walk below), so the
// active session yields a consistent snapshot ≤~1 s stale; if a member reads
// short of its frozen size the remainder is zero-padded so the archive stays
// well-formed. The mutex is held per chunk, never across client I/O.
static void handleSdSessionGet() {
    if (!g_sd_active) { sdSendError(503, "sd_unavailable"); return; }
    uint32_t id;
    if (!sdParseIdArg(id)) { sdSendError(404, "not_found"); return; }

    char dir[16];
    snprintf(dir, sizeof dir, "/s%05lu", (unsigned long)id);

    struct Member { String name; uint32_t size; };
    std::vector<Member> members;
    {
        SdLock lock;
        File d = SD.open(dir);
        if (!d || !d.isDirectory()) {
            if (d) d.close();
            sdSendError(404, "not_found");
            return;
        }
        for (File f = d.openNextFile(); f; f = d.openNextFile()) {
            if (!f.isDirectory())
                members.push_back({ String(sdBasename(f.name())),
                                    (uint32_t)f.size() });
            f.close();
        }
        d.close();
    }

    uint64_t total = 1024;   // end-of-archive: two zero blocks
    for (auto& m : members)
        total += 512 + (((uint64_t)m.size + 511) / 512) * 512;
    if (total > UINT32_MAX) { sdSendError(507, "too_large"); return; }

    server.setContentLength((size_t)total);
    server.sendHeader("Content-Disposition",
                      String("attachment; filename=") + (dir + 1) + ".tar");
    server.send(200, "application/x-tar", "");
    WiFiClient client = server.client();

    static uint8_t buf[4096];   // loop-task stack is tight; single-threaded handler
    for (auto& m : members) {
        char arcname[48], path[48];
        snprintf(arcname, sizeof arcname, "%s/%s", dir + 1, m.name.c_str());
        snprintf(path,    sizeof path,    "%s/%s", dir,     m.name.c_str());
        uint8_t hdr[512];
        tarFillHeader(hdr, arcname, m.size);
        if (!sdClientWrite(client, hdr, 512)) return;

        File f;
        { SdLock lock; f = SD.open(path, FILE_READ); }
        uint32_t sent = 0;
        while (sent < m.size) {
            size_t want = m.size - sent;
            if (want > sizeof buf) want = sizeof buf;
            int n;
            { SdLock lock; n = f ? f.read(buf, want) : -1; }
            if (n <= 0) { memset(buf, 0, want); n = want; }   // short/gone: pad
            if (!sdClientWrite(client, buf, n)) {
                SdLock lock; if (f) f.close();
                return;                                       // client went away
            }
            sent += n;
        }
        { SdLock lock; if (f) f.close(); }

        size_t pad = (512 - (m.size % 512)) % 512;
        if (pad) {
            memset(buf, 0, pad);
            if (!sdClientWrite(client, buf, pad)) return;
        }
    }
    memset(buf, 0, 1024);
    sdClientWrite(client, buf, 1024);   // end-of-archive trailer
}
```

- [ ] **Step 3: Register the route**

In `setup()`, inside the Task 2 `#if defined(HAS_SD)` registration block, add:

```cpp
    server.on(UriBraces("/sd/sessions/{}"), HTTP_GET, handleSdSessionGet);
```

- [ ] **Step 4: Build**

Run: `cd /Users/michael/hack/solecan/embedded/esp32-s3 && pio run -e rejsacan`
Expected: `SUCCESS`.

- [ ] **Step 5: Commit**

```bash
git add embedded/esp32-s3/src/main.cpp
git commit -m "Add GET /sd/sessions/{id} streaming tar download"
```

---

### Task 5: `DELETE /sd/sessions/{id}`

**Files:**
- Modify: `embedded/esp32-s3/src/main.cpp` (extend the `HAS_SD` handler block; register route)

**Interfaces:**
- Consumes: `SdLock`, `sdSendError()`, `sdParseIdArg()` (Task 4), `sdRemoveSessionDir()` (existing reaper helper).
- Produces: `handleSdSessionDelete()` — `200 {"free_mb":N}` / `409 {"error":"active_session"}` / `404` / `503` exactly as Task 6's test expects.

- [ ] **Step 1: Add the handler**

After `handleSdSessionGet()` in the `HAS_SD` handler block, add:

```cpp
// DELETE /sd/sessions/{id} — recursively remove a session directory via the
// reaper's helper. The active session is never deletable. free_mb is
// recomputed here (usedBytes() is slow, but deletes are rare) so the response
// reflects the space just reclaimed.
static void handleSdSessionDelete() {
    if (!g_sd_active) { sdSendError(503, "sd_unavailable"); return; }
    uint32_t id;
    if (!sdParseIdArg(id)) { sdSendError(404, "not_found"); return; }
    if (id == g_sd.session) { sdSendError(409, "active_session"); return; }

    char dir[16];
    snprintf(dir, sizeof dir, "/s%05lu", (unsigned long)id);

    SdLock lock;
    File d = SD.open(dir);
    if (!d || !d.isDirectory()) {
        if (d) d.close();
        sdSendError(404, "not_found");
        return;
    }
    d.close();
    if (!sdRemoveSessionDir(dir)) { sdSendError(500, "remove_failed"); return; }

    uint64_t total = SD.totalBytes();
    uint64_t used  = SD.usedBytes();
    g_sd.free_mb = (uint32_t)((total > used ? total - used : 0) >> 20);
    server.send(200, "application/json",
                String("{\"free_mb\":") + g_sd.free_mb + "}");
}
```

- [ ] **Step 2: Register the route**

In the `setup()` registration block:

```cpp
    server.on(UriBraces("/sd/sessions/{}"), HTTP_DELETE, handleSdSessionDelete);
```

- [ ] **Step 3: Build**

Run: `cd /Users/michael/hack/solecan/embedded/esp32-s3 && pio run -e rejsacan`
Expected: `SUCCESS`. Rebuild the non-SD board too: `pio run -e lilygo_t2can` → `SUCCESS`.

- [ ] **Step 4: Commit**

```bash
git add embedded/esp32-s3/src/main.cpp
git commit -m "Add DELETE /sd/sessions/{id}"
```

---

### Task 6: Bench test — update `stage_sd`, add `stage_sd_files`

**Files:**
- Modify: `embedded/esp32-s3/device-test.py`

**Interfaces:**
- Consumes: the endpoint shapes produced by Tasks 2–5; existing helpers `section()`, `check()`, `report()`, `http_get()`, `fetch_json()`.
- Produces: `http_request(host, path, method, timeout)` helper; `stage_sd(args, j) -> bool` (now returns logging-healthy); `stage_sd_files(args, sd_ok)`; `--sd-delete-test` flag gating the destructive delete check.

- [ ] **Step 1: Add imports and the request helper**

Add `io` and `tarfile` to the stdlib imports at the top of `device-test.py`. Then generalize the HTTP helper — replace the existing `http_get` (search for `def http_get`) with:

```python
def http_request(host: str, path: str, method: str = "GET",
                 timeout: float = 6.0):
    """Returns (status, headers, body) without following redirects."""
    url = f"http://{host}:{HTTP_PORT}{path}"
    req = urllib.request.Request(url, method=method)
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def http_get(host: str, path: str, timeout: float = 6.0):
    return http_request(host, path, timeout=timeout)
```

- [ ] **Step 2: Rework `stage_sd` to pull diagnostics from `/sd/status`**

Replace the whole `stage_sd` function (search for `def stage_sd`) with:

```python
def stage_sd(args, j: dict) -> bool:
    """SD logging health. Returns True when state == logging (gates the
    file-API stage). Diagnostics that used to ride along in /json's sd
    object (fail_op, recoveries) now come from /sd/status."""
    section("SD logging")
    sd = j.get("sd")
    if sd is None:
        if args.expect_sd:
            check(False, "sd status present",
                  "no 'sd' object in /json — is this an SD-capable build?")
        else:
            report("SKIP", "no 'sd' object in /json (board without microSD)")
        return False

    try:
        status, _, body = http_get(args.host, "/sd/status")
        st = json.loads(body) if status == 200 else {}
    except Exception as e:  # noqa: BLE001 — any transport error fails the check
        status, st = 0, {}
    if not check(status == 200, "GET /sd/status", f"HTTP {status}"):
        st = {}

    state = sd.get("state")
    if state == "error":
        op = st.get("fail_op")
        if op in ("ring_alloc", "start_session"):
            hint = ("failed during boot init, before any writes — "
                    "check the card's FAT formatting"
                    if op == "start_session" else
                    "PSRAM ring allocation failed — wrong build/board?")
        else:
            hint = ("card unresponsive through all remount attempts; "
                    "reseat or replace the card and reboot")
        check(False, "SD logging healthy",
              f"latched error: fail_op={op!r} after "
              f"{st.get('fail_kb', 0)} KB — {hint}")
        return False
    if state == "no_card":
        if args.expect_sd:
            check(False, "card mounted", "state=no_card — insert a card and "
                  "reboot (the card is probed once at boot)")
        else:
            report("WARN", "no card at boot — session logging dormant "
                           "(pass --expect-sd to make this a failure)")
        return False

    check(state == "logging", "SD session logging", f"state={state}")
    check(sd.get("session", 0) >= 1, "session directory open",
          f"session={sd.get('session')}")
    free_mb = sd.get("free_mb", 0)
    check(free_mb > 0, "card has free space", f"free_mb={free_mb}")
    if free_mb < 1024:
        report("WARN", f"only {free_mb} MB free — the reaper will start "
                       "deleting old sessions below 512 MB")

    drops = sd.get("raw_dropped", 0) + sd.get("json_dropped", 0)
    check(drops == 0, "no ring-buffer drops",
          f"raw={sd.get('raw_dropped', 0)} json={sd.get('json_dropped', 0)}")
    recoveries = st.get("recoveries", 0)
    if recoveries:
        report("WARN", f"{recoveries} remount recoveries this boot — "
                       "logging survived, but the card link is glitching")
    return state == "logging"
```

- [ ] **Step 3: Add `stage_sd_files`**

Insert directly after `stage_sd`:

```python
def stage_sd_files(args, sd_ok: bool) -> None:
    """Exercise the SD file API: list, tar download of the active session,
    refusal cases, optional destructive delete, and logging-survives check."""
    section("SD file API")
    if not sd_ok:
        report("SKIP", "SD logging not healthy — file API not exercised")
        return

    kb0 = json.loads(http_get(args.host, "/sd/status")[2]).get("kb_written", 0)

    status, _, body = http_get(args.host, "/sd/sessions")
    if not check(status == 200, "GET /sd/sessions", f"HTTP {status}"):
        return
    sessions = json.loads(body).get("sessions", [])
    active = [s for s in sessions if s.get("active")]
    if not check(len(active) == 1, "exactly one active session",
                 f"{len(active)} active of {len(sessions)} listed"):
        return
    check(all(f.get("name") and f.get("size") is not None
              for s in sessions for f in s.get("files", [])),
          "listing entries carry name+size")

    # Tar download of the active session: exact Content-Length, parseable
    # archive, expected members. Sizes may exceed the earlier listing (the
    # session is live) — internal consistency is what's checked.
    sid = active[0]["id"]
    status, hdrs, body = http_request(args.host, f"/sd/sessions/{sid}",
                                      timeout=120)
    if check(status == 200, f"GET /sd/sessions/{sid}", f"HTTP {status}"):
        ctype = hdrs.get("Content-Type", "")
        check(ctype.startswith("application/x-tar"), "tar content-type", ctype)
        clen = int(hdrs.get("Content-Length", -1))
        check(len(body) == clen, "body matches Content-Length",
              f"{len(body)} vs {clen}")
        try:
            tf = tarfile.open(fileobj=io.BytesIO(body))
            names = tf.getnames()
            check(f"s{sid:05d}/can_00.asc" in names, "raw part in tar",
                  ", ".join(names[:4]))
            jsonl = f"s{sid:05d}/data_00.jsonl"
            if check(jsonl in names, "json part in tar"):
                data = tf.extractfile(jsonl).read()
                check(data.lstrip()[:1] == b"{", "jsonl member looks like JSON",
                      repr(data[:20]))
        except tarfile.TarError as e:
            check(False, "tar parses", str(e))

    # Refusals.
    status, _, _ = http_request(args.host, f"/sd/sessions/{sid}",
                                method="DELETE")
    check(status == 409, "DELETE active session refused", f"HTTP {status}")
    status, _, _ = http_request(args.host, "/sd/sessions/99999",
                                method="DELETE")
    check(status == 404, "DELETE missing session -> 404", f"HTTP {status}")

    # Destructive delete of the oldest non-active session — opt-in.
    others = sorted(s["id"] for s in sessions if not s.get("active"))
    if not args.sd_delete_test:
        report("SKIP", "delete test (pass --sd-delete-test to enable — "
                       "removes the oldest session on the card)")
    elif not others:
        report("SKIP", "delete test: no non-active session on the card")
    else:
        victim = others[0]
        status, _, body = http_request(args.host, f"/sd/sessions/{victim}",
                                       method="DELETE")
        if check(status == 200, f"DELETE /sd/sessions/{victim}",
                 f"HTTP {status}"):
            check("free_mb" in json.loads(body), "delete reports free_mb")
        _, _, body = http_get(args.host, "/sd/sessions")
        remaining = [s["id"] for s in json.loads(body).get("sessions", [])]
        check(victim not in remaining, "deleted session gone from listing",
              f"remaining={remaining}")

    # Logging must have kept running through all of the above. kb_written
    # advances every flush (~1 s) because the 1 Hz jsonl snapshot always has
    # data, so give it a beat and compare.
    time.sleep(2.5)
    st2 = json.loads(http_get(args.host, "/sd/status")[2])
    check(st2.get("state") == "logging", "still logging after file ops",
          f"state={st2.get('state')}")
    check(st2.get("kb_written", 0) > kb0, "kb_written advanced",
          f"{kb0} -> {st2.get('kb_written')}")
```

- [ ] **Step 4: Wire the flag and the stage into `main()`**

Next to the existing `--sd-soak` argument (search for `--sd-soak`), add:

```python
    ap.add_argument("--sd-delete-test", action="store_true",
                    help="exercise DELETE /sd/sessions/{id} by removing the "
                         "oldest non-active session (destructive)")
```

In `main()`, replace the line `stage_sd(args, j)` with:

```python
    sd_ok = stage_sd(args, j)
    stage_sd_files(args, sd_ok)
```

- [ ] **Step 5: Syntax check**

Run: `python3 -m py_compile embedded/esp32-s3/device-test.py && echo OK`
Expected: `OK`. (Full execution needs bench hardware — run `python3 embedded/esp32-s3/device-test.py --expect-sd --sd-delete-test` against a flashed device when available.)

- [ ] **Step 6: Commit**

```bash
git add embedded/esp32-s3/device-test.py
git commit -m "Add SD file API stage to device acceptance test"
```

---

### Task 7: README documentation

**Files:**
- Modify: `embedded/esp32-s3/README.md` (Endpoints table ~line 308; "SD-card session logging" section ~line 347)

**Interfaces:** none — docs only.

- [ ] **Step 1: Extend the Endpoints table**

In the `## Endpoints` table (search for `| \`http://tractor.local/json\` |`), add rows:

```markdown
| `http://tractor.local/sd/status` | SD logging status + diagnostics (RejsaCAN only) |
| `http://tractor.local/sd/sessions` | SD session/file inventory as JSON |
| `http://tractor.local/sd/sessions/N` | `GET` whole session as a `.tar`; `DELETE` removes it |
```

- [ ] **Step 2: Document the API in the SD section**

In the "SD-card session logging (RejsaCAN only)" section, find:

```markdown
Logging status shows in `/json` under `sd` (state, session, KB written, free MB,
drops) and on the dashboard footer. Tunables are `#define`s at the top of the
```

and replace the first sentence so it reads:

```markdown
Logging status shows in `/json` under `sd` (state, session, KB written, free MB,
drops) and on the dashboard footer; full diagnostics (`raw_part`, `json_part`,
`recoveries`, `fail_op`, `fail_kb`) live on `/sd/status`. Tunables are `#define`s at the top of the
```

Then append a subsection after the "**Pulling data off and replaying it**" paragraph and its code block:

````markdown
### Pulling files over WiFi

The card never has to leave the tractor — the firmware serves the sessions
over HTTP while logging continues (SD access is mutex-shared with the
writer; the PSRAM rings absorb the brief stalls):

```bash
curl http://tractor.local/sd/status                    # logging state + diagnostics
curl http://tractor.local/sd/sessions                      # sessions and their files
curl -O -J http://tractor.local/sd/sessions/7           # download session 7 → s00007.tar
curl -X DELETE http://tractor.local/sd/sessions/7       # delete session 7
```

Downloads are uncompressed USTAR archives with an exact `Content-Length`.
The **active** session is downloadable too: member sizes freeze when the
transfer starts, so you get a consistent snapshot that trails the live
session by up to ~1 s (the writer's flush cadence). Deleting the active
session is refused (`409`); all `/sd` endpoints answer `503` when no card
was present at boot or logging has latched an error.
````

- [ ] **Step 3: Commit**

```bash
git add embedded/esp32-s3/README.md
git commit -m "Document the SD file access API"
```
