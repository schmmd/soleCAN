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
  arrive.
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

## What the LEDs tell you

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

1. **Install PlatformIO** — either the VS Code extension or the standalone CLI:

   ```bash
   pip install platformio          # or: brew install platformio
   ```

2. **Clone the repo and enter this folder**:

   ```bash
   git clone <repo-url>
   cd solectrac/embedded/esp32-s3
   ```

3. **Set WiFi credentials** as environment variables — the build embeds them
   into the firmware (the firmware refuses to compile without them):

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

## Endpoints

Once the board is on the network it advertises itself as `tractor.local`
via mDNS.

| URL / Port | Purpose |
|---|---|
| `http://tractor.local/` | Auto-refreshing dashboard |
| `http://tractor.local/json` | Decoded state as JSON |
| `tractor.local:28600` | socketcand TCP stream of raw CAN frames |
| `/dev/cu.usbmodem*` (USB CDC) | SLCAN stream of raw CAN frames |

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

## Source layout

```
esp32-s3/
├── platformio.ini          # board envs + build configuration
├── boards/                 # custom board JSONs (LilyGo T-2CAN)
├── README.md               # this file
└── src/
    ├── main.cpp            # all firmware code (decode, HTTP, SLCAN, socketcand, LED)
    └── dashboard.html      # embedded into firmware at build time
```
