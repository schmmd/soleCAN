# Solectrac CAN Monitor (ESP32)

Firmware that reads J1939 CAN frames from a Solectrac e25G electric tractor and
exposes the decoded state in four different ways:

- A mobile-friendly HTML dashboard over WiFi
- A JSON endpoint for scripting
- BLE JSON output
- Raw CAN frames over USB (SLCAN)
- Raw CAN frames over WiFi (socketcand)

## Supported hardware

The firmware builds for three ESP32-S3 boards. Pick the one you have; all
speak the same J1939 bus at 250 kbit/s (29-bit extended frames).

| Board | PlatformIO env | CAN TX | CAN RX | Status LED |
|---|---|---|---|---|
| Adafruit ESP32-S3 Reverse TFT Feather + CAN Pal | `adafruit_feather_s3` | GPIO 8 (A5) | GPIO 14 (A4) | NeoPixel on GPIO 33 (power-gated by GPIO 21) |
| LilyGo T-2CAN (CAN B / native TWAI) | `lilygo_t2can` | GPIO 7 | GPIO 6 | none |
| RejsaCAN-ESP32-S3 v3.x | `rejsacan` | GPIO 14 | GPIO 13 | Yellow on GPIO 11 (warnings), Blue on GPIO 10 (CAN activity) |

Notes:

- On the **Adafruit Feather**, the Reverse TFT display is not used.
- On the **LilyGo T-2CAN**, **both** CAN ports are streamed. The native TWAI
  controller (CAN B header, GPIO 6/7) is read into the J1939 decoder and
  forwarded as socketcand channel `can0`; the second port (MCP2515 on SPI,
  CS=10/SCK=12/MOSI=11/MISO=13/INT=8) is forwarded raw as channel `can1`. Both
  ports are expected to be classic CAN at 250 kbit/s. The transceivers are
  galvanically isolated, so when wiring to a separate analyzer you must also
  connect DGND between the two — without it the bus floats and no frames
  arrive. To monitor the tractor, wire its single J1939 bus to **CAN B**
  (`can0`); see "Wiring the LilyGo T-2CAN to the tractor" below.
- The MCP2515 needs its active-low RESET line (GPIO 9 on the T-2CAN) driven
  high before SPI; the firmware pulses it in `setup()`. If `init_err` in
  `/json` is `1` (`kNoMCP2515`), the chip isn't answering SPI — check
  `MCP2515_RST_PIN` and the SPI pins.
- The crystal frequency is `MCP2515_QUARTZ_HZ` in `src/main.cpp` (16 MHz for
  the stock T-2CAN). If init succeeds (`init_err: 0`) but `frames_rx` stays 0,
  a wrong crystal value is the prime suspect — try 8 MHz and reflash.
- On the **RejsaCAN-ESP32-S3** the firmware drives two extra pins at boot:
  `CAN_RS` (GPIO 38) is pulled LOW so the transceiver runs in high-speed
  normal mode, and `FORCE_ON` (GPIO 17) is pulled HIGH so the auto-shutdown
  circuit doesn't cut power across key cycles. If `frames_rx` stays 0 with the
  bus clearly active, double-check that `CAN_RS` is reaching the transceiver —
  a floating RS pin silently mangles 250 kbit/s framing.
- The pin map lives in `src/main.cpp` under `BOARD_ADAFRUIT_FEATHER_S3` /
  `BOARD_LILYGO_T2CAN` / `BOARD_REJSACAN`. Build environments are defined
  in `platformio.ini`.

## LilyGo T-2CAN

### Wiring to the tractor

The Solectrac OBD-II diagnostic port is a passive tap on the single 250 kbit/s
J1939 bus. Only four cavities are populated (see `DOCUMENTATION.md` →
"CAN bus topology" for the full pinout):

| OBD-II (J1962) pin | Signal |
|---|---|
| 6 | CAN_H |
| 14 | CAN_L |
| 4 | chassis ground |
| 16 | +12 V battery |

On the T-2CAN, use the **CAN B** 4-pin screw terminal (`P1`, the one wired to
the native TWAI transceiver). Match the silkscreen labels:

| OBD-II pin | → | T-2CAN CAN B terminal |
|---|---|---|
| 6 (CAN_H) | → | `CANH` |
| 14 (CAN_L) | → | `CANL` |
| 4 (GND) | → | `GND` (CAN-side / `DGND`) |

The CAN transceiver (`TD501MCAN`) is a **galvanically isolated** module, so its
bus side has its own ground reference. If your board's terminal has a 120 Ω
termination jumper for CAN B, leave it **off**: the tractor bus is already
terminated (it measures ~40 Ω), and this is a tap, not a bus end.

### Powering the board

The T-2CAN accepts **DC 5–12 V** on its 2-pin power screw terminal (a separate
terminal from the CAN-B connector, feeding an on-board buck regulator). You have
two options:

- **USB-C (galvanically isolated):** power from a laptop or USB power bank. The
  isolated transceiver keeps the board fully decoupled from the tractor — best
  for bench work or when wiring to a separate analyzer.
- **Tap the tractor's 12 V (single-cable field setup):** wire OBD-II pin 16
  (+12 V) to the power terminal **`+` / `VIN`** and OBD-II pin 4 (GND) to the
  power terminal **`GND`**, observing polarity. 12 V is within the 5–12 V input
  range. This shares the tractor's ground, so the CAN isolation is electrically
  moot in this mode, but it is safe and is the simplest in-cab setup — one
  connector powers the board and carries CAN.

> ⚠️ Do **not** exceed 12 V on the power terminal — the board is rated DC 5–12 V.
> The tractor's nominal 12 V accessory rail is fine; do not wire it to a higher
> traction-pack rail.

## Bus mode: passive tap by default, transmit is opt-in

The default firmware is a **hardware-enforced passive tap**: both CAN
controllers (native TWAI on can0, MCP2515 on can1) come up in listen-only mode,
so the silicon never drives a bit — no ACKs, no error frames, no injection. A
bug in a transmit path cannot perturb the bus, because there is no path from
code to the wire. This is the right default for tapping a bus already occupied
by a device you must not disturb (e.g. a fleet or grant-compliance telematics
logger); a passive OBD-II Y-splitter taps the same bus in parallel.

To write to the bus, build with **`-DCAN_ALLOW_TX`**. That switches both
controllers to NORMAL mode, so they ACK received frames *and* arm every
transmit path: SLCAN `t`/`T` injection over USB and socketcand `< send >` over
WiFi. The flag is board-agnostic (works on `adafruit_feather_s3`,
`lilygo_t2can`, `rejsacan`):

```bash
# Native PlatformIO (substitute your env)
pio run -e <env>                                          # listen-only passive tap (default)
PLATFORMIO_BUILD_FLAGS=-DCAN_ALLOW_TX pio run -e <env>    # NORMAL: ACKs + transmit

# Docker (reproducible build): add the build-arg; omit it for the passive tap
docker build -f esp32-s3/Dockerfile --build-arg CAN_ALLOW_TX=1 -t solectrac-fw .
```

Confirm which mode is running via `can.mode` (`normal` / `listen_only`) in
`/json`.

**Gotcha with the default (listen-only):** on the tractor's four-ECU bus the
real nodes ACK each other, so monitoring works unchanged. But on a bare
two-node bench setup — one talker plus this board — nothing ACKs the talker, so
it retransmits and eventually goes bus-off. Build with `-DCAN_ALLOW_TX` for
bench work where this board must provide the ACK.

## Kelly e-hydraulic pump monitor (RejsaCAN, opt-in)

The e-hydraulic **Kelly KLS pump controller** is not on the CAN bus — it has a
separate serial diagnostic port. Build with **`-DENABLE_KELLY`** (RejsaCAN only)
to poll it on UART1 and fold its telemetry into `/json` as a `kelly` object,
which the dashboard renders as a **Hydraulic Pump** card. Off by default; a
plain RejsaCAN build is unchanged.

```bash
PLATFORMIO_BUILD_FLAGS=-DENABLE_KELLY pio run -e rejsacan
```

Wiring uses **GPIO47 (RX)** and **GPIO48 (TX)** — plain GPIOs, deliberately NOT
the board's rear RXD/TXD pads (GPIO44/43, the ESP32-S3 UART0 console pins), which
corrupted the receive waveform. The Kelly port is **5 V TTL**, so the
controller's Tx needs a **~1–2.2 kΩ series resistor** into the RX pin to protect
the 3.3 V input:

- Kelly **Tx** (green) →\[~1–2.2 kΩ]→ **GPIO47** (RX)
- Kelly **Rx** (blue) → **GPIO48** (TX)
- Kelly **V−** (black) → **GND**; Kelly **V+** (red, 12 V) → leave unconnected

The monitor is **read-only by construction** — only the three zero-data monitor
queries (`0x3A/0x3B/0x3C`) are ever transmitted, and no flash session is opened,
so the firmware cannot reconfigure the controller. Polling is non-blocking and
the `kelly` object appears only while the controller is powered and answering.
Connector pinout, wire protocol, field map, and the series-resistor rationale
are in [`../../kelly/README.md`](../../kelly/README.md).

## What the LED tells you

The LilyGo T-2CAN has no user LED, so its LED calls are no-ops.

**Adafruit Feather** (single NeoPixel):

| Pattern | Meaning |
|---|---|
| Red blink | CAN driver failed to initialize |
| Amber blink | Booted, waiting for WiFi |
| Dim white (solid) | WiFi connected, no CAN frames received recently |
| Green blink | CAN frames arriving on the bus |

**RejsaCAN-ESP32-S3** (yellow + blue, plus a hard-wired green power LED):

| Pattern | Meaning |
|---|---|
| Yellow fast blink (10 Hz) | CAN driver failed to initialize |
| Yellow slow blink (2 Hz) | Booted, waiting for WiFi |
| Yellow off | Network OK |
| Blue blink | CAN frames arriving on the bus |
| Blue off | No frames recently (green power LED still confirms the board is alive) |

## Setting up on a new computer

> **Prefer a reproducible build?** Skip straight to "Building with Docker"
> below — it sidesteps all host Python/toolchain issues and is the recommended
> path on macOS.

1. **Install PlatformIO** into an isolated Python environment:

   ```bash
   python3.13 -m venv ~/.venvs/pio
   ~/.venvs/pio/bin/pip install platformio
   # then use ~/.venvs/pio/bin/pio, or add it to PATH
   ```

   > ⚠️ **Use Python 3.11–3.13, not 3.14.** PlatformIO 6.1.x segfaults during
   > package post-install on Python 3.14. `brew install platformio` currently
   > pulls in Python 3.14 as a dependency and fails for this reason — install
   > into a 3.13 venv (above), or use Docker.

2. **Clone the repo and enter this folder**:

   ```bash
   git clone <repo-url>
   cd solectrac/esp32-s3
   ```

3. *(Optional)* **Set a bench WiFi network** to also join. Leave these unset to
   build AP-only — the board still broadcasts its own hotspot (the stable
   default for field use), so this step isn't required:

   ```bash
   export WIFI_SSID="your-network"
   export WIFI_PASS="your-password"
   ```

   Add these to your shell profile (`~/.zshrc`, `~/.config/fish/config.fish`)
   if you'd like them to persist.

4. **Plug the board in via USB-C.** On macOS the serial port appears as
   `/dev/cu.usbmodemXXXXX`; PlatformIO auto-detects it.

## Common commands

Pass `-e <env>` to target a specific board. Without it, PlatformIO builds every
environment in `platformio.ini`.

| Command | What it does |
|---|---|
| `pio run -e adafruit_feather_s3` | Build firmware for the Adafruit Feather |
| `pio run -e lilygo_t2can` | Build firmware for the LilyGo T-2CAN |
| `pio run -e <env> -t upload` | Build and flash to the connected board |
| `pio device monitor -b 115200` | Open USB serial console (also speaks SLCAN — see below) |
| `pio run -t clean` | Wipe build cache (useful if PIO ever gets confused) |

If `upload` fails with `port is busy`, something else (often a leftover
`pio device monitor` in another shell) is holding the serial port. Find and
close it:

```bash
lsof /dev/cu.usbmodem*
```

## Building with Docker

The `Dockerfile` builds the firmware in a pinned, reproducible environment
(Python 3.13 + PlatformIO 6.1.19), avoiding host toolchain issues entirely.
**Build the firmware in Docker, then flash from the host** — Docker Desktop on
macOS cannot reach the board's USB serial port.

The build context is the **repo root** (the firmware embeds the canonical
`dashboard.html` shared with the Android app). From the repo root:

```bash
docker build -f esp32-s3/Dockerfile \
    --build-arg GIT_SHA=$(git rev-parse --short HEAD) -t solectrac-fw .
```

The SHA is baked into the build and surfaced as `version` in `/json` so a
flashed board is identifiable; native `pio run` reads it from the working tree
automatically. Omit the build-arg to ship `unknown`.

Extract the build artifacts onto the host:

```bash
docker run --rm -v "$PWD/out:/out" solectrac-fw
# -> out/{bootloader,partitions,boot_app0,firmware}.bin
```

Then flash from the host with `esptool` (from a Python 3.13 venv). These are the
standard Arduino-ESP32 offsets — the same four images, at the same offsets, that
`pio run -t upload` writes:

```bash
~/.venvs/pio/bin/pip install esptool
~/.venvs/pio/bin/esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX \
    --baud 921600 write_flash \
    0x0     out/bootloader.bin \
    0x8000  out/partitions.bin \
    0xe000  out/boot_app0.bin \
    0x10000 out/firmware.bin
```

> If `write_flash` can't connect, hold **BOOT-0**, tap **RST**, release
> **BOOT-0** to force the board into download mode, then retry.

To override the credentials in a Docker build, pass them as build args:

```bash
set -a; source esp32-s3/.env; set +a
docker build -f esp32-s3/Dockerfile \
    --build-arg AP_SSID="$AP_SSID" --build-arg AP_PASS="$AP_PASS" \
    --build-arg MDNS_NAME="$MDNS_NAME" \
    --build-arg WIFI_SSID="$WIFI_SSID" --build-arg WIFI_PASS="$WIFI_PASS" \
    --build-arg GIT_SHA=$(git rev-parse --short HEAD) \
    -t solectrac-fw .
```

## Customizing the WiFi AP and mDNS hostname (optional)

The board always broadcasts its own hotspot (so it's reachable in the field).
By default the hotspot is **SSID `tractor` / password `electricity`** and the
board advertises itself as **`tractor.local`** — if you don't do anything here,
the build is unchanged.

If you also set `WIFI_SSID` / `WIFI_PASS`, the board additionally joins that
network as a station for bench use (dual AP+STA mode). Leave them empty for an
AP-only build — the recommended field setup, since the AP and station share one
radio and a station endlessly scanning for an out-of-range network makes the
hotspot drop in and out.

To override the AP credentials, mDNS hostname (and/or set a bench network), copy
the template to a gitignored `.env` and set values:

```bash
cp .env.example .env
# set a strong AP_PASS (WPA2 requires 8-63 chars), e.g.:
python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(24)))"
```

Then source it before building (native or Docker):

```bash
set -a; source .env; set +a
pio run -e lilygo_t2can          # native; or use the Docker --build-arg form above
```

`AP_SSID`/`AP_PASS`/`MDNS_NAME` are injected at build time by
`inject_build_overrides.py` (a no-op when the env vars are unset), so the
default build is unchanged.

## Changing the station WiFi at runtime (`/wifi`)

The station (home/shop network) credentials can also be changed **without
reflashing**, from a web form the board serves at `/wifi`. Reach the board —
join its always-on `tractor` AP, or hit it on the current network — open
`http://tractor.local/wifi` (or `http://<board-ip>/wifi`), enter the new SSID
and password plus the **AP password** (`AP_PASS`, the gate that authorizes the
change), and save. It applies **live**, no reboot; the always-up AP is the
recovery path if you mistype. Only the station credentials are settable here —
the AP SSID/password and mDNS name stay compile-time. An empty SSID disables the
station (AP-only).

**Persistence and precedence — read this before you get surprised.** The
credentials are stored in **NVS**, a separate flash partition, so they **survive
a normal reflash** (`pio run -t upload` rewrites only the app partition). At boot
the firmware reads NVS **first** and falls back to the compiled
`WIFI_SSID`/`WIFI_PASS` only when NVS is unprovisioned. So once you set
credentials through the form, they **take precedence over any baked-in
`WIFI_SSID`/`WIFI_PASS`** — reflashing with *different* compiled credentials will
**not** change the network the board joins. To return to the compiled defaults,
re-enter them through the form or fully erase flash (`esptool erase_flash`, or
`pio run -t erase`), which clears NVS.

> No pre-apply scan is done (deliberate simplicity): a mistyped SSID makes the
> station scan endlessly and destabilizes the shared-radio AP, and because it
> persists in NVS the degradation survives a reboot until you correct it via the
> form. Type carefully.

## Endpoints

Once the board is on the network it advertises itself as `tractor.local`
via mDNS.

| URL / Port | Purpose |
|---|---|
| `http://tractor.local/` | Auto-refreshing dashboard |
| `http://tractor.local/json` | Decoded state as JSON |
| `http://tractor.local/config` | Build + WiFi diagnostics as JSON (board, firmware version, features, STA/AP status) |
| `http://tractor.local/wifi` | Web form to set the station WiFi SSID/password at runtime (AP-password gated) |
| `tractor.local:28600` | socketcand TCP stream of raw CAN frames |
| `/dev/cu.usbmodem*` (USB CDC) | SLCAN stream of raw CAN frames |
| `http://tractor.local/sd/status` | SD logging status + diagnostics (RejsaCAN only) |
| `http://tractor.local/sd/sessions` | SD session/file inventory as JSON |
| `http://tractor.local/sd/sessions/N` | `GET` whole session as a `.tar`; `DELETE` removes it |

### Consuming raw frames with `python-can`

Over USB (SLCAN):

```bash
python -m can.viewer -i slcan -c /dev/cu.usbmodem14301 -b 250000
```

Over WiFi (socketcand). Note that `can.viewer`'s CLI silently ignores extra
interface kwargs, so use `can.logger` (or a Python snippet) when you need to
pass `host` / `port`:

```bash
uv run python -m can.viewer -i socketcand -c can0 --bus-kwargs host=tractor.local port=28600
```

On the **T-2CAN** both buses are exposed on the same port; pick which one by
changing the `-c` channel. Run two clients to log both at once:

```bash
uv run python -m can.logger -i socketcand -c can0 --bus-kwargs host=tractor.local port=28600 &
uv run python -m can.logger -i socketcand -c can1 --bus-kwargs host=tractor.local port=28600
```

One client per channel is allowed; a new connection on a busy channel is
refused so the existing clients aren't disturbed.

## SD-card session logging (RejsaCAN only)

The RejsaCAN-ESP32-S3 has an onboard microSD reader (SPI: SCK 39, MOSI 40,
MISO 41, CS 45 — pins that dodge the N16R8 module's octal-PSRAM lines). When a
FAT-formatted card is present **at boot**, the firmware records the whole
session to it; with no card it runs exactly as a card-less tap (zero overhead,
no writer task). A card inserted after boot is *not* picked up — reboot to use
it. The two other boards have no card reader and are unaffected.

Each power-on is one session directory `/sNNNNN/` (index = highest existing + 1),
containing two streams, split into 64 MB parts:

| File | Contents |
|---|---|
| `can_PP.asc` | every received CAN frame, Vector ASCII — replayable by the Python tools unchanged |
| `data_PP.jsonl` | one full `/json` snapshot per line at `SD_JSON_HZ` (default **1 Hz**) |

- **Crash-safe-ish:** the writer `fsync`s every ~1 s (`SD_FLUSH_MS`), so any
  power cut, reset, or crash loses at most ~1 s of not-yet-synced data.
- **Never fills the card:** when free space drops below `SD_MIN_FREE_BYTES`
  (default 512 MB) the writer deletes the **oldest** session directory (never
  the active one) until it's back above the threshold.
- **Never stalls the bus:** all SD I/O runs on a dedicated core-0 task fed by
  PSRAM ring buffers; `loop()` only enqueues bytes. If a card stall fills a
  ring, whole lines are dropped and counted (`sd.raw_dropped` / `sd.json_dropped`
  in `/json`) rather than blocking the CAN receive path.

Logging status shows in `/json` under `sd` (state, session, KB written, free MB,
drops) and on the dashboard footer; full diagnostics (`raw_part`, `json_part`,
`recoveries`, `fail_op`, `fail_kb`) live on `/sd/status`. Tunables are `#define`s at the top of the
"SD-card session logging" section in `main.cpp` (`SD_JSON_HZ`, `SD_FLUSH_MS`,
`SD_MAX_PART_BYTES`, `SD_MIN_FREE_BYTES`, ring sizes).

**Pulling data off and replaying it** — pop the card into a reader, then the raw
log feeds the existing tools directly:

```bash
python3 solecan-analyze.py -o out /Volumes/SD/s00007/can_00.asc   # decode → CSVs
python3 solecan-stream.py --replay /Volumes/SD/s00007/can_00.asc  # TUI replay
jq . /Volumes/SD/s00007/data_00.jsonl | less                       # decoded timeline
```

The `.jsonl` is a convenience timeline; the `.asc` is the full-fidelity record
(you can always regenerate the decoded view from it with `solecan-analyze.py`).

### Pulling files over WiFi

The card never has to leave the tractor — the firmware serves the sessions
over HTTP while logging continues (SD access is mutex-shared with the
writer; the PSRAM rings absorb the brief stalls):

```bash
curl http://tractor.local/sd/status               # logging state + diagnostics
curl http://tractor.local/sd/sessions             # sessions and their files
curl -O -J http://tractor.local/sd/sessions/7     # download session 7 → s00007.tar
curl -X DELETE http://tractor.local/sd/sessions/7 # delete session 7
```

Downloads are uncompressed USTAR archives with an exact `Content-Length`.
The **active** session is downloadable too: member sizes freeze when the
transfer starts, so you get a consistent snapshot that trails the live
session by up to ~1 s (the writer's flush cadence). If the card errors
mid-transfer the stream is truncated and the socket closed, so the client
detects a short read against `Content-Length` rather than a silently
zero-filled file. Deleting the active
session is refused (`409`); `/sd/sessions` and `/sd/sessions/N` answer `503` when
no card was present at boot or logging has latched an error, while
`/sd/status` always answers `200` and reports that state.

On the default build the board deep-sleeps after 10 minutes of CAN silence
(wake-on-CAN only — see "Energy-saving deep sleep" in `main.cpp`), so it is
only reachable over WiFi while the bus is live or was recently. Serving any
`/sd` (or other real HTTP) request defers that sleep, so an in-progress
download or delete won't be cut off; but if the tractor has been off for more
than 10 minutes the board is already asleep and won't answer until CAN traffic
resumes. Build with `-DNO_AUTOSHUTDOWN` to keep it awake through bus silence.

## Pre-ship bench test

`device-test.py` is an acceptance suite to run against each flashed device
before it ships. From a bench Mac it exercises every external interface: the
HTTP dashboard and `/json` (including firmware version, CAN health counters,
and the RejsaCAN 12 V sense), the captive-portal redirect, mDNS, socketcand
slot handling, SLCAN over USB — including confirming that a listen-only build
answers injection attempts with BELL, the pre-ship proof that the passive-tap
guarantee is in the flashed image — and optionally BLE. With a bench CAN
adapter it also runs an end-to-end decode check: it injects synthetic J1939
frames covering every decoder path (pack, cells, temps, limits, faults,
motor, charger, DM1) and verifies the decoded engineering values in `/json`,
the raw-frame taps, and that `motor.alive` latches and goes stale correctly.

```bash
# WiFi-only smoke test: join the device's AP (SSID `tractor`), then from the
# repo root:
uv run python esp32-s3/device-test.py

# Full pre-ship run: USB serial + bench injector + ACK adapter + BLE +
# operator LED checks
uv run python esp32-s3/device-test.py \
    --serial /dev/cu.usbmodem101 \
    --inject-interface slcan --inject-channel /dev/cu.usbserial-A50 \
    --ack-interface canalystii --ack-channel 0 \
    --expect-version $(git rev-parse --short HEAD) \
    --ble --interactive
```

Stages whose prerequisite flag isn't given are reported as SKIP, so the
WiFi-only form is still a useful smoke test. The exit code is 0 only when
every executed check passes.

Bench notes:

- **Test one device at a time.** Every unit broadcasts the same AP SSID and
  mDNS name.
- **The injection stage needs an ACK node on the bench bus.** The device
  under test is listen-only and never ACKs, so a lone injector goes
  error-passive and retransmits its *first* frame forever while the rest
  queue behind it — none of the later fixtures reach the device. Wire any
  second adapter to the bench bus and pass it as
  `--ack-interface`/`--ack-channel`; the suite opens it in normal mode
  purely to provide hardware ACKs. The suite probes for the no-ACK
  condition right after the first frame and skips the decode sweep with a
  single clear failure instead of a wall of misleading ones.
- **Terminate the bench bus** (~120 Ω between CANH and CANL) and connect
  ground between the adapter and the board.
- **Never run the injection stage with the device wired to the tractor** —
  the bench adapter would spoof BMS/motor frames onto the real bus.
- **Pass `--expect-vin` only when the board is powered from a 12 V supply.**
  On USB power the RejsaCAN rail sense reads ~4.6 V (5 V minus the
  protection-diode drop), which would fail a 12 V expectation.
- `--ble` needs the `bleak` package (`uv pip install bleak`); it is not a
  project dependency.
- On a T-2CAN pass `--channels 2` so the socketcand channel checks match the
  board.

## Source layout

```
esp32-s3/
├── platformio.ini          # board envs + build configuration
├── boards/                 # custom board JSONs (LilyGo T-2CAN)
├── copy_dashboard.py       # pre-build: copies repo-root dashboard.html → src/
├── inject_build_overrides.py # pre-build: injects AP_SSID / AP_PASS / MDNS_NAME
├── device-test.py          # bench acceptance suite for a flashed device
├── README.md               # this file
└── src/
    ├── main.cpp            # all firmware code (decode, HTTP, SLCAN, socketcand, LED)
    └── dashboard.html      # copied in by copy_dashboard.py; gitignored
```
