# Solectrac CAN Monitor (ESP32)

Firmware that reads J1939 CAN frames from a Solectrac e25G electric tractor and
exposes the decoded state in four different ways:

- A mobile-friendly HTML dashboard over WiFi
- A JSON endpoint for scripting
- BLE JSON output
- Raw CAN frames over USB (SLCAN)
- Raw CAN frames over WiFi (socketcand)

## Supported hardware

The firmware builds for two ESP32-S3 boards. Pick the one you have; both speak
the same J1939 bus at 250 kbit/s (29-bit extended frames).

| Board | PlatformIO env | CAN TX | CAN RX | Status LED |
|---|---|---|---|---|
| Adafruit ESP32-S3 Reverse TFT Feather + CAN Pal | `adafruit_feather_s3` | GPIO 8 (A5) | GPIO 14 (A4) | NeoPixel on GPIO 33 (power-gated by GPIO 21) |
| LilyGo T-2CAN (CAN B / native TWAI) | `lilygo_t2can` | GPIO 7 | GPIO 6 | none |

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
- The pin map lives in `src/main.cpp` under `BOARD_ADAFRUIT_FEATHER_S3` /
  `BOARD_LILYGO_T2CAN`. Build environments are defined in `platformio.ini`.

## Wiring the LilyGo T-2CAN to the tractor

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
bus side has its own ground reference. The ground wire is **not optional** — without
tying OBD pin 4 to the CAN-side ground the bus floats and no frames arrive. If
your board's terminal has a 120 Ω termination jumper for CAN B, leave it **off**:
the tractor bus is already terminated (it measures ~40 Ω), and this is a tap, not
a bus end.

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

## What the LED tells you

Adafruit Feather only — the LilyGo T-2CAN has no user LED, so these calls are
no-ops on that board.

| Pattern | Meaning |
|---|---|
| Red blink | CAN driver failed to initialize |
| Amber blink | Booted, waiting for WiFi |
| Dim white (solid) | WiFi connected, no CAN frames received recently |
| Green blink | CAN frames arriving on the bus |

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
