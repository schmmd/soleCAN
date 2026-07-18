# SD-card file access API — design

**Date:** 2026-07-18
**Target:** `embedded/esp32-s3` firmware (RejsaCAN / any `HAS_SD` board)
**Status:** approved design, pre-implementation

## Goal

Web-based (JSON/HTTP API only — no UI) support for listing, downloading, and
deleting SD-card session logs, without interrupting the continuous session
logging that runs whenever a card is present.

## Background

The firmware logs continuously for the whole uptime when a card is present at
boot: raw frames to `/sNNNNN/can_PP.asc` and 1 Hz decoded snapshots to
`/sNNNNN/data_PP.jsonl`, with parts rolling at 64 MB. All SD I/O is owned by a
dedicated writer task on core 0; HTTP handlers run on the loop task. There is
no idle state, so file access must coexist with active logging.

## Endpoints

All served by the existing `WebServer` on port 80, unauthenticated like `/json`
and `/config`. Session routes are registered with `UriBraces`
(`/sd/session/{}`), which the ESP32 Arduino `WebServer` supports.

### `GET /sd/status`

Card/logging status only — reads in-RAM state, no mutex, no card I/O, cheap to
poll: `state`, `session` (active), `kb_written`, `free_mb`, plus the
diagnostic fields evicted from `/json`: `raw_part`, `json_part`,
`recoveries`, `fail_op`, `fail_kb`. Always answers `200`, whatever the card
state (that's the point — it reports it).

### `GET /sd/list`

Session inventory: `sessions` array of
`{ id, active, bytes, files: [{ name, size }] }`, built from one
mutex-guarded directory walk.

Returns `503` when `state` is `no_card`/`error`/dormant.

### `GET /sd/session/{id}`

Streams the entire session directory as an **uncompressed USTAR tar**
(`application/x-tar`, `Content-Disposition: attachment; filename=sNNNNN.tar`).

Why tar, not zip: a USTAR archive streams trivially on-device — 512-byte
headers with member sizes known up front, so an exact `Content-Length` can be
sent (sum of member sizes rounded up to 512, plus one header block per file,
plus the 1024-byte end-of-archive trailer), and no CRC pass over the data is
needed (zip requires CRC32 per member up front or data descriptors). No
compression: gzip on the ESP32 over multi-MB logs is slow, and `.asc`/`.jsonl`
compress well on the host afterwards.

Active-session download is allowed and yields a snapshot: member sizes are
frozen at header-write time, and the two currently-open part files are
truncated/padded in the stream to exactly that size, so the archive is always
internally consistent. Data from the last ≤1 s (since the writer's last flush)
is not included — inherent to snapshotting a live session.

`404` if the session directory does not exist; `503` as above.

### `DELETE /sd/session/{id}`

Recursively removes `/sNNNNN` using the existing session-directory removal
helper (same code path as the free-space reaper). Responses:

- `200` with updated `free_mb` on success
- `409` for the active session (never deletable)
- `404` if absent; `503` as above

No POST fallback — API-only consumers can send DELETE.

## `/json` slimming

The `sd` object in `/json` shrinks to exactly what the dashboard renders:
`state`, `session`, `kb_written`, `free_mb`, `raw_dropped`, `json_dropped`.
Moved to `/sd/status`: `raw_part`, `json_part`, `recoveries`, `fail_op`,
`fail_kb`.

`dashboard.html` changes accordingly: the session label drops the part suffix
(shows `s42`, no longer `s42.3` after a part roll). This also slims the BLE
snapshot mirrored by the Android app, which renders the same dashboard, so
nothing else consumes the removed fields.

## Concurrency

A new FreeRTOS mutex `g_sd_mutex` serializes all SD/SPI access:

- The writer task takes it around each drain/flush/roll burst.
- HTTP handlers take it per operation — and for tar streaming, **per chunk**
  (read ~4–8 KB under the mutex, release, send over TCP, repeat) so a multi-MB
  download never starves the writer. The PSRAM rings (1 MB raw / 256 KB json)
  absorb the interleaving stalls.

## Error handling

SD failures inside HTTP handlers (read error mid-stream, remove failure) do
**not** invoke the writer's remount/latch machinery — they abort the HTTP
response (or return `500`) and leave card recovery to the writer's own path,
so a flaky download can never kill logging. A tar stream that fails mid-body
is simply truncated (the client sees a short read vs. `Content-Length`).

## Testing

Extend `embedded/esp32-s3/device-test.py` with a bench stage (requires a card
in the device):

1. `GET /sd/status` — `state == "logging"`, sane counters.
2. `GET /sd/list` — valid shape, active session present.
3. Download a session tar — verify member names/sizes against the listing.
4. `DELETE` an old session — gone from the next listing, `free_mb` updated.
5. `DELETE` the active session — refused with `409`.
6. Throughout: logging stats (`kb_written`) keep advancing.

## Docs

Endpoint table and the active-session snapshot caveat added to
`embedded/esp32-s3/README.md`.
