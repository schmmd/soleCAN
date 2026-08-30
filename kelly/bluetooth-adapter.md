# Building a Kelly Bluetooth adapter

Kelly sells a Bluetooth dongle for the KLS controller's SM-4P diagnostic port.
This document describes how to build an equivalent one from ~$10 of parts.

The clone is a **transparent Classic-Bluetooth SPP ↔ UART bridge** that powers
itself from the diagnostic port. Nothing about the Kelly ETS protocol lives in
the adapter — it shovels bytes, and `solectrac-kelly-monitor.py` (or the Kelly
app, or the ESP32 firmware) does the decoding. See `README.md` for the port
pinout, the wire protocol, and the monitor tool.

## Why build one

- **It sidesteps the grounding problem.** The Kelly is powered from the traction
  pack, so its V− is not at chassis potential; a chassis-referenced wired
  receiver injects that noise into the signal reference and corrupts frames
  (`README.md` §"Grounding dominates reliability"). A Bluetooth adapter has **no
  ground wire to the host at all**, so the whole dongle floats with the traction
  pack by construction — no ADuM1201, no isolated supply, no barrier layout.

  This does **not** make `isolator.txt` obsolete. That build addresses a
  different case: the chassis-powered ESP32 reading the Kelly over wired UART,
  where both ends are real and must stay galvanically separated. Bluetooth only
  helps when the host is a laptop or phone.
- Spare/replacement for the official unit, at a fraction of the price.
- Fully documented, so it can be modified — e.g. swapped to BLE to match the
  transport this repo's Android app already speaks.

## The official adapter, as measured

Characterization of the genuine Kelly dongle. The electrical rows were measured
on a bench, the dongle powered from a 12 V supply with its data lines
unterminated; the protocol rows are deduced from live monitor sessions on the
tractor.

| Property | Value | Confidence |
|---|---|---|
| Bluetooth flavor | Classic **SPP** (appears as `/dev/cu.<name>` on macOS) | CONFIRMED |
| Device name | `26061702` — a bare 8-digit serial, no vendor string | CONFIRMED |
| MAC address | `20:24:06:19:06:77` | CONFIRMED |
| MAC vendor | **None** — `20:24:06` is not a registered IEEE OUI | CONFIRMED |
| Supply | SM-4P pin 1 (red, ~12 V out of the controller) | CONFIRMED |
| Idle current | **~10 mA** at 12 V, powered but unpaired | CONFIRMED |
| Connected current | not yet measured | UNKNOWN |
| TX idle level (dongle → Kelly Rx, blue) | **~4 V**, unloaded | CONFIRMED |
| Internal UART config | **19200 8N1** | CONFIRMED |
| Byte transparency | no added framing or escaping | CONFIRMED |
| Module part | unidentified — no OUI, no teardown yet | UNKNOWN |

Two readings worth interpreting:

**~10 mA idle** is low for an HC-05, which sits at 30–40 mA while discoverable
with its LED blinking. That points at a lower-power module (a BK3231-class part
such as a JDY-30/31) or one that idles more aggressively. It matters only for
regulator sizing — see the note under Power below.

**~4 V unloaded TX idle** is about a diode drop under 5 V, the signature of a
simple level-translation stage (a series diode or an NPN inverter) off a 5 V
rail rather than a true push-pull 5 V driver. The clone does **not** need to
reproduce this: 3.3 V drive into the Kelly's Rx is CONFIRMED working, by both
the CH340 adapter and the `esp32-bridge` sketch.

The unregistered MAC means no OUI lookup will ever name the module, and it also
means there is no vendor identity for a clone to fail to match.

**The UART config and transparency are settled by deduction, not measurement.**
`solectrac-kelly-monitor.py` has pulled valid checksummed frames off the tractor
through this dongle. The Kelly's wire rate is fixed at 19200 8N1, so the dongle's
UART must match it — any other rate puts garbage on the wire — and the bridge
must be byte-transparent, since added framing or escaping would break the 19-byte
frame checksum. A successful decode is proof of both.

Note that the *host* side proves nothing here. Over SPP the `/dev/cu.*` node is a
virtual port on an RFCOMM channel, and the baud rate set on it is inert — it does
not reach the wire. Only the module's own UART setting matters. This is exactly
why the clone's `AT+UART` value is the one that has to be right, and why setting
19200 in the monitor is a formality on a Bluetooth link even though it is load-
bearing on a CH340.

## Clone specification

Everything the adapter must do, distilled:

| | |
|---|---|
| **Power in** | 12 V from SM-4P pin 1, tens of mA |
| **UART** | 19200 8N1, no flow control, idle-high |
| **Adapter TX → Kelly Rx** (blue, pin 3) | 3.3 V is sufficient — plain wire |
| **Kelly Tx → adapter RX** (green, pin 2) | 5 V source into a 3.3 V input — **needs level shifting** |
| **Ground** | Kelly V− (black, pin 4) only |
| **V+ (red, pin 1)** | supply *input* to the dongle — never wire to a host |
| **Bluetooth** | Classic SPP slave, transparent, no added framing |

> **The Kelly only answers with PWR above ~18 V.** The dongle will power up,
> pair, and loop back long before the controller says anything. Pairing is not
> evidence the tractor is awake.

## Reference build

### Parts (~$10)

- **HC-05** Classic SPP module on the common ZS-040 breakout (or a **JDY-31**,
  a near drop-in and closer to the official dongle's power draw). Configure as
  a slave — the phone or laptop connects *to* it.
- **AMS1117-5.0** regulator module for 12 V → 5 V (see Power below).
- Resistors: **1 kΩ** and **2 kΩ**, for the RX divider.
- Optional: LED + 1 kΩ across the 5 V rail — lights only when the controller is
  powering pin 1, so it doubles as a "Kelly is awake" indicator.
- A **JST-SM 4-pin** pigtail to mate the tractor harness, small perfboard,
  heatshrink or a small project box.

> Confirm the connector before ordering. "SM-4P" is read here as JST-SM at
> 2.5 mm pitch, and the harness gender should be checked against the tractor —
> neither is verified in this repo. TENTATIVE.

### Power

The HC-05 breakout's VCC accepts 3.6–6 V and regulates to 3.3 V internally, so
feed it **5 V**. Its logic pins are 3.3 V.

Size the regulator for the *connected, discoverable* peak, not the official
dongle's 10 mA idle — an HC-05 will draw considerably more. At 40 mA the
regulator drops 7 V and dissipates ~0.28 W, which is why the **AMS1117-5.0**
(SOT-223) is the default here rather than the 78L05 that `isolator.txt`
specifies for its few-milliamp load. A TO-92 78L05 is rated to 100 mA and will
work, but runs warm at that dissipation; it is the better choice only with a
genuinely low-power module.

Do not use a switching buck module: there is no efficiency argument at these
currents, and it puts converter hash next to the UART lines.

### Wiring

```
  SM-4P (tractor harness)                    HC-05 / JDY-31 breakout

  pin 1  red   (~12 V) ──> AMS1117-5.0 ──> 5 V ──> VCC
  pin 4  black (V−)    ─────────┬──────────────────> GND
                                │
  pin 2  green (Kelly Tx, 5 V) ──┴─[ 1 kΩ ]──┬──> RXD  (3.3 V input)
                                             │
                                          [ 2 kΩ ]
                                             │
                                            GND

  pin 3  blue  (Kelly Rx) <──────────────────────── TXD  (plain wire, 3.3 V)
```

The 1 kΩ / 2 kΩ divider puts 5 V × 2/(1+2) = **3.33 V** at RXD.

> Use a real divider here, **not** the bare series resistor that `README.md` and
> the `esp32-bridge` sketch specify. That trick relies on the ESP32's documented
> internal clamp diode holding the pin at 3.3 V while the resistor limits clamp
> current — an HC-05's input has no such guarantee. The series resistor is
> correct for the ESP32 and wrong here.

The TX direction needs no protection: 3.3 V clears the Kelly's input-high
threshold (CONFIRMED).

### Module configuration

Put the module in AT mode (on a ZS-040 breakout, hold the small button or pull
EN/KEY high while applying power; the LED blinks slowly at ~2 s). Then, over a
USB-serial adapter at 38400 8N1 — the HC-05's AT-mode rate, which is *not* the
data rate you are about to set:

```
AT                      -> OK
AT+ROLE=0               -> slave
AT+UART=19200,0,0       -> 19200 baud, 1 stop bit, no parity
AT+NAME=26061702        -> optional; see below
AT+PSWD="1234"          -> whatever pairing PIN you prefer
```

`AT+UART` sets the rate used for *data* once connected — this is the one that
must be 19200 to match the Kelly.

**On the name:** any name works for `solectrac-kelly-monitor.py`, which just
takes whatever `/dev/cu.*` you point `--port` at. Whether the **Kelly Android
app** filters its device list by name is UNKNOWN. If it does, an 8-digit
numeric name in the official dongle's style is the thing to mimic. Setting the
name to exactly `26061702` while the real dongle is also powered will make the
two indistinguishable during pairing — pick a different serial if both are in
service.

### Assembly

1. Regulator, divider, and LED on the perfboard; module on headers so it can be
   pulled for reconfiguration.
2. Keep the SM-4P pigtail short and route it away from the pump phase leads.
   Twisting green with black helps.
3. Strain-relieve the pigtail through a perfboard hole, then sleeve the board in
   heatshrink or a small project box with the LED visible.
4. Label it — and if it carries a different name than the official dongle,
   put that name on the label.

Unlike the `isolator.txt` build, there is **no isolation barrier to respect** in
the layout. The whole board sits on the Kelly's floating V−, and the Bluetooth
link is the barrier.

## Verification

Each stage is testable without moving to the next.

**1. Bench, no tractor, no Kelly.** Power the board from a 12 V supply. It
should appear in a Bluetooth scan. Note the idle current for comparison against
the official dongle's 10 mA.

**2. Loopback.** Short the module's own TXD to RXD, pair, open the port, and
type — characters should echo. This proves the SPP path end to end. It does not
verify the baud (loopback is baud-agnostic, same UART both ways), only that the
bridge is alive and transparent.

**3. Against a USB-serial adapter.** Wire the finished dongle's SM-4P data pins
to a CH340 — dongle blue → CH340 RX, dongle green ← CH340 TX, grounds common
(safe on a bench supply; **not** on the tractor, where it would defeat the
isolation). Open both ports at 19200 and type each way. Clean characters confirm
the divider, the module's 19200 config, and byte-for-byte transparency — and
here, unlike over SPP, the CH340's baud setting is real, so a mismatch shows up
as garbage rather than being silently absorbed.

Both ends must be visible to the machine running the test, since it is passing
bytes between them.

**4. On the tractor.** Plug into the SM-4P port, power the tractor above ~18 V
so the controller talks, and run the monitor:

```bash
python3 solectrac-kelly-monitor.py --port /dev/cu.<name> --tui
```

Expect the warmup behavior `README.md` describes — the first few polls miss
while the SPP link comes up. Compare a full reading against the official dongle
on the same controller before trusting the clone.

Remember: **one host at a time.** These adapters accept a single connection, so
disconnect the phone before the Mac can use it.

## Open questions

Answering any of these would tighten this document:

- **Module identity** — needs a teardown photo of the official dongle's PCB.
  The unregistered MAC rules out identification by OUI.
- **Connected-state current draw** of the official dongle, which is the honest
  number for regulator sizing — the one remaining number that a bench session
  with the genuine unit would settle.
- **Whether the Kelly app filters on device name**, which decides how closely a
  clone must imitate `26061702`.
- **Whether SM-4P pin 1 is live with the tractor off** — the dongle pairs long
  before the controller talks, but it is not recorded whether pin 1 is always
  hot or comes up with the key.
- **A BLE variant.** An ESP32-S3 running the Nordic UART Service would match the
  transport `../android/` already speaks and would be iOS-reachable, at more
  cost and complexity than a $5 SPP module. Note that this is the *only* way an
  ESP32-S3 can serve this role: the S3 has no Classic Bluetooth radio (BLE
  only), so it cannot impersonate the official SPP dongle and cannot be used
  with the Kelly app. Classic SPP needs an original ESP32, or a dedicated
  module.
