# Kelly KLS e-hydraulic pump controller — serial diagnostics & monitor

The Solectrac e25G's **e-hydraulic pump** is driven by a **Kelly KLS7212M /
KLS7218** controller (per the parts catalog). It runs the BLDC pump motor for
power steering, the lift, and the PTO wet clutch. It is a *separate* controller
from the main-bus ECUs and is **not** on the J1939 CAN bus — the main-bus
topology and why the hydraulics don't appear there live in
`../DOCUMENTATION.md` §"What is NOT on this bus".

This directory documents the Kelly's **4-pin serial diagnostic port** and
provides `solectrac-kelly-monitor.py`, a read-only live monitor for it.

## Connector and wire protocol

The 4-pin port is an **SM-4P connector** speaking Kelly's proprietary "ETS"
serial protocol, not CAN. Pin functions from the KLS-S manual (Figure 13) —
CONFIRMED:

| Pin | Signal | Wire color | Direction / wiring                                       |
|-----|--------|------------|----------------------------------------------------------|
| 1   | V+     | red        | ~12 V supply out of the controller; leave unconnected    |
| 2   | Tx     | green      | Controller → host; wire to the adapter's **RX**          |
| 3   | Rx     | blue       | Host → controller; wire to the adapter's **TX**          |
| 4   | V−     | black      | Ground / signal return; wire to the adapter's **GND**    |

Wire colors on this tractor's harness run **red, green, blue, black** in pin
order — all four CONFIRMED. **Red = V+ (12 V)** and **black = V− (ground)** are
metered (the supply pair) and sit at pins 1 and 4, the two ends. **Green = Tx
(pin 2)** and **blue = Rx (pin 3)** are confirmed by a live decode: with green
driving the host's RX (through the series resistor) and blue on the host's TX,
the controller returns valid checksummed telemetry.

Pin 1's supply measures **~12 V** across V+/V− on this tractor — CONFIRMED by
meter, not the ~5 V the pin was previously assumed to carry. Leave it
unconnected, and in particular never wire it to a TTL adapter's VCC or a
level-shifter's HV reference: 12 V there will destroy the part. This is a
low-voltage supply rail and is unrelated to the Tx/Rx logic swing (5 V TTL —
see below).

Wire protocol is **19200 baud, 8N1**, framed as
`[CMD][LEN][DATA 0..16][CHECKSUM]` in 19-byte frames, checksum
`(CMD + LEN + sum(DATA)) & 0xFF`. Live monitor uses commands `0x3A`/`0x3B`/`0x3C`
(each a zero-data query returning 16 bytes); concatenated they form a 48-byte
telemetry block. Flash config is a separate 512-byte read/write command set.
The community `kelly-connect-oss` project reimplements this protocol and
documents the field offsets.

**Signal level is logic-level (TTL) UART, not bipolar RS-232 — CONFIRMED.** A
full bidirectional monitor session succeeded through a bare CH340 (TTL)
USB-serial adapter: the controller received the query commands and returned
valid checksummed frames, which a TTL adapter could not do against an RS-232
port. So a plain TTL USB-UART works directly and no MAX3232 is needed.

The logic swing measures **~5 V** on Tx and Rx (to V−) under active traffic —
CONFIRMED, so this is **5 V TTL**. The line idles high near 5 V and pulses to
0 V. That's fine for a 5 V-friendly CH340, but it matters when driving a 3.3 V
ESP32 GPIO, which is not 5 V-tolerant: put a **~1–2.2 kΩ resistor in series** on
the Kelly-Tx → ESP32-RX line (or a BSS138 level-shifter on that line). The
resistor is not a divider — the ESP32's internal clamp diode holds the pin at
3.3 V and the series resistor limits clamp current to <1 mA. The other direction
(ESP32-TX → Kelly-Rx) is a plain wire; 3.3 V clears the Kelly's input-high
threshold. The controller only talks with PWR above ~18 V, so telemetry is
available only when the tractor is powered.

The protocol is single-master request/response — sniff passively or be the only
talker. The flash-write command set can misconfigure the controller; capture
those frames for reference, never replay them.

## Live findings (CONFIRMED)

- **Powered from the traction pack.** B+ reads pack voltage (77 V observed
  mid-SOC), consistent with the F100F3 pack-voltage decode on the main bus.
- **Two-setpoint speed control, not throttle-modulated.** The dash hydraulic
  on/off switch works through the throttle-pot input (TPS): it reads 0 with the
  switch off (pump stopped) and a fixed ~253/255 full-scale command with it on.
  Pump speed is selected by the Kelly **"Low Speed" digital input**: Low Speed
  asserted → **~2400 RPM**; deasserted → **~2800 RPM** (overshoots on the
  upward step before the speed loop settles).
- **The dash lift switch is the speed selector.** Flipping the lift switch
  deasserts Low Speed and moves the pump to the high setpoint — this is the
  "LOW/HIGH speed-selection switch" of schematic 5.11.
- **Direction is fixed forward** (Forward Switch asserted; commanded and actual
  direction always agree).
- **Motor and controller temperature sensors are fitted and live** (both track
  warm-up in °C); the motor sensor is a KTY84-130 per the KLS-S manual.
- Steady-state phase current with the pump unloaded is **22–25 A** at both
  setpoints.
- The Kelly **Brake Switch input reads asserted** whenever the controller is
  awake — including with the hydraulic switch off and the pump stopped, which
  rules out the hydraulic-motor on/off switch as its source.

## AC Monitor screen — observed conditions

The Kelly app's "AC Monitor" screen, captured under three switch conditions.

**1. Hydraulic switch on, lift switch LOW — pump at the low setpoint (~2400 RPM).**
Low Speed asserted (1), TPS pegged at full command (253), ~25 A phase current.

```
┌─────────────────────────────────────────────────────────────────┐
│  AC Calibration            AC Monitor                           │
├─────────────────────────────────────────────────────────────────┤
│  Error Status    (empty — no active faults)                     │
├─────────────────────┬─────────────────────┬─────────────────────┤
│ TPS Pedel       253 │ Hall A            1 │ Setting Dir       0 │
│ Brake Pedel       0 │ Hall B            1 │ Actual Dir        0 │
│ Brake Switch      1 │ Hall C            0 │ Brake Switch2     0 │
│ Foot Switch       0 │ B+ Volt          77 │ Low Speed         1 │
│ Forward Switch    1 │ Motor Temp       17 │ Motor Speed    2427 │
│ Reversed          0 │ Controller Temp   7 │ Phase Current    25 │
└─────────────────────┴─────────────────────┴─────────────────────┘
```

**2. Same, lift switch flipped to HIGH — pump steps to the high setpoint (~2800 RPM).**
Low Speed deasserts (0); RPM rises after a brief overshoot, phase current eases
to ~22 A, and motor/controller temps tick up as they warm.

```
┌─────────────────────────────────────────────────────────────────┐
│  AC Calibration            AC Monitor                           │
├─────────────────────────────────────────────────────────────────┤
│  Error Status    (empty — no active faults)                     │
├─────────────────────┬─────────────────────┬─────────────────────┤
│ TPS Pedel       253 │ Hall A            1 │ Setting Dir       0 │
│ Brake Pedel       0 │ Hall B            1 │ Actual Dir        0 │
│ Brake Switch      1 │ Hall C            0 │ Brake Switch2     0 │
│ Foot Switch       0 │ B+ Volt          77 │ Low Speed         0 │
│ Forward Switch    1 │ Motor Temp       18 │ Motor Speed    2788 │
│ Reversed          0 │ Controller Temp   9 │ Phase Current    22 │
└─────────────────────┴─────────────────────┴─────────────────────┘
```

**3. Hydraulic switch off — pump stopped.**
TPS reads 0, Motor Speed and Phase Current 0. Halls show a static parked-rotor
state (1/0/1). Low Speed is still 1 (lift switch left in low), and Brake Switch
stays asserted (1) as it does whenever the controller is awake.

```
┌─────────────────────────────────────────────────────────────────┐
│  AC Calibration            AC Monitor                           │
├─────────────────────────────────────────────────────────────────┤
│  Error Status    (empty — no active faults)                     │
├─────────────────────┬─────────────────────┬─────────────────────┤
│ TPS Pedel         0 │ Hall A            1 │ Setting Dir       0 │
│ Brake Pedel       0 │ Hall B            0 │ Actual Dir        0 │
│ Brake Switch      1 │ Hall C            1 │ Brake Switch2     0 │
│ Foot Switch       0 │ B+ Volt          77 │ Low Speed         1 │
│ Forward Switch    1 │ Motor Temp       18 │ Motor Speed       0 │
│ Reversed          0 │ Controller Temp   7 │ Phase Current     0 │
└─────────────────────┴─────────────────────┴─────────────────────┘
```

## Under load — single-session observations

From one ~9-minute capture of a real work cycle (loader and driving, then PTO
mowing, all at the low speed setpoint) — TENTATIVE:

- **~25 A standing draw whenever the pump is on**, with hydraulic demand stacked
  on top: steering and bucket lifts spike phase current to ~70 A, while
  sustained PTO mowing sits around 25–30 A. The pump runs continuously through
  the cycle — stopping or reversing the tractor doesn't unload it, since it
  keeps serving steering and the PTO wet-clutch pressure and cooling. (Tractor
  reverse doesn't appear here at all: the pump direction is fixed, and travel
  direction is the separate traction motor on the CAN bus.)
- **The motor is the thermally-limited part.** Under continuous running the
  controller temperature levels off near 18 °C, while the motor temperature
  keeps climbing (past 40 °C over the run, still rising) — where it plateaus is
  not yet captured.

## The monitor tool

`solectrac-kelly-monitor.py` polls the three monitor commands over a USB-serial
**or Bluetooth** adapter and prints the decoded block — as plain text, JSON, or
a `--tui` view laid out like the AC Monitor screen above.

### Read-only by design

The tool only ever transmits the live-monitor and code-version query commands
(`0x11`, `0x3A`, `0x3B`, `0x3C`). Every outbound frame passes through a single
`_transmit()` guard that refuses any command outside that allowlist, and no
flash session is ever opened — so it cannot write, erase, or reconfigure the
controller.

### Usage

```bash
# list serial ports
python3 solectrac-kelly-monitor.py

# live monitor (plain text, 0.5 s poll)
python3 solectrac-kelly-monitor.py --port /dev/cu.usbserial-XXXX

# TUI laid out like the app's AC Monitor screen
python3 solectrac-kelly-monitor.py --port /dev/cu.usbserial-XXXX --tui

# one reading with the raw 48-byte block
python3 solectrac-kelly-monitor.py --port /dev/cu.usbserial-XXXX --once --raw

# machine-readable, one JSON object per reading
python3 solectrac-kelly-monitor.py --port /dev/cu.usbserial-XXXX --json
```

Needs `pyserial`; `--tui` also needs `rich`. Both are declared in
`../pyproject.toml` (`uv sync`, or `pip install pyserial rich`).

### Bluetooth (SPP)

The Kelly Bluetooth adapter is a Classic **SPP** serial bridge, so no special
mode is needed — on macOS it appears as a serial port named after the device
(e.g. `/dev/cu.26061702`); just pass it as `--port`. Confirmed working
end-to-end (a CH340 USB adapter and this SPP adapter decode identically).

Bluetooth links are less steady than USB, so the reader is built to cope:

- **Warmup.** Opening the port brings the SPP link up, which takes a second or
  two; the first few polls miss and are tolerated silently (a one-line "waiting
  for controller…" note, not an error). `--once` waits through warmup.
- **Auto-reconnect.** If the link drops mid-session, the reader reopens the port
  and continues rather than crashing.

macOS gotchas, learned the hard way:

- **Find the right port.** The adapter's port is named after its device name
  (e.g. `/dev/cu.26061702`, from `ls /dev/cu.*`).
- **The dashboard's "Connected → Not Connected" flicker is normal.** A macOS SPP
  link is only held while an app has the port open, so clicking Connect in
  Bluetooth Settings shows Connected for a second and then drops. Don't fight it
  — the link comes up (and stays up) when the tool opens the port; the Settings
  pane can't hold it.
- **One host at a time.** These adapters accept a single connection, so
  disconnect the phone / Kelly app (turn the phone's Bluetooth off) before the
  Mac can use it.

### Validate before trusting the decode

The field offsets and scalings come from the `kelly-connect-oss` `PROTOCOL.md`
and match live readings on this tractor, but scalings can differ by firmware.
The first time, compare the tool's output against the Kelly app on the same
controller. If a field is off, correct its offset/scale in `Monitor.decode`.
