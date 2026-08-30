# Kelly FTDI USB mode — design

**Date:** 2026-08-30
**Target:** `esp32-s3` firmware, RejsaCAN board only (`ENABLE_KELLY`)
**Status:** approved design, pre-implementation

## Goal

Add a `kelly-ftdi` USB port role that makes the device enumerate as an **FTDI
FT232R**, so the official Kelly Android app can reach the KLS pump controller
through the SoleCAN device over USB-OTG.

## Background

The Kelly app offers two transports: the Classic-SPP Bluetooth dongle
(`kelly/bluetooth-adapter.md`) and a wired **FT232** path over USB-OTG. The
wired path identifies candidate adapters by USB vendor ID and accepts only
FTDI's `0x0403`. A CH340 (`0x1A86`) is invisible to it — confirmed on the bench
2026-08-30, which is why selecting FT232 in the app fails instantly with a
CH340 attached.

This matters for SoleCAN specifically because **the ESP32-S3 has no Classic
Bluetooth radio**, so it can never impersonate the SPP dongle
(`kelly/bluetooth-adapter.md`, "A BLE variant"). USB-OTG as an FT232 is the only
route by which an S3 can serve the Kelly app at all.

The data path already exists. `USB_KELLY` (`main.cpp:470`) is a transparent
USB↔Kelly bridge over the CDC console. What is missing is only how the board
*presents itself* on USB.

### Why this is not just another `UsbMode`

`logging`, `slcan`, and `kelly` all share one CDC interface and switch only data
routing, so they can flip at runtime. FTDI is a **vendor-class** device: its
descriptors are fixed at enumeration, and its host driver speaks a proprietary
control protocol rather than CDC.

Worse, the two USB personalities live on different silicon. The firmware builds
with `-DARDUINO_USB_MODE=1`, selecting the S3's **USB-Serial-JTAG** peripheral,
whose descriptors are in ROM and cannot be changed. FTDI emulation requires the
**USB-OTG** peripheral driven by TinyUSB. Both exist on the chip and share one
internal PHY; only one may own it at a time.

## Decision

Route the PHY **at boot**, chosen from a persisted setting, and leave the
existing hardware-CDC path untouched for every other mode.

This was chosen over moving the whole firmware to TinyUSB (`ARDUINO_USB_MODE=0`)
because that would cost a real debuggability property: with hardware CDC, USB
enumerates from ROM and a serial port still appears when the sketch hangs. Under
TinyUSB the firmware drives USB, so a hung loop makes the port vanish — a poor
trade on a device running CAN, WiFi, BLE, and USB emulation concurrently.

### Feasibility, verified

Against the pinned platform (pioarduino 55.03.39 = Arduino core 3.3.9 / IDF
5.5.4):

- `esp_private/usb_phy.h` is present in the S3 lib set.
- `hal/usb_phy_types.h:35-37` defines `USB_PHY_CTRL_OTG` and
  `USB_PHY_CTRL_SERIAL_JTAG`.
- `libarduino_tinyusb.a` is prebuilt and links even though the core is built for
  hardware CDC.

## Architecture

### 1. Mode and persistence

Add `USB_KELLY_FTDI` to `UsbMode`, name `kelly-ftdi`, gated on `ENABLE_KELLY`
exactly as `kelly` is (`main.cpp:485-492`).

Unlike the other three roles it **persists to NVS**, because the PHY choice is
made once at boot. This deliberately breaks the existing invariant that the USB
role is RAM-only and "a power cycle always returns to logging"
(`main.cpp:465-466`) — for this mode only. The comment there must be updated to
say so; a future reader who trusts the old invariant will be wrong.

- NVS namespace `usb`, key `mode`, storing the mode name.
- Selecting `kelly-ftdi` writes NVS and reboots.
- `/usb` gains a control that sets the mode back to `logging` and reboots. This
  is the escape hatch: in FTDI mode there is no CDC console to type into.

**Two refusals**, both returning the existing "unknown mode" error path:

- Rejected on `NO_WIFI` builds. No dashboard means no way back.
- Rejected on non-Kelly builds, mirroring `kelly`.

### 2. Boot-time PHY routing

Early in `setup()`, before `Serial.begin()`:

- NVS says `kelly-ftdi` → `usb_phy_new()` with `USB_PHY_CTRL_OTG`, then TinyUSB
  init with the FT232R descriptors. Arduino `Serial` is never started.
- Anything else → **do nothing**. The USB-Serial-JTAG path is untouched, so all
  existing boards and modes are bit-for-bit unaffected.

### 3. FTDI device emulation

New `src/ftdi_device.cpp` / `src/ftdi_device.h`. `main.cpp` is ~3500 lines and
this is a self-contained unit with one job; it does not belong there.

**Descriptors**

| Field | Value |
|---|---|
| `idVendor` | `0x0403` |
| `idProduct` | `0x6001` |
| `bcdDevice` | `0x0600` (FT232R — drivers key baud math off this) |
| Class | vendor-specific, one interface, no class descriptors |
| Endpoints | bulk IN + bulk OUT, 64-byte max packet (full speed) |
| `iManufacturer` | `FTDI` |
| `iProduct` | `FT232R USB UART` |
| `iSerialNumber` | derived from the ESP32 MAC |

The serial number is not cosmetic. Hosts name the device node from it, so a
stable serial yields a stable `/dev/cu.usbserial-*` across replugs — the exact
property whose absence on the CH340 caused a port rename to masquerade as dead
hardware during the 2026-08-30 bench session.

**Control requests** via `tud_vendor_control_xfer_cb`, `bmRequestType` `0x40`
(out) / `0xC0` (in):

| `bRequest` | Name | Handling |
|---|---|---|
| `0x00` | `RESET` | flush ring buffers, ACK |
| `0x01` | `MODEM_CTRL` | ACK |
| `0x02` | `SET_FLOW_CTRL` | ACK (no flow control on the Kelly link) |
| `0x03` | `SET_BAUD_RATE` | decode divisor, apply to `Serial1` |
| `0x04` | `SET_DATA` | decode frame format, apply to `Serial1` |
| `0x05` | `GET_MODEM_STATUS` | return the 2 status bytes |
| `0x06` | `SET_EVENT_CHAR` | ACK |
| `0x07` | `SET_ERROR_CHAR` | ACK |
| `0x09` | `SET_LATENCY_TIMER` | store |
| `0x0A` | `GET_LATENCY_TIMER` | return stored value (default 16 ms) |

Most are accept-and-ignore. The requirement is that none of them NAK — a stalled
control transfer makes the host abandon the device.

**Baud divisor.** FT232R base clock is 3 MHz. `wValue` holds the low 16 bits of
the divisor, `wIndex` the high bits; the top 3 bits select a fractional part
from `{0, 0.5, 0.25, 0.125, 0.375, 0.625, 0.75, 0.875}`. 19200 encodes as
integer 156 with fraction `0.25` (3000000/19200 = 156.25). Decode and apply
rather than hardcoding 19200, so the host genuinely controls the wire rate.

**The 2-byte status prefix.** Every bulk IN packet begins with two status bytes
— modem status then line status, conventionally `0x01 0x60` — before any
payload, capping payload at **62 bytes per 64-byte packet**. A real FT232 also
emits status-only packets when idle, at the latency-timer interval.

This is the single most likely source of a subtle bug. Getting it wrong does not
prevent enumeration; it silently corrupts or drops two bytes per packet, which
presents as "the port opens and data looks almost right" — precisely the failure
shape that consumed the 2026-08-30 session.

### 4. Data flow

```
Kelly app / host  <-- USB bulk -->  ring buffers  <-->  Serial1 (GPIO47 RX / GPIO48 TX)
```

`kellyPoll()` is suspended while the mode is active, mirroring what `USB_KELLY`
already does — the host owns the link and the monitor must not inject its own
queries.

## Error handling

- **`usb_phy_new()` fails** → fall back to logging mode, leave USB-Serial-JTAG
  alone, and record the failure in `/json`. It must be diagnosable without a
  console.
- **NVS holds an unparseable mode** → treat as `logging`.
- **Mode selected on a build that refuses it** → existing unknown-mode error;
  NVS is not written.
- **Host never enumerates** → no special handling. The device is still fully
  reachable over WiFi and BLE; the dashboard is the way out.

## Testing

**Acceptance gate.** Jumper the Kelly UART pins (GPIO47 ↔ GPIO48) into a
loopback, set the mode, and plug into a Mac. Both must hold:

1. It enumerates via Apple's built-in FTDI driver as `/dev/cu.usbserial-*`.
2. `loopback.py` returns **all 256 byte values, byte-for-byte identical**.

A port appearing is explicitly **not** sufficient. The status-prefix bug
produces a working-looking port that corrupts data, so only a full-range
byte-exact round trip closes this out.

**`device-test.py`** gets a small stage: the mode is settable, `/usb` reports
it, and it is rejected on non-Kelly builds. The suite cannot verify enumeration
— its own console disappears in this mode — so the stage must set the mode back
to `logging` before finishing, as the SLCAN stage already does
(`device-test.py:810`).

**Deferred to hardware:** end-to-end validation against the Kelly Android app,
which needs a USB-C OTG adapter. The Mac gate is the proxy until then.

## Risks

- **`esp_private/usb_phy.h` is a private ESP-IDF header.** Acceptable because
  `platformio.ini` pins the platform, but a platform bump can break the build.
  The pin comment should mention this new dependency.
- **The FT232R protocol is documented only informally**, via `libftdi` and the
  Linux `ftdi_sio` source. There is no vendor specification to check against.
- **EEPROM reads (`0x90`) are unhandled.** Linux's `ftdi_sio` does not need
  them; `libftdi` and possibly the Kelly app do. If the app enumerates but
  refuses, this is the first thing to add.
- **Untested against the actual consumer.** We have neither a genuine FT232 to
  characterize nor a confirmed-working app session, so "what the app requires"
  is inferred rather than observed.

## Out of scope

- Any change to `logging`, `slcan`, or `kelly` behavior.
- FTDI emulation on non-Kelly boards.
- Runtime switching without a reboot.
- The composite CDC+FTDI device — rejected, as it would make every board
  advertise FTDI's vendor ID and presents a device shape FTDI drivers are not
  written for.

## Files touched

| File | Change |
|---|---|
| `esp32-s3/src/ftdi_device.h` | new — public surface: init, poll, mode query |
| `esp32-s3/src/ftdi_device.cpp` | new — descriptors, control requests, bulk pump |
| `esp32-s3/src/main.cpp` | enum + name/parse, NVS load/save, boot PHY routing, `/usb` control, suspend `kellyPoll()` |
| `esp32-s3/device-test.py` | mode-settable stage |
| `esp32-s3/README.md` | document the mode, the reboot, and the escape hatch |
| `kelly/README.md` | note the app's two transports and the FT232 vendor-ID requirement |
