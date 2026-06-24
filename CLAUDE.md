# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Reverse-engineering and monitoring tooling for a **Solectrac e25G** electric
tractor's CAN buses. The decode information is empirical — derived from captured
traffic, vendor manuals, and live injection tests — so everything is tagged with
confidence markers: **CONFIRMED**, **TENTATIVE**, **UNKNOWN**. Preserve and
respect these markers when editing docs or decoders; don't promote a TENTATIVE
decode to CONFIRMED without injection or cross-validation evidence.

## Two distinct CAN buses (essential mental model)

The tractor exposes two separate buses, and code/docs are organized around the
split. Confusing them is the most common way to go wrong here.

1. **Main vehicle J1939 bus** — 250 kbit/s, 29-bit extended frames. Four ECUs
   (motor controller `0xCA`, BMS `0xF3`, charger `0xE5`, cluster) plus a passive
   OBD-II tap. Broadcast PGNs, no request/response. Decoded by the Python
   `solectrac-*` tools, the ESP32 firmware, and documented in `DOCUMENTATION.md`.

2. **BMS UDS diagnostic bus** — a *separate* 2-pin port on the UDAN BMS, ISO-TP /
   UDS request-response on `0x740` (req) / `0x748` (resp). Polled by reading Data
   Identifiers (DIDs). Handled only by `bms/` and documented in `bms/README.md`.

## Architecture

### Shared decoder core
`solecan_proto.py` is the **single source of truth** for the main-bus J1939
protocol: source-address (SA) map, PGN identifiers, BMS fault-bit tables, and
voltage/current/temperature scalings. Both `solecan-analyze.py` and
`solecan-stream.py` import it. Rule of thumb enforced by the code: protocol
facts and scalings live in `solecan_proto.py`; **display-only** tables
(human-readable lamp text, error-code descriptions) live in the script that
renders them. When adding a signal, put the encoding in the proto module and the
presentation in the consumer.

### Main-bus tools (root)
- `solecan-analyze.py` — offline batch decoder. Reads any `python-can`
  `LogReader` format and emits tidy long-format CSVs (`signals.csv`,
  `frames.csv`, `decoders.csv`, `can_ids.csv`). `frame_index` joins
  `signals.csv` → `frames.csv` so any value traces back to its source bytes.
- `solecan-stream.py` — live/replayed `rich` TUI dashboard. Also serves the
  decoded JSON over HTTP. Decodes the same frames as the analyzer.
- `util/` — bus injection probes (`mc_inject.py`, `solectrac-inject-f108.py`)
  that transmit modified frames to map how the cluster renders codes. These
  actively write to the bus.

### BMS diagnostic tool (`bms/`)
- `solectrac-bms-diagnostics.py` — polls UDS DIDs in a background thread and
  serves a localhost dashboard. Independent of the main-bus tools and the proto
  module; its DID map is in `bms/README.md`.

### Embedded firmware (`embedded/esp32-s3/`)
ESP32-S3 firmware that re-implements the main-bus J1939 decode in C++ and
exposes it four ways: WiFi HTML dashboard, JSON endpoint, BLE (Nordic UART
Service), USB SLCAN, and socketcand. All logic is in `src/main.cpp`; the board
pin maps are `#ifdef`-selected (`BOARD_ADAFRUIT_FEATHER_S3` /
`BOARD_LILYGO_T2CAN`). See `embedded/esp32-s3/README.md` for the full build,
wiring, and flashing guide.

### Android app (`android/`)
Mirrors the ESP32 web dashboard over BLE so the phone doesn't need to join the
tractor's WiFi. Loads the dashboard HTML in a WebView and pipes JSON snapshots
from the NUS characteristic. `android/README.md` has details (note: it still
refers to the firmware as `esp32/`; the real path is `embedded/esp32-s3/`).

### Shared dashboard HTML
`dashboard.html` at the repo root is the **single tracked copy**. Both
consumers copy it into place at build time:

- Android (`android/app/build.gradle.kts`) registers a `copyDashboardAsset`
  task that runs before `preBuild` and copies it into
  `android/app/src/main/assets/dashboard.html`.
- ESP32 (`embedded/esp32-s3/copy_dashboard.py`, wired in via
  `extra_scripts = pre:copy_dashboard.py`) copies it to
  `embedded/esp32-s3/src/dashboard.html` so `board_build.embed_txtfiles` can
  bake it into the firmware binary.

Both destinations are gitignored, so the file cannot drift — there is only
one tracked copy. The Docker build uses the repo root as its context and
places `dashboard.html` directly at `embedded/esp32-s3/src/dashboard.html`;
`copy_dashboard.py` treats a pre-placed file as authoritative when the shared
source isn't present in the build context.

## Commands

### Python tooling
`pyproject.toml` (managed with `uv`) is the full dependency set, including BLE
(`bless`) and the Canalyst-II interface. `requirements.txt` is the lighter set
sufficient for the analyzer and stream TUI (`python-can`, `pyserial`, `rich`).

```bash
# Offline decode of captures -> CSVs in OUTDIR
python3 solecan-analyze.py -o out capture1.asc capture2.blf

# Live TUI from a CAN interface (any python-can interface works)
python3 solecan-stream.py --interface slcan --channel /dev/cu.usbmodem101 --bitrate 250000
python3 solecan-stream.py --interface socketcan --channel can0 --bitrate 250000

# Replay a capture
python3 solecan-stream.py --replay session.log

# BMS UDS diagnostics dashboard
python3 bms/solectrac-bms-diagnostics.py
```

### Firmware (`embedded/esp32-s3/`)
The reproducible path is Docker (see `embedded/esp32-s3/README.md`); native
builds use PlatformIO. Flashing always happens on the host (Docker Desktop on
macOS can't reach USB).

```bash
# Docker build (context = repo root, because it embeds the canonical dashboard.html)
docker build -f embedded/esp32-s3/Dockerfile \
    --build-arg WIFI_SSID="..." --build-arg WIFI_PASS="..." \
    --build-arg GIT_SHA=$(git rev-parse --short HEAD) -t solectrac-fw .
docker run --rm -v "$PWD/out:/out" solectrac-fw   # extracts bins to out/

# Native PlatformIO
pio run -e lilygo_t2can            # or adafruit_feather_s3
pio run -e lilygo_t2can -t upload  # build + flash
```

> **Python version gotcha:** PlatformIO 6.1.x **segfaults during package
> post-install on Python 3.14** — and `pyproject.toml` requires Python ≥3.14 for
> the CAN tooling. Keep these separate: run the firmware build under Python
> 3.11–3.13 (a dedicated venv) or in Docker, while the Python tooling uses 3.14.

### Android (`android/`)
```bash
# Docker build (context = repo root, like the firmware build)
docker build -f android/Dockerfile \
    --build-arg GIT_SHA=$(git rev-parse --short HEAD) -t solectrac-android .
docker run --rm -v "$PWD/out:/out" solectrac-android   # extracts APK to out/

# Native Gradle
gradle wrapper --gradle-version 8.7   # one-time; wrapper JAR is not checked in
./gradlew installDebug
```

## No automated tests
This repository has no test suite — the tools are reverse-engineering scripts
validated against real captures and live injection on the tractor.

## Reference docs
- `DOCUMENTATION.md` — main-bus J1939 decode, CAN topology, OBD-II pinout,
  cluster hardware, vendor error-code tables.
- `bms/README.md` — BMS UDS diagnostic port: wire protocol, session lifecycle,
  DID map.
