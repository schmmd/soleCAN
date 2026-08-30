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
| Pairing PIN | **`1234`** | CONFIRMED |
| Supply | SM-4P pin 1 (red, ~12 V out of the controller) | CONFIRMED |
| Idle current | **~10 mA** at 12 V, powered but unpaired | CONFIRMED |
| Connected current | not yet measured | UNKNOWN |
| TX idle level (dongle → Kelly Rx, blue) | **~4 V**, unloaded | CONFIRMED |
| Internal UART rate | **19200** — measured, see below | CONFIRMED |
| Frame format | 8N1 | TENTATIVE |
| Byte transparency | no added framing or escaping | CONFIRMED |
| Bridging | bidirectional, both directions verified at 19200 | CONFIRMED |
| Session indicator | LED **steady** = SPP session up, **blinking** = none | CONFIRMED |
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

**The 19200 rate is measured.** With a phone holding the SPP session, `U`
(`0x55`, `0b01010101`) was sent repeatedly from a phone terminal while a CH340
on the dongle's wire side listened at each candidate rate in turn. Only 19200
decoded cleanly. The failures confirm the result rather than merely failing:
`0x55` undersampled at half rate collapses to a constant `0xCC`, and at quarter
rate to `0xFC`, which is exactly what 9600 and 4800 returned. A module that
auto-bauds would not have produced a single clean rate.

Bidirectionality was checked the other way in the same rig — text sent from the
CH340 into the dongle's Rx appeared in the phone terminal — so the bridge
carries both directions at 19200 and tolerates the CH340's 5 V drive on its
input.

This supersedes the earlier deduction, which reasoned from the monitor pulling
valid checksummed frames through the dongle: the Kelly's rate is fixed, so the
dongle's UART had to match, and the bridge had to be byte-transparent or the
19-byte checksum would break. That argument was sound and its conclusion held,
but it could not rule out auto-bauding, and it could not distinguish 8N1 from
other framings — which is why **frame format remains TENTATIVE**.

**Byte transparency has since been swept — CONFIRMED.** Over a direct RFCOMM
channel with a CH340 on the dongle's wire side, `00 01 02 03 11 13 1a 7f 80 81
fe ff` was sent Bluetooth→wire and arrived byte-for-byte and in order, and
`a1 a2 a3 a4 a5 a6` sent wire→Bluetooth likewise. That covers null, XON/XOFF,
EOF, `0x7F`, and the high-bit range — the bytes that break a naive bridge. The
dongle adds no framing, escaping, or flow-control interception in either
direction.

Note that the *host* side tells you nothing about the wire. Over SPP the
`/dev/cu.*` node is a virtual port on an RFCOMM channel, and the rate set on it
never reaches the wire; only the module's own UART setting does. This is why the
clone's `AT+UART` value is the one that has to be right.

**But the host-side rate is not inert, at least on macOS.** Measured on this
dongle, write throughput to the RFCOMM port tracks the tty setting at exactly
`baud/12` — 800 B/s at 9600, 1600 at 19200, 3197 at 38400, 9560 at 115200. The
setting does not reach the wire, but it does throttle the host→dongle direction
locally. At the 19200 this document specifies that ceiling is 1600 B/s, far
above anything the Kelly protocol needs, so it has never been visible in
practice — but setting the port to a low rate would throttle the link with no
obvious cause. Do not treat it as a formality that can be set to anything.

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

**On the name:** any name works. `solectrac-kelly-monitor.py` just takes
whatever `/dev/cu.*` you point `--port` at, and the **Kelly Android app**
presents a device picker rather than binding to a particular name, so a clone
does not need to imitate `26061702`. Prefer a distinct name: setting it to
exactly `26061702` while the real dongle is also powered makes the two
indistinguishable during pairing.

The official dongle's own PIN is `1234`, so that is a reasonable default to
match, but nothing requires it.

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

### "Connected" does not mean the link carries data

Budget for this before blaming your wiring. On a bench session with the genuine
dongle, a Mac associated at the Bluetooth level and passed **zero bytes** across
15 acquisition attempts. Everything the host offered as a status signal was
wrong:

- `blueutil --is-connected` returned `1`
- System Settings → Bluetooth showed **Connected**
- opening `/dev/cu.<name>` succeeded, and writes were accepted

Writes are accepted because macOS buffers and paces them locally at `baud/12`
(above), so even write timing looks plausible on a dead channel. The symptom is
indistinguishable from a broken harness, and it cost most of a session chasing
reseated wires, a swapped pair, and grounding — none of which were ever at
fault. Unpairing and re-pairing did not fix it; the same dongle worked
immediately with a phone.

**The LED is the only honest indicator.** Steady = SPP session established.
Blinking = no session, whatever the host claims. Check it before debugging
anything else.

The general rule: **gate on a data probe, not on connection status.** Send a
byte and require it to arrive.

#### Cause: the `/dev/cu.*` node, not the dongle — CONFIRMED

Reproduced on a second Mac (Apple Silicon, macOS 15, BCM_4388C2) and traced.
The fault is entirely in the host's tty-to-RFCOMM bridge:

- Opening `/dev/cu.26061702` returns in **0.00 s**, emits **no Bluetooth log
  activity whatsoever** (`log stream` on the Bluetooth subsystem is silent),
  and passes zero bytes. The `/dev/tty.*` node, which should block for carrier,
  behaves identically.
- The pairing record is correct. `defaults read /Library/Preferences/com.apple.Bluetooth`
  shows `PersistentPorts -> "20:24:06:19:06:77" = { BSDName = 26061702;
  RFCOMMChannel = 1; }`.
- The dongle is fine: a Classic inquiry answers, and an SDP query returns one
  service record on **RFCOMM channel 1, UUID 1101** (Serial Port).
- Toggling the Bluetooth radio recreates the `/dev` node and changes nothing.
- `blueutil --connect` brings up the *baseband* link (`--is-connected` → 1, and
  it stays 1 while the port is held open — this is not the Settings flicker),
  but the LED keeps blinking, so **no RFCOMM session ever opens**. This is why
  `--is-connected` is worthless here: it reports ACL, not SPP.

**Opening the RFCOMM channel directly through IOBluetooth works on the same
machine**, with the tty layer bypassed entirely. Verified against a CH340 wired
to the dongle's SM-4P data pins (the step-3 rig below): bytes written to the
channel appeared on the wire byte-for-byte and in order.

This is what `kelly/kelly_rfcomm.py` does, so `solectrac-kelly-monitor.py` and
`solectrac-kelly-dump-config.py` now take the RFCOMM path automatically when
`--port` names a paired dongle. See `README.md` §"Bluetooth (SPP)".

Two things this does **not** establish: *why* macOS declines to bridge the node
(the daemon logs nothing, so there is no failure to read), and whether the
Intel Mac's earlier failure has the same root cause — its reported symptoms
differed slightly, in that Settings showed **Connected** without being forced.

### Phone-in-the-loop, when the host's SPP stack won't cooperate

A host that cannot open an SPP session can still characterize the dongle
completely, because the two ends are independently reachable. Let a **phone**
own the Bluetooth side with any SPP terminal app, and let the computer watch the
**wire** side through a CH340. Nothing needs RFCOMM on the computer.

- **Phone → wire.** Send `U` (`0x55`) repeatedly from the phone while the CH340
  listens at each candidate rate. The rate that decodes cleanly is the module's
  UART rate. This is how the 19200 above was measured.
- **Wire → phone.** Send text from the CH340 and watch it appear in the phone
  terminal. Confirms the opposite direction and that the module's Rx tolerates
  the CH340's 5 V drive.
- **Transparency.** Use the terminal's hex-send mode for the bytes that break
  naive bridges: `00 01 02 03 11 13 1A 7F 80 81 FE FF` — null, XON/XOFF, EOF,
  and the high-bit range.

Two gotchas. Terminal apps append CR/LF by default, so expect `0d`/`0a`
alongside your payload or set the line ending to None. And `0x55` is the right
probe byte precisely because it is `0b01010101`: a wrong rate decodes it to a
visibly different value instead of aliasing into something plausible.

## Open questions

Answering any of these would tighten this document:

- **Module identity** — needs a teardown photo of the official dongle's PCB.
  The unregistered MAC rules out identification by OUI.
- **Connected-state current draw** of the official dongle, which is the honest
  number for regulator sizing — the one remaining number that a bench session
  with the genuine unit would settle.
- ~~**Full byte transparency.**~~ **Answered** — the sweep was run over a direct
  RFCOMM channel and passes in both directions. See "The official adapter, as
  measured" above.
- **The frame format.** 19200 is measured; 8N1 is assumed from the Kelly side
  and has never been distinguished from 8N2 or a parity setting.
- **Why macOS associates but passes no data.** *Partly answered:* the fault is
  the host's `/dev/cu.*` tty-to-RFCOMM bridge, and opening the RFCOMM channel
  through IOBluetooth directly works on the same Mac — see "Cause: the
  `/dev/cu.*` node, not the dongle" above. Still open: *why* the bridge declines
  (the Bluetooth daemon logs nothing at all), and whether the Intel Mac fails
  the same way. Latency and throughput are now measurable but still unmeasured.
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
