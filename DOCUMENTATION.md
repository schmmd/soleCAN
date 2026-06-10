# CAN bus documentation for Solectrac tractors

CAN protocol and hardware documentation for a Solectrac electric tractor. All
decode information is derived from captured CAN traffic, vendor manual tables,
the COBO cluster datasheet, the "BMS Update" document, the Solectrac Parts
Catalog (e25), and live injection tests on the tractor.

Confidence markers used throughout:

- **CONFIRMED** — verified by injection, cross-validation, or operator
  ground truth.
- **TENTATIVE** — single-source or partial evidence; encoding plausible
  but not nailed.
- **UNKNOWN** — observed but not decoded.


## Contents

- [Vehicle and pack](#vehicle-and-pack)
- [CAN bus topology](#can-bus-topology)
- [J1939 Decodings](#j1939-decodings)
  - [Source-address map](#source-address-map)
  - [BMS (SA 0xF3)](#bms-sa-0xf3)
  - [Motor controller (SA 0xCA)](#motor-controller-sa-0xca)
  - [Charger (SA 0xE5)](#charger-sa-0xe5)
  - [Vehicle controller (SA 0xD0)](#vehicle-controller-sa-0xd0)
- [Instrument cluster hardware](#instrument-cluster-hardware)
- [Vendor error code tables](#vendor-error-code-tables)
- [Open questions](#open-questions)


## Vehicle and pack

The tractor is a **Solectrac 25G** (non-HST variant).

"Pack" refers to the tractor's traction battery — the high-voltage lithium-ion
battery that powers the motor, as distinct from the 12 V accessory battery that
runs the cluster and lights.

| Property                  | Brochure                              | Operator manual (CET)                              | Service manual              | BMS GUI                 | Observed |
|---------------------------|---------------------------------------|----------------------------------------------------|-----------------------------|-------------------------|----------|
| Bus baud                  | —                                     | —                                                  | 250 kbaud (J1939 default)   | —                       | —        |
| Cell P/N                  | —                                     | —                                                  | `SEPNI-8688190P-17.5AH-5P`  | —                       | `SEPNI8688190P-15Ah` (battery faceplate) |
| Cells in parallel         | —                                     | —                                                  | 4 modules × 5P1S = 20P      | "~20 cells in parallel" | —        |
| Cells in series           | —                                     | —                                                  | 20 (one per module)         | 20                      | 20 (CAN captures) |
| Charge rate (L1, 110 V)   | ~1.2 kW AC (implied by 11 hr 20→80 %) | —                                                  | —                           | —                       | 1475 W ± 3 W DC, constant 10–95 % SOC |
| Charging temp range       | 0–40 °C                               | —                                                  | —                           | —                       | —        |
| Charging time             | 5.5 hr (Lvl 2, 220 VAC, 20→80%); 11 hr (Lvl 1, 110 VAC) | 8 hr (0→100%, on-board charger)  | —                           | —                       | ~14 hr (L1 120 VAC, 10→100 % SOC) |
| Charging-target voltage   | 83 V                                  | 82 VDC (§9.1)                                      | —                           | —                       | —        |
| Cluster supply            | —                                     | 12 V / 20 Ah aux battery                           | 12 V (accessory, not pack)  | —                       | —        |
| Cycle life                | 2500 cycles @ 25 °C                   | 2500 cycles @ 25 °C                                | —                           | —                       | —        |
| Main HV fuse              | —                                     | —                                                  | 350 A                       | —                       | —        |
| Manufacture date          | —                                     | —                                                  | —                           | —                       | 2021-12-02 (battery faceplate) |
| Nominal pack voltage      | 72 V                                  | 72 V (§1.2 plate, §9.1)                            | **73.0 V**                  | 72.0 V                  | 72 V (battery faceplate) |
| Operating temp range      | −20 to 55 °C                          | —                                                  | —                           | —                       | —        |
| Pack chemistry            | Li NMC                                | NMC (Li-ion)                                       | NMC                         | NiCoMn                  | —        |
| Pack model number         | —                                     | `EV-008-72V300Ah-01` (§1.2 plate)                  | —                           | —                       | `EV-008-72V300Ah-02` (battery faceplate) |
| Pack serial number        | —                                     | —                                                  | —                           | —                       | NO.079 / QR `031PE0021Y020ABC20100079` (sticker) (battery faceplate) |
| Pack vendor               | —                                     | Soundon New Energy Technology Co., Ltd. (§1.2 plate) | "Escorts Solution"        | "ESCORTS-INTERNAL"      | Soundon New Energy Technology Co., Ltd. (engraved) + Escorts (sticker) (battery faceplate) |
| Pack weight               | —                                     | 175 ± 15 kg (§1.2 plate)                           | —                           | —                       | 175 ± 15 kg (battery faceplate) |
| Rated capacity            | 350 Ah                                | 300 Ah (270 Ah opt., §9.1; 300 Ah on §1.2 plate)   | **350 Ah**                  | 300 Ah                  | 300 Ah (battery faceplate) |
| Rated charge (DC / AC)    | —                                     | charger out 3.3 kW @ 220 V; in AC 85–265 V, 50/60 Hz, IP67 | —                   | 78 A / 39 A             | —        |
| Rated energy              | —                                     | 21.6 kWh (§1.2 plate)                              | 25.5 kWh @ 23 ± 2 °C        | —                       | 21.6 kWh (battery faceplate) |
| Temperature probes        | —                                     | —                                                  | -                           | 7 active                | 7 active (CAN captures) |
| Voltage operating range   | —                                     | 60–84 V (§1.2 plate)                               | 60–84 V                     | —                       | 60–84 V (battery faceplate) |

**The 300 Ah vs 350 Ah split is a two-SKU situation.** The service manual cell
P/N is `SEPNI-8688190P-17.5AH-5P` (17.5 Ah cells); the as-installed pack's
nameplate sticker is `SEPNI8688190P-15Ah` (15 Ah cells). Same cell family
(SEPNI 86 × 88 × 190 mm prismatic NMC), different capacity grade. Plugged into
the 20-series × 4-module × 5-parallel topology:

- 17.5 Ah cells → 4 × 5 × 17.5 = **350 Ah pack, 25.5 kWh @ 73 V** —
  service-manual and brochure-quoted SKU.
- 15 Ah cells → 4 × 5 × 15 = **300 Ah pack, 21.6 kWh @ 72 V** —
  this tractor's installed SKU. The operator manual's example
  faceplate (§1.2) and the BMS GUI both describe the same SKU.

**Pack vendor is Soundon; UDAN is the BMS firmware/tool vendor.** The battery
faceplate is laser-etched **Soundon New Energy Technology Co., Ltd.** (Chinese
NMC pack manufacturer). An Escorts-branded white sticker rides on top: Escorts
Kubota Limited (Farmtrac's parent in India, Solectrac's US distribution brand)
buys the pack from Soundon, applies its own QR-coded serial ("Escorts 72V300Ah
NO.079"), and ships it into Farmtrac/Solectrac tractors.

The service manual's BMS troubleshooting section delegates all live-data
inspection to a host-side application called **UDAN** (referenced repeatedly:
"Connect UDAN and check the minimum cell voltage", etc.). UDAN has been
identified as the **UDAN iBMS Upper Utility** from **Anhui UDAN Technology Co.,
Ltd.** — a Chinese BMS firmware/diagnostic-tool vendor. The tool is publicly
downloadable Windows software and can be connected via CAN @ 250 kbit/s with
hardware such as the CANalyst-II.

**BMS firmware project identification.** When connected via UDAN, the BMS
reports its loaded project file as **`C121.082.001.01`** with the display name
**`印度索伦72V300Ah原版`** ("India Solectrac 72V 300Ah Original Version"). This
is the UDAN-side project key for the firmware/parameter set on this tractor.

**BMS field connector** is part number **`RT061412SNHEC03`** (12-pin circular).
Per the manual's DTC 125 troubleshooting (page 30 of the battery section), main
vehicle CAN exits on **pins D and E** — a 60 Ω resistance test across D↔E (two
120 Ω terminators in parallel) confirms a healthy bus. Pins A/B/C are 12 V
power rails: **B is GND**, A and C are switched/unswitched +12 V (DTCs
140/142/143/144 all prescribe "12 V between A↔B and C↔B" as the integrity
check). The remaining field-connector pins F/G/H/J/K/L are unassigned in
troubleshooting steps and are the most likely physical home of the **second
(diagnostics) CAN pair** — see "Second 2-pin CAN port" under the CAN topology
section.

Schematic 5.7 in the FT 25G service manual uses BMS-internal terminal letters
that do **not** map 1:1 to the field connector — it shows main CAN on pins H/J
(`CAN_H3`/`CAN_L3`) and a second pair on F/G (`CANDE-H`/`CANDE-L`) labelled "TO
BMS DEBUG CONNECTOR PIN-1/PIN-2". The schematic's H/J and the field connector's
D/E refer to the same physical bus; the two pin-naming conventions are
independent.

**HV power path.** The pack feeds the traction inverter through the **Albright
SW200** main contactor (service manual §4.2.3) protected by a **350 A battery
cut-off fuse** (§4.3.5; parts catalog Table 60 lists 355 A — same component,
rounding). A separate **discrete hydraulic contactor** (Table 60) gates HV
between the pack and the BLDC hydraulic pump motor; its coil is energized from
the main E-Controller's key-switch wire. Neither contactor is on the CAN bus,
which is part of why hydraulic activity produces no CAN signature (the other
part being that the e-hydraulic controller has no CAN pins at all — see "What
is NOT on this bus").


## CAN bus topology

Single shared CAN bus at 250 kbaud. The ODB-2 diagnostic port we capture from
is on the same bus as the ECUs that run the tractor — it is not a separate
diagnostic segment.

Per **schematic 5.10** in the FT 25G service manual, the bus has **exactly
four** CAN nodes, with terminators at the two physical ends of the linear bus:

    1. MOTOR CONTROLLER (SA 0xCA) — Curtis controller, pins 23/35;
                                    120 Ω terminator at this end
                                    (per parts catalog, model is Curtis
                                    1238E; nameplate not verified in
                                    this corpus)
    2. BMS (SA 0xF3)              — pins H/J of the BMS internal terminals,
                                    field-connector pins D/E
    3. CHARGER (SA 0xE5)          — on-board AC charger; pins 1/2.
                                    Also speaks CAN to the BMS — DTC 124
                                    is "Fast Charger CAN connection fault"
    4. CLUSTER                    — COBO ECO MATRIX VT3 instrument
                                    cluster, pins 35/36; 120 Ω terminator
                                    at this end

The **OBD-II diagnostic connector** is a passive tap on the same bus — not an
extra node. It follows standard OBD-II HS-CAN pinout with four cavities
populated (verified by physical inspection):

- **Pin 4** — chassis ground
- **Pin 6** — CAN_H (yellow 0.75 mm²)
- **Pin 14** — CAN_L (green 0.75 mm²)
- **Pin 16** — +12 V battery

The older Solectrac topology diagram's "DB9" connector label is a mislabel —
it's the OBD-II port.

```
   [120 Ω]─┬────────┬────────┬────────┬────────┬─[120 Ω]
           │        │        │        │        │
        ┌──┴──┐  ┌──┴──┐  ┌──┴──┐  ┌──┴──┐  ┌──┴──┐
        │ MC  │  │ BMS │  │ CHG │  │ OBD │  │ CLU │
        │0xCA │  │0xF3 │  │0xE5 │  │ tap │  │     │
        └─────┘  └─────┘  └─────┘  └─────┘  └─────┘
```

- `[120 Ω]` = terminator drawn on schematic 5.10 (MC and Cluster).
- OBD = OBD-II capture port (passive tap, no SA).
- Node order on the schematic is as drawn; physical electrical order
  on the wire is not verified.

### What is NOT on this bus

**The E-Hydraulic Controller does not broadcast on the CAN bus.** Schematic
5.11 in the service manual shows it driven by a discrete control interface:
LOW/HIGH speed-selection switch, hydraulic-motor on/off switch, throttle wiper
potentiometer (10 kΩ), Hall/encoder, key-switch wire from the main E-Controller
(`KS01A`), and three-phase U/V/W out to a BLDC pump motor. It is not a
CAN-speaking ECU on this vehicle.

The parts catalog identifies the e-hydraulic as a **Kelly KLS7212M / KLS7218**
controller, which is a CAN-capable family. However, no CAN data has been found
that is related to the hydraulic system.

The rear 3-point hitch, lift, power steering, and remote hydraulics are fully
mechanical-hydraulic with no electrical interface (per the manual's Hydraulic
System chapter, pp 295-319 — a fully mechanical Escorts design with draft +
position levers, rocker top-link spring, mechanical position-feedback cam,
manual auxiliary spool, and no solenoids/sensors/transducers anywhere). The PTO
shaft speed is purely mechanical, but PTO **engagement** uses a **wet clutch**
controlled by a dashboard switch (service manual §1.4, switch C: "PTO Wet
Clutch"); the wet clutch requires hydraulic pressure from the e-hydraulic pump
to engage and oil flow for cooling, which is why the pump must be on for PTO
operation.

### Bus termination

Bus measures **40 Ω** across CAN_H/CAN_L at the OBD-II port (key off, all nodes
connected) — three 120 Ω resistors in parallel, one beyond what the schematic
draws. Unplugging the BMS field connector raises the reading to the textbook
**60 Ω**, confirming the extra terminator is **internal to the BMS**. Drivers
tolerate the 3-terminator config and captures are clean.

### Second 2-pin CAN port — BMS diagnostics bus — CONFIRMED

A separate 2-pin connector on the tractor carries the **BMS diagnostics bus** —
shown on schematic 5.7 as `CANDE-H` / `CANDE-L`, labelled "TO BMS DEBUG
CONNECTOR PIN-1 / PIN-2". The BMS exposes two CAN pairs: the main vehicle bus
(above) and this diagnostics pair. It is the channel the host-side **UDAN**
tool consumes.

**Resistance confirms it is a separate, BMS-only bus.** Measured key-off with
nothing plugged in, the 2-pin connector reads 120 Ω across the pair. If the
2-pin were a tap onto the main bus, it would read the same as any other tap on
the main bus (40 Ω at OBD-II in the same conditions, §"Bus termination" above).
Unplugging the BMS field connector causes the 2-pin reading to go **open**
(overload), which proves the only node electrically present on that pair is the
BMS — no other module in the harness taps it. The single 120 Ω is therefore the
BMS's internal terminator on its diagnostics pair, and the 2-pin connector is
its physical termination at the harness end (no second terminator until a tool
is plugged in). All consistent with the schematic 5.7 `CANDE-H`/`CANDE-L`
diagnostics-pair interpretation.

**Traffic content matches the same picture.** Time-synced dual-bus captures
(diagnostics + main) show the diagnostics pair carries only UDS traffic to the
BMS (request `0x740`, response `0x748`) plus a single all-zero `0x7FD` frame at
each key-on (origin UNKNOWN); zero J1939 broadcasts appear on it. The main
vehicle bus carries zero UDS frames — `0x740`/`0x748` are exclusive to the
diagnostics pair. UDS responses only occur with the key on, and stop a few
seconds after key-off. Full UDS protocol, DID map, and bootstrap details are in
[`bms/README.md`](bms/README.md).

## J1939 decodings

Almost all of the traffic monitored on the Solectrac is
[J1939](https://www.csselectronics.com/pages/j1939-explained-simple-intro-tutorial),
a standardized language for heavy duty vehicles on top of the CAN protocol.
Each 29-bit J1939 identifier breaks down as:

| Bits   | Field               | Notes                                                                 |
|--------|---------------------|-----------------------------------------------------------------------|
| 28..26 | Priority (P)        | 0 = highest, 7 = lowest. Priority 6 is typical for periodic broadcasts.|
| 25     | Reserved (R) / EDP  | Always 0 in classic J1939.                                            |
| 24     | Data Page (DP)      | Selects between page 0 (default) and page 1.                          |
| 23..16 | PDU Format (PF)     | PF < 0xF0 → PDU1 (destination-specific). PF ≥ 0xF0 → PDU2 (broadcast).|
| 15..8  | PDU Specific (PS)   | Destination Address (DA) for PDU1, or Group Extension (GE) for PDU2.  |
| 7..0   | Source Address (SA) | The transmitter's J1939 address.                                      |

From this identifier, the Parameter Group Number (PGN) is reconstructed
according to the following logic so that broadcasts can use the address space
as additional data storage:

```
  if (PF < 0xF0) {
      // PDU1: PS is DA, not in PGN
      PGN = (DP << 16) | (PF << 8);
      DA  = PS;
  } else {
      // PDU2: PS is GE, part of PGN
      PGN = (DP << 16) | (PF << 8) | PS;
  }
```

In J1939 data collected for the Solectrac, > 99% of frames are PDU2
broadcasts. The only recurring PDU1 exception is `1806E5F4`: the standard
Elcon/TC-protocol charger command from SA 0xF4 to the on-board charger at
0xE5 (see the 1806E5F4 section).

### Source-address map

Every J1939 frame's 29-bit CAN ID ends in an 8-bit source address (SA)
identifying which node on the bus sent it. The table below pairs each SA seen
in our captures with the ECU we believe is behind it and the frames it emits,
and is the basis for the per-source decoder dispatch elsewhere in this
document.

| SA   | Role                                  | Frames observed                                             |
|------|---------------------------------------|--------------------------------------------------------------|
| 0xF3 | BMS                                   | F100/F102/F104/F106/F107/F108/F113../F155..                  |
| 0xE5 | On-board charger                      | FF50 telemetry                                               |
| 0xF4 | BMS charger-interface logical SA      | Sends 1806E5F4 (BMS-to-charger voltage/current request)       |
| 0xD0 | Vehicle / accessory controller (physical home unresolved) | Periodic F100D0 heartbeat; byte-0 0x00 → 0x0C at wake-up     |
| 0xCA | Motor controller / drive ECU          | DM1 (FECA) + FF21 motor telemetry (~85 Hz); silent while charging |
| 0x12 | Dashboard / instrument cluster        | FF21 heartbeat at ~10 Hz; byte 0 = 0x00 during boot, 0x01 once alive |
| 0x041 (11-bit) | Instrument-cluster power-transition marker (non-J1939) | Standard CAN 2.0A, not J1939. Constant payload `20 12 01 00 00 00 01 11`. One frame at cluster power-up and one at power-down: key-on/key-off in drive sessions, and equally the operator-confirmed mid-charge key cycles used to check SOC on the dash, where the markers bracket windows with the dash heartbeat running while VC and MC stay silent. The cluster is the only node transitioning at every observed firing (BMS and charger broadcast straight through them), making it the near-certain sender. |

#### FF2112 — Dashboard heartbeat — CONFIRMED

SA 0x12 broadcasts PGN FF21 at ~10 Hz with payload
`01 00 00 00 00 00 00 00` in steady state. The same PGN is used by the
motor controller at SA 0xCA, but the on-wire IDs are distinct:
`0x18FF2112` vs. `0x0CFF21CA`.

Byte 0 is an alive flag: `0x00` during the first ~700 ms after key-on
(cluster boot), then `0x01` thereafter. Bytes 1..7 are always `0x00`
padding. The dashboard/instrument-cluster attribution is by elimination:
SA 0x12 is not a standard J1939 source address, and the boot-then-alive
pattern lines up with the key-on transitions in the ignition captures.

### BMS (SA 0xF3)

All scalings derived empirically. Byte numbering is 1-based with explicit
`data[N]` (0-based) annotations where helpful.

#### F113..F13C — Per-cell voltages — CONFIRMED

8 bytes = 4 × big-endian uint16, millivolts.

    F113 = cells  0.. 3
    F114 = cells  4.. 7
    ...
    F117 = cells 16..19
    F118..F13C reserved (cells 20..167); 0xFFFF / 0 sentinel on this pack.

Per-PGN cell index mapping cross-validated by Pearson correlation of each
aligned u16 BE slot against the 20 cell mV values returned by DID `0x0101` over
a drive cycle (n=794 paired samples, pack delta ≥5 mV throughout, peak 11 mV):
all 20 slots picked their natural-order PGN slot as the top match.

Cells read ~3.6–3.7 V at ~40 % SOC, ~4.16 V/cell at 100 %.

#### F155..F15E — Module temperatures — CONFIRMED

8 bytes = 8 × uint8 with J1939 +40 °C offset (raw 53 = 13 °C).

    F155 = channels 0..7
    F156 = channels 8..15
    ...
    F15E = channels 72..79

Only the first 7 channels are populated on this pack; the rest are 0xFF (not
present).

#### F102F3 — Cell min/max summary — CONFIRMED (max/min/spread)

| Byte    | Meaning                                            |
|---------|----------------------------------------------------|
| 1..2 BE | max cell mV                                        |
| 3..4 BE | min cell mV                                        |
| 5       | max-cell **number, 1-based**                       |
| 6       | min-cell **number, 1-based**                       |
| 8       | **cell-voltage spread, mV** (= bytes 1..2 − bytes 3..4) |

**Indexing convention:** byte 5/6 use 1-based cell numbers as the BMS GUI
displays them ("Max cell #19"). The parser's `cell_index` in `cells.csv` is
0-based — subtract 1 to map. Cross-validated against contemporaneous per-cell
PGN snapshots in `recorded-data/charging.csv`.

When several cells tie at the max, the BMS reports the lowest-index winner.

The reported `min_mv` is occasionally 1 mV higher than the actual lowest
voltage in the per-cell snapshot taken alongside it — likely a timing-skew or
filtering artifact in the BMS. The *index* still correctly identifies the right
cell.

**Byte 8 is the cell-voltage spread** — `max − min` of this frame's own
bytes 1..2 and 3..4, in mV. Confirmed by exhaustive identity check:
**522,027 / 522,027 frames match exactly** (zero residuals) across the
capture corpus, spanning drive, idle, and a full 14-hour 10→100 %
charge. Across the charge it behaves like a real physical quantity:
15,138 value changes, every one a ±1 mV step, ranging 2–11 mV. This
makes F102 fully symmetric with F104, whose byte 5 is the analogous
temperature spread.

Smallest spread observed across all captures: 2 mV.

#### F100F3 — Pack status — CONFIRMED (voltage, current, SOC)

| Byte | data[]  | Meaning                                                     |
|------|---------|-------------------------------------------------------------|
| 1..2 | data[0..1] BE | **Pack terminal voltage**: V = raw × 0.1              |
| 3..4 | data[2..3] BE | **Signed pack current**: A = (be16 − 0x7D00) × 0.1    |
| 5    | data[4] | **BMS-published SOC**: % = raw × 0.4 − 0.8                  |
| 6    | data[5] | 0xFA constant — **leading SOH candidate** (250 raw × 0.4 %/bit = 100 %) |
| 7    | data[6] | 0x14 (= 20) — series cell count                             |
| 8    | data[7] | 0x00 constant                                               |

**Pack terminal voltage (data[0..1])** is a single big-endian 16-bit
field at 0.1 V/bit — the same encoding as the charger's FF50E5 output
voltage. The pack's 60–84 V operating window keeps the high byte at
0x02 or 0x03 (51.2–102.3 V), which can make the field masquerade as a
"range-selector byte plus 8-bit voltage"; the high byte carries no
information beyond being the top of the voltage value, and ticks
between 0x02 and 0x03 simply as pack V crosses 76.8 V (at lower SOC,
under heavy-load sag, and during active charging).

Anchored by linear regression of the field against 20 × mean(cell mV)
across all 70 captures, fitted separately on each side of the 76.8 V
(raw 0x0300) boundary:

| High byte | Fit (raw8 = low byte) | n | RMSE |
|------|-----|---|------|
| 0x03 | V = 0.0998 × raw8 + 76.82 | 13,370 | 0.028 V |
| 0x02 | V = 0.1003 × raw8 + 51.13 | 14,127 | 0.033 V |

Both fits are the BE-16 × 0.1 decode restated per high-byte band
(0x0300 × 0.1 = 76.8, 0x0200 × 0.1 = 51.2). Max residual under 1 V in
both, attributable to high-current transients where pack V swings
faster than the BMS broadcasts. Cross-checked against the FF50 charger
frame, which carries the same BE-16 encoding (R² = 0.986 across 2863
active-charging frames).

**Pack current** is signed BE-16 with a fixed bias of 0x7D00 (raw 32000 = 0 A)
at 0.1 A/bit. Convention: positive = drawing from pack, negative = charging
into pack.

Cross-validation against operator-confirmed dashboard amperage (amp-*.asc
steady-state captures, 2026-05-09):

| File          | data[2..3] range  | mean decoded A | dash A |
|---------------|-------------------|----------------|--------|
| amp-1.asc     | 0x7D12 (constant) |   1.8          |   1    |
| amp-18.asc    | 0x7D9D – 0x7DC5   |  17.6          |  18    |
| amp-35.asc    | 0x7E53 – 0x7E94   |  37.0          |  35    |
| amp-42.asc    | 0x7E8E – 0x7EC5   |  41.7          |  42    |
| amp-58.asc    | 0x7F32 – 0x8061   |  62.1          |  58    |

Mean decoded current matches dashboard to within ~1 A across the full 0–60 A
range, exercising the 0x7D→0x7E and 0x7F→0x80 high-byte boundaries. amp-1.asc
is the only true-idle capture: data[2..3] is constant at 0x7D12 = 1.8 A standby
draw (BMS + dashboard + DC-DC). Putting the tractor in DRIVE energizes
inverter/contactor circuitry that adds ~16 A above standby — earlier captures
sat at ~17 A "idle" for this reason.

**Pitfall warning.** A naive "data[3] alone, 1 A/bit" decode matches the
dashboard at idle by coincidence: data[2] sits at 0x7D, the bias cancels, and
data[3] reads as 0..25.5 A. The moment real current crosses ~25.6 A, data[2]
ticks to 0x7E and data[3] rolls back near zero, making the byte-only decode
appear to "saturate" under load. Always read both bytes BE with the bias.

**SOC (data[4])** — CONFIRMED. Fit:

    SOC % = data[4] × 0.4 − 0.8

Slope 10/25 = 0.4, intercept −0.8; nine dashboard-screen anchors at every
10 % step from 10 % to 90 % all decode within 0.4 % of the displayed integer:

| Dashboard | raw     | Fit predicts |
|-----------|---------|--------------|
| 10 %      | 27 (0x1B)  | 10.0 %    |
| 20 %      | 52 (0x34)  | 20.0 %    |
| 30 %      | 77 (0x4D)  | 30.0 %    |
| 40 %      | 102 (0x66) | 40.0 %    |
| 50 %      | 126 (0x7E) | 49.6 %    |
| 60 %      | 152 (0x98) | 60.0 %    |
| 70 %      | 177 (0xB1) | 70.0 %    |
| 80 %      | 202 (0xCA) | 80.0 %    |
| 90 %      | 227 (0xE3) | 90.0 %    |

The 50 % anchor is the only one whose raw value (126) doesn't land exactly on
the fit — it decodes to 49.6 %, which the dashboard rounds up to "50 %". The
adjacent raw 127 would decode to exactly 50.0 %; the BMS happened to sit one
LSB below it during the captures.

Raw saturates at 250 (= 99.2 %) at full charge. Linearity holds through the
full 10–90 % range — no curvature or breakpoint at the low end.

**SOH candidate (data[5])** TENTATIVE. data[5] is 0xFA = 250 across every
capture (73 captures, all BMS frames swept by `util/soh_byte_sweep.py`). 250
raw × 0.4 %/bit decodes to 100 %. The byte-constancy sweep eliminates every
other plausible SOH location in the visible BMS frames — every other constant
byte is either already attributed (cell count, voltage, current, SOC) or is a
J1939 sentinel (0x00 / 0xFF).

Lending evidence to the above, UDAN exposes a separately labeled **`SOH(%)`**
field that reads exactly **`100.0`** on this pack, sitting alongside its own
`Shown SOC` and `Real SOC` fields.

#### F104F3 — Pack temperature min/max summary — CONFIRMED

Pack-wide hottest/coldest module-temperature summary, analogous to F102.

| Byte | data[]  | Meaning                                          |
|------|---------|--------------------------------------------------|
| 1    | data[0] | **Max module temperature**: °C = raw − 40         |
| 2    | data[1] | **Min module temperature**: °C = raw − 40         |
| 3    | data[2] | Max-temp **probe number, 1-based**                |
| 4    | data[3] | Min-temp **probe number, 1-based**                |
| 5    | data[4] | **Temperature spread, °C** (max − min, no offset) |

Cross-validated two ways:

- Per-probe temperatures from F155..F15E reduce to the same max/min/spread
  on every capture.
- The BMS-internal peak DIDs 0x2830 (top-4 max temps, value byte at the
  start of each 3-byte tuple) and 0x2838 (top-4 min temps) carry the same
  values: F104F3 data[0] tracks 0x2830 top-1 raw value at r ≈ 1.0, and
  F104F3 data[1] tracks 0x2838 top-1 raw value the same way.

#### F106F3 — BMS state — PARTIALLY CONFIRMED

Periodic frame. Byte 0 is **two bitfields, not a flat enum** — a context
bit plus a three-step activity ladder:

| Bits | Meaning                                                       |
|------|---------------------------------------------------------------|
| bit 6 (0x40) | **Run context** — normal key-on operation             |
| bit 7 (0x80) | **Plug context** — J1772 present, charge not active   |
| low bits     | **Activity ladder**: 0x00 init → 0x04 ready → 0x05 active |

Every observed value is a product of the two parts: 0x00 (boot),
0x40 / 0x44 / 0x45 (run context climbing the ladder), and
0x80 / 0x84 / 0x85 (plug context, same ladder). The ladder moves one
step at a time: key-on boot runs 0x00 → 0x44 → 0x45; end of charge
steps down 0x45 → 0x85 → 0x84 → 0x80 over ~3 s as delivery stops.
0x84/0x85 also appear transiently around OPC shutdown, so they are
ordinary ladder steps, not charge-specific states.

**0x45 ("run, active") covers driving and actively delivering AC charge
alike** — during a full L1 charge essentially every frame is 0x45, not
0x80.

**0x80 = plug present, charging not active.** A controlled plug-cycle
(key on throughout) showed byte 0 stepping 0x45 → 0x80 at the instant
the J1772 connector was inserted into the tractor, and back to 0x45 the
instant active charging began. It also *returns* to 0x80 after a charge
completes and persists until the BMS sleeps — so it is not limited to
the pre-charge handshake. Nor is it guaranteed during one: a plug-in
with AC ready went from 0x45 straight into active charging (~7 s
handshake) without ever showing 0x80. Three other bytes flip in
lockstep at the plug-in moment: F108F3 byte 5 = 0x01, F108F3 byte 7 =
0xBB (= 140 + 142 + 143 + 144 + 145, the maintenance cluster), and
1806E5F4 switches to the 0 V / 0 A stop command `00 00 00 00 01` (vs.
the steady 84.6 V / 39 A enable command `03 4E 01 86 00` when no plug —
see the 1806E5F4 section).

**End-of-charge shutdown sequence** (observed once, AC removed at
~100 % SOC): byte 0 steps 0x45 → 0x85 → 0x84 → 0x80 while the charger's
output voltage decays toward zero and its flag byte cascades
0x08 → 0x0C → 0x1C (see FF50E5); the BMS keeps broadcasting in the 0x80
state for ~30 s and then sleeps. An unplug that interrupts an active
mid-SOC charge remains unobserved.

**Byte 1 — companion status byte (TENTATIVE).** Five values observed:
0x80 (boot, alongside byte 0 = 0x00), 0x84 (boot transition), 0xE0 (no
plug), 0xC4 (plug present), 0xCC (actively charging). It flips at the
exact plug-in and charge-start instants and is *more* specific than
byte 0 during charging: it distinguishes plug-present (0xC4) from
actively-charging (0xCC) while byte 0 sits at 0x45 for both. Reads
naturally as J1939 2-bit status pairs (bits 3..2: 0 = no plug, 1 = plug
present, 3 = charging; bits 7..6: 3 = running, 2 = init). Bytes 2..7
are constant `FC FF FF FF FF FF` across the entire corpus.

Vendor GUI implies more states exist (Calibrating, Charging,
Discharging, Fault, Sleep); whether they map onto further byte-0 /
byte-1 codes is not observed in captured data.

#### F107F3 — BMS limits — PARTIALLY CONFIRMED

Layout matches the standard J1939 limits-frame template:

| Bytes | Likely meaning                              | Observed                                                  |
|-------|---------------------------------------------|-----------------------------------------------------------|
| 0..1  | Discharge current limit, 0.01 A/bit         | 0x38A4 (145.0 A) dominant (~92 % of frames); 0x2710 (100.0 A) in the default pattern (boot/idle/active charging/low-SOC limp); 0x2EE0 (120.0 A) and 0x36B0 (140.0 A) as brief boot-ramp intermediates |
| 2..3  | Max current-into-pack acceptance (regen) while in drive, 0.01 A/bit — never published during AC charging | SOC-dependent taper: 130 A at 10–14 %, 124 A at 20–75 %, 110.5 A at 80 %, 100 A at ≥90 % (and whenever the frame sits in its default pattern). Boot ramps through 0x27D8…0x2FA8 in 2-A steps (102→122 A) before settling. See SOC-taper table below. |
| 4     | Companion flag to bytes 0..1                | 0x01 when bytes 0..1 ∈ {0x36B0, 0x38A4} (≥140 A discharge limit); 0x00 when bytes 0..1 ∈ {0x2710, 0x2EE0} (≤120 A) — transition is between the 120 A and 140 A rungs of the boot ramp |
| 5     | Pack-voltage echo, **linear** (`V ≈ 0.222 × b5 + 56.9`) | Live value when byte 4 = 0x01; 0x00 when byte 4 = 0x00             |
| 6..7  | Charge-power allowance above 100 A baseline, BE u16 × 10 W | 0x0000 when charge limit is 100 A; otherwise tracks `(charge_limit_a - 100 A) × pack_voltage_v` |

**The frame is mode-gated.** Bytes 0..1 and 2..3 never move
independently: outside the boot slew-ramp, every observed change is a
whole-frame switch between an ACTIVE shape (b0..1 = 145.0 A, b2..3 =
SOC-band value, byte 4 = 0x01, byte 5 = live V echo) and the DEFAULT
shape `27 10 27 10 00 00 00 00`. Boot, key-off, active AC charging, the
sustained sub-10 %-SOC clamp, and the brief mid-drive transients are
all the same default broadcast — one inactive mode, not separately
computed limit reductions.

Bytes 0..1 stay pinned at 145.0 A throughout drive captures even when
instantaneous pack current on F100F3 peaks far above it (246 A
observed). It is therefore not an instantaneous ceiling — but it may
still be an enforced **continuous** limit rather than an advisory: in
the most aggressive long drives the worst-case 30 s and 60 s
rolling-average currents are 143 A and 140 A, just under the published
145 A, with only sub-30-s bursts exceeding it. Separately, the value
itself never responds to load, temperature, or SOC in any capture (the
only dynamics are the boot slew and the mode switch), so a static
configured rating is as consistent with the data as a computed limit.
An inverted reading also fits: 100 A — the default-mode value — as the
continuous rating, with 145 A a short-term boost allowance granted
while the pack is healthy. Real instantaneous protection, if any, lives
on separate voltage-sag and temperature thresholds.

Bytes 6..7 are confirmed as a charge-power allowance above the 100 A baseline.
Across all paired F107/F100 frames the raw value matches `(charge_limit_a - 100
A) × pack_voltage_v / 10` within 1 raw count — so b2..3 and b6..7 are a
redundant A-and-W encoding of the same allowance.

**Bytes 2..3 are the max current the BMS will accept *into* the pack
while in drive** — in practice a regen acceptance. The taper below is
never broadcast during AC charging (a full 10→100 % charge stayed in
the default pattern throughout), so the field does not serve as
guidance to the OBC; the BMS's actual charger command is the separate
1806E5F4 frame. The value follows a clean monotonic taper with SOC:

| SOC band  | b2..3   | Acceptance |
|-----------|---------|------------|
| 10–14 %   | 0x32C8  | 130.0 A    |
| 20–75 %   | 0x3070  | 124.0 A    |
| 80 %      | 0x2B2A  | 110.5 A    |
| ≥ 90 %    | 0x2710  | 100.0 A    |

This is a textbook battery-acceptance curve: the empty pack can sink more
current safely, and the BMS narrows the budget as SOC climbs to avoid
overcharge. Two caveats on interpretation. First, the BMS GUI's rated
charge current is **78 A DC — below every value in the taper** — which
suggests b2..3 is a short-duration (pulse) allowance rather than a
continuous charge rating. Second, every taper rung was observed in a
*different* session: no capture shows a live band-edge step mid-drive,
so a value latched per SOC at frame activation is so far
indistinguishable from a continuously tracked one. (SOC and open-circuit
cell voltage are also nearly collinear in this data, and all drive
captures sit at 15–17 °C, so cell-Vmax or temperature as the real
driver is not yet excluded either.)

Like b0..1, **b2..3 is not an instantaneous ceiling**: regen bursts
reach −185 A against a published 124 A. But the bursts are short —
worst-case 5 s average regen stays near −73 A and 10 s near −52 A — so,
as on the discharge side, a pulse-window or filtered-current
enforcement fits the data as well as "advisory" does. Real
instantaneous protection lives on the same voltage/temperature
thresholds noted for b0..1.

**Common F107F3 patterns.** The frame has four dominant steady-state shapes plus
startup/transition values:

| Pattern                            | Bytes 0..1 | Bytes 2..3 | Byte 4 | Byte 5 | Context                              |
|------------------------------------|------------|------------|--------|--------|--------------------------------------|
| `38 A4 30 70 01 XX 00 YY`          | 145.0 A    | 124.0 A    | 0x01   | V_pack | Sustained drive default              |
| `38 A4 32 C8 01 XX 00 YY`          | 145.0 A    | 130.0 A    | 0x01   | V_pack | Sustained low-SOC drive (10–14 %)    |
| `38 A4 27 10 01 XX 00 YY`          | 145.0 A    | 100.0 A    | 0x01   | V_pack | High-SOC idle / brief drive transients |
| `27 10 27 10 00 00 00 00`          | 100.0 A    | 100.0 A    | 0x00   | 0x00   | Boot / key-off / active charging / low-SOC limp |

Byte 4 and byte 5 are not independent state bits — they move in lockstep with
bytes 0..1. The `27 10` pattern is the BMS's default/zeroed broadcast and shows
up in several distinct contexts:

- **Key-on boot ramp** (~1.5 s): discharge limit (bytes 0..1) climbs
  27 10 → 2E E0 → 36 B0 → 38 A4 (100 → 120 → 140 → 145 A) while charge limit
  (bytes 2..3) sweeps 27 D8 → 2F A8 in 2-A steps (102 → 122 A) before settling
  at 30 70 (124 A). One full ramp captured at ~5 frames per rung.
- **Key-off / R1 N transition**: snaps back to `27 10` and stays there through
  shutdown.
- **Active AC charging**: after startup/transition rows, persistent `27 10`
  throughout normal charge.
- **Brief mid-drive transients** at low SOC: drives at SOC 10–14 % show
  sporadic ~2 s excursions to the `27 10` pattern with no abnormal current or
  voltage in F100F3 at the moment of the dip. Cell voltages, temperatures,
  and the F106 state byte are all unremarkable at the dip instants too —
  these read as whole-frame resets to the default broadcast, not limit
  recalculations.

The sustained 100.0 A clamp is a **separate threshold** from the F108
code 101 (SOC ≤ 15 %) and code 140 fault bits: a continuous ~15-minute
drive from 14 % down to exactly 10 % held codes 101 + 140 on F108F3
throughout without F107F3 sustaining the clamp (only the brief
transients above), while a drive that crossed below 10 % held the
clamp continuously.

Byte 5 is a pack-voltage echo with a **single linear encoding** — the
previously-reported "banded, non-linear" behaviour was an analysis artifact:

> **V_pack ≈ 0.222 × b5 + 56.9**, valid whenever byte 4 = 0x01.

Fit across 1,994 load samples from six driving captures (regen, high-gear,
full-throttle reverse, braking, accelerate-decelerate): **R² = 0.9961, max
residual 0.14 V, mean residual 0.075 V**. The fit holds across the full
pack-voltage range and at idle — idle captures show b5 = 0x61,
which the formula maps to 78.4 V against an F100F3 reading of 78.5 V. So byte 5
is a coarser companion to the F100F3 pack-voltage field, with byte 4 acting
as its enable.

The discharge-voltage-limit and contactor-diagnostic theories remain ruled out
(a static limit would not tick-by-tick track instantaneous V_pack; contactor
state is discrete), but the "four bands with distinct intercepts" picture is
withdrawn. The apparent bands were a single continuous line: the doc's
"heavy-load sag" (0x5A–0x61) and "sustained drive" (0x6F–0x77) regions lie on
one slope, and the pooled-R²=0.04 collapse was caused entirely by the cited
"idle 0x56–0x59 → 101.7–102.2 V" band, which **does not exist in the real idle
captures** (they read b5 = 0x61 → 78.5 V, on the line). Those 102 V samples
were stale boot/transition frames, not a fourth band. Not surfaced as a separate channel — the
full-precision pack voltage is already on `state.pack_v`.

#### F108F3 — BMS active fault bitmap — CONFIRMED via injection

Active BMS fault flags. All bytes 0x00 in healthy idle (verified
against `asc/bms-error-codes/idle-no-bms.asc`).

Every per-bit assignment below was established by spoofing F108 with
each bit set in isolation and reading the resulting code off the
dashboard. The layout is non-uniform — different bytes use different
bits-per-code rates:

| Byte | Encoding         | Codes                                  |
|------|------------------|----------------------------------------|
| 0    | 2 bits per code  | 100, 101, 102, 103                     |
| 1    | 2 bits per code  | 104, 105, 106, 107                     |
| 2    | 2 bits per code  | 108, 109, 110, 111                     |
| 3    | 2 bits per code  | 112, 113 (bits 4..7 silent; 114/115 reserved) |
| 4    | 1 bit per code   | bit 0=116, bit 1=117, ..., bit 7=123   |
| 5    | 1 bit per code   | bit 0=124, bit 1=125, bit 2=126, bit 3=127 (bits 4..7 silent) |
| 6    | (all silent)     | —                                      |
| 7    | 1 bit per code, with gaps | (see byte-7 table below)      |

For the 2-bit bytes the dashboard treats either bit of a pair as the
code being asserted — SAE J1939 "2-bit status" convention (00 = off,
01/10/11 = on at varying severity, dashboard renders any non-00 pair
as the code on).

##### F108 byte 7 mapping

| Bit | Mask | Code | Meaning                                       |
|-----|------|------|-----------------------------------------------|
| 0   | 0x01 | 140  | System fault level                            |
| 1   | 0x02 | —    | Status flag, no dashboard code. Co-fires with bit 0 (140) and qualifies it — see byte-7 bit-1 note below. PARTIALLY CONFIRMED |
| 2   | 0x04 | —    | (silent)                                      |
| 3   | 0x08 | 142  | BMS fault need maintenance                    |
| 4   | 0x10 | 143  | Battery fault need maintenance                |
| 5   | 0x20 | 144  | Battery system fault needs maintenance        |
| 6   | 0x40 | 144  | Duplicate of bit 5 (re-verified)              |
| 7   | 0x80 | 145  | Full charge/discharge cycle needed            |

Notable:

- Code 146 ("Maintenance mode status") is listed in the manual but is **not**
  encoded in F108 anywhere.
- Bit 6 genuinely re-asserts code 144 (re-verified with single-bit
  injection). Likely a severity-pair the dashboard renders
  identically.
- Bit 2 has never been observed asserted; semantics UNKNOWN.

**Byte-7 bit 1 (0x02) — qualifier on code 140** (PARTIALLY CONFIRMED).
Across the capture corpus, bit 1 has only two observed byte-7
contexts: `0x03` (= 140 + bit 1, dominant) and `0xBB` (= 140 + bit 1 +
142 + 143 + 144 + 145). It **never fires without bit 0 (code 140)** —
a rule that now also holds across the 500k+ frames of a full 10→100 %
charge. But the reverse is not true: code 140 can fire with bit 1
*clear*, and that case shows up only in charging contexts: byte 7 =
`0x01` (140 alone, sustained through hours of active charging
alongside the full-charge codes 102 + 109 elsewhere in the frame) and
`0xB9` (140 + 142 + 143 + 144 + 145; the "full-charge maintenance"
pattern). In both, bit 1 is suppressed exactly as the
charging-qualifier semantic predicts.

That gives a tight semantic: **bit 1 ≈ "code 140 is asserted and the
BMS is not in the charging-maintenance state"** — a discharge-side
qualifier on 140 that filters out the noisy charging-side assertions.
It still does not isolate the low-SOC limp regime cleanly on its own
(it also fires during the operator-presence-control shutdown
sequence, when the BMS reacts to bus silence at any SOC), but it is
strictly tighter than gating on `code 140` alone for limp detection.
A robust limp indicator wants `(byte-7 bit 1) AND (drive-enabled per
F100D0)`.


### Motor controller (Curtis 1238E, SA 0xCA)

The motor controller is a **Curtis 1238E** AC induction motor
controller (parts catalog Table 60, Ref 4). The MC error code table
reproduced below (codes 12, 22, 36, 41–46, 47, 49, 87–89, 99 ...)
matches the public Curtis 1238 fault-code list one-for-one, so the
Curtis 1238 manual is the authoritative reference for any FF21CA
byte questions not yet resolved here.

The motor controller emits two frames on this bus: FF21CA (motor
telemetry) and FECA (DM1, fault codes). FF21CA is suppressed entirely
while charging — the controller goes silent when traction contactors
(Albright SW200) are open.

#### FF21CA — Motor telemetry — CONFIRMED (RPM, torque, temps, state)

Broadcast at ~85 Hz. Full 29-bit ID is `0x0CFF21CA` (priority 3, not
the default 6 — higher priority than BMS broadcasts, consistent with a
real-time inverter feed).

| Byte | data[]        | Meaning                                                          |
|------|---------------|------------------------------------------------------------------|
| 1..2 | data[0..1] LE | **Torque** — unsigned magnitude of motor effort, little-endian u16, observed 0..262 |
| 3..4 | data[2..3] LE | **Motor RPM**: rpm = (le16) − 0x0C80                       |
| 5    | data[4]       | **Controller temperature**: °C = raw − 40                          |
| 6    | data[5]       | **Motor temperature**: °C = raw − 40                               |
| 7    | data[6]       | 0x00 constant — fault/status candidate (UNKNOWN)                  |
| 8    | data[7]       | **Packed transmission state** (high nibble = range, low = F/N/R)  |

**Motor RPM.** Little-endian uint16 with bias 0x0C80 (=3200).  RPM is
**magnitude only** — values below 0x0C80 are not emitted even in reverse.
Reverse is signaled separately by data[7]. Physical source is the
2-channel A/B quadrature encoder on the motor shaft — see "Motor
speed encoder" below for the MC-side pinout.

**Torque (data[0..1], little-endian u16).** This field is **not pedal
position** but rather the controller's unsigned magnitude of commanded motor
torque / current — what fraction of motor max effort the controller is asking
the inverter to apply. Symmetric across drive and regen: the value
rises whether the motor is being driven *or* being used as a
generator. The direction of work (drive vs regen) is **not encoded
anywhere in FF21CA** — derive it from the sign of `F100F3` pack
current. Observed range 0..262 (peak forward acceleration pushes past
the 8-bit boundary — see below); idle resting offset ~3 (sensor noise); below
raw ~14 the controller's dead band keeps motor RPM near 0. CONFIRMED.

Two observations that establish the "effort, not pedal" reading:

- **Stationary tests with the pedal floored in neutral** top out
  in the 0x20..0x48 range (= 13–28 % of raw scale). A pedal-position
  field would read near full-scale regardless of load; an effort-demand
  field stays low because the controller doesn't need to issue much
  torque against a held vehicle.
- **Heavy regen with the pedal fully released** drives the byte to
  0x99..0xAE (≈ 60–70 %) while pack current swings to −133 A (into the
  pack). Pedal-position cannot explain a high reading with the pedal
  off; effort-demand can.

The forward/reverse ceiling asymmetry noted historically (raw ceilings of ~262
vs ~0x96) is a controller-side reverse-effort limiter applied before the value
goes on the wire.

**data[1] is the torque high byte — CONFIRMED.** Two phenomena that were
documented separately turned out to be the same thing viewed through an 8-bit
lens: (a) data[1]'s rare `0x01` excursions, briefly mis-read as a coast /
freewheel flag, and (b) "brief 0x00 transients in data[0] under heavy load,"
flagged as a probable wraparound artifact. Decoding data[0..1] as a
little-endian u16 resolves both. Across all 353 nonzero-data[1] frames in the
corpus (12 runs in 4 driving captures), every run enters and exits through
data[0] = 0xFD–0xFF, and the 16-bit value is perfectly continuous across the
boundary (…253, 254, 256, 257, 258… up to a corpus max of 262) while RPM climbs
under hard forward acceleration — peak load, the opposite of coasting. The
converse also holds: the corpus contains **zero** frames where data[0] = 0x00
with full-scale neighbours and data[1] = 0x00, so the "transients" were never
glitches — just the low byte of a value ≥ 256. No hold-previous workaround is
needed; consumers should simply decode the u16. (The earlier "coast flag"
signature — "torque ≈ 0, forward, RPM ~510–660" — was an artifact of reading
data[0] alone: actual torque in those frames was 256–262.)

**Controller and motor temperatures (data[4], data[5]) — CONFIRMED.** Both u8
with the J1939 −40 °C offset. Raw 0 = not present (suppressed). data[4] is the
Curtis 1238E controller (inverter electronics) and data[5] is the traction
motor housing.

Confirmed by thermal-response signature over a 24-minute sustained-load capture
(highway-gear drive + mowing) with BMS pack temperature pinned at 15 °C the
whole time:

- data[4] rose 19 → 32 °C (+13 °C), data[5] rose 15 → 23 °C (+8 °C). Both
  monotonic with load — rules out non-thermal interpretations (counter, status,
  gear).
- data[4] responds tick-by-tick to load (25–28 °C oscillation during heavy
  work) and reaches the higher peak; data[5] is smoother and trails. That
  matches a small inverter heatsink (fast thermal time constant, higher
  steady-state) vs. a large motor housing (slow time constant, cooler) —
  confirming the label assignment.
- Starting values land at ambient (data[5] = 15 °C = pack temp; data[4] a few
  °C warm from prior idle), and peaks stay well below the fault thresholds (75
  °C controller, 125 °C motor) — confirming the +40 offset.

**Packed transmission state (data[7]).**

    high nibble (data[7] >> 4)  = range switch (RPM cap selector)
        0x0 = R1 (2000 RPM cap)
        0x1 = R2 (2500 RPM cap)
        0x2 = R3 (2800 RPM cap)

    low nibble  (data[7] & 0xF) = F/N/R lever
        0x0 = Neutral
        0x4 = Forward
        0x8 = Reverse

**Startup interlock.** data[7] reflects lever position only, not drivetrain
readiness. After power-on the motor controller requires the F/N/R lever to pass
through Neutral before it will accept a drive direction: the tractor will not
move even if the F or R nibble is present in data[7]. There is no CAN signal
that distinguishes this "not-yet-armed" state from normal operation — the byte
is identical in both cases. Applications must track power-on state
independently and prompt the operator to cycle through Neutral before
commanding motion.

**Range switch R1/R2/R3 — CONFIRMED.** The high nibble of data[7] is the
operator's range-switch selection from the dashboard control (3 settings
labeled with animal icons on the tractor — turtle at R1, rabbit at R3; referred
to as R1/R2/R3 throughout this documentation). It selects a motor-RPM cap (2000
/ 2500 / 2800) and is **not** a mechanical gear stage. The cap is drive-side
only: it limits inverter-commanded RPM but not coast/regen overspeed. Motor RPM
> 3000 has been observed in R3 while regen is active.

**The mechanical L/M/N/H range lever is sensor-less** and broadcasts nothing on
CAN. Its position cannot be recovered from any frame on this bus — only the
operator knows it.

**Ground speed is NOT derivable from CAN.** Wheel-speed computation
requires knowing which mechanical gear is engaged, which the bus does
not report. The CET Operator Manual page 34 publishes the linear
motor-RPM → ground-speed coefficients for both tire options. They are
recorded here as reference if the mechanical gear is known by other
means (e.g. operator input), but they are unsafe to apply to live CAN
data:

| Mechanical range | km/h per 1000 motor RPM | km/h at 2800 RPM (max) |
|------------------|-------------------------|------------------------|
| L (Low, Agri)    | 1.64                    | 4.6                    |
| M (Medium, Agri) | 3.14                    | 8.8                    |
| H (High, Agri)   | 6.25                    | 17.5                   |
| L (Low, Turf)    | 2.04                    | 5.7                    |
| M (Medium, Turf) | 3.07                    | 8.6                    |
| H (High, Turf)   | 6.07                    | 17.0                   |

"Agri" = 5×12 front / 8.0×18 rear; "Turf" = 23×8.5-12 front /
33×13.5-16.5 rear.


#### FECA (DM1) — MC fault channel — CONFIRMED via injection

Standard J1939 DM1 broadcast from SA 0xCA. Empty payload in all
recorded captures (`00 00 00 00 00 00 FF FF`) because no MC faults
occurred organically — DM1 is the right channel; it just was never
populated until injection.

| Bytes | Meaning                                                |
|-------|--------------------------------------------------------|
| 0..1  | J1939 lamp/flash bytes                                 |
| 2..4  | **SPN (= displayed MC code number)**                   |
| 5     | FMI / occurrence count                                 |
| 6..7  | 0xFFFF terminator                                      |

The cluster prepends "MC" based on source address. A populated DM1
injected from SA 0xF3 (BMS) was **ignored** by the cluster — the
cluster has subsystem-specific decoders rather than a unified DM1
path:

- MC (SA 0xCA): J1939 DM1, SPN = displayed number.
- BMS (SA 0xF3): proprietary F108 bitmap (continuous broadcast).

**Latch quirk.** The cluster latches DM1 DTCs on receipt and does **not**
unlatch when DM1 returns to empty. Standard J1939 prescribes DTCs going
"previously active" after 3 s of frame absence; this cluster keeps them on
screen until a key cycle.

FF21CA data[6] remains an unknown non-DM1 status candidate — it is `0x00` in
every one of 425,941 corpus frames. data[1] is the torque high byte, not a
status field; see above.
Injection of non-zero values into FF21CA byte 7 flashed dashboard lamps but
never produced a numeric code.


#### Motor speed encoder

The motor's 2-channel A/B quadrature encoder (no Z pulse, PPR not yet measured)
connects to the MC via a 4-pin IC pigtail. Pin assignments from the service
manual's code-12 and code-36 DTC troubleshooting procedures:

- **Pins 1 & 4** — 12 V supply (12 V should appear between these pins with the
  pigtail unplugged from the motor, IGN on)
- **Pins 2 & 3** — A and B signal channels (frequency increases proportionally
  with motor speed)

To measure PPR, probe pins 2 and 3 while spinning at a known RPM — see open
questions.


### Charger (SA 0xE5)

#### FF50E5 — Charger telemetry — CONFIRMED (V, A, status)

Charger→BMS status broadcast. The on-board charger speaks the standard,
publicly documented **Elcon/TC charger CAN protocol**: `18FF50E5` status
out of the charger, `1806E5F4` command into it (next section). All
multi-byte fields are big-endian, 0.1 unit/bit.

| Byte | data[]  | Meaning                                                |
|------|---------|--------------------------------------------------------|
| 1..2 | data[0..1] BE | **Output voltage**: V = raw × 0.1                |
| 3..4 | data[2..3] BE | **Output current**: A = raw × 0.1                |
| 5    | data[4] | **Status flags** (see below)                           |

The voltage is one 16-bit field spanning the full 0–102.3 V range —
there is no mode/range byte. During active charging it tracks pack
voltage (linear regression against F100F3 across a multi-hour L1
charge: slope 0.099–0.102 V/LSB, R² > 0.99 below 76.8 V, R² = 0.986
across the end-of-charge taper above it). Outside active charging it
reads the charger's own output terminals, not the pack: ~0.2 V with the
plug inserted but no charge running, a slow rise toward pack voltage
during key-on wake-up, and a smooth exponential decay (59 → 11 V over
~30 s) after charging stops. That continuous decay walks the high byte
0x02 → 0x01 → 0x00, which is what definitively pins the field as a
single BE-16 value.

Output current has only been observed below 25.6 A (L1 charging tops
out at ~21.5 A), so the high byte `data[2]` has read 0x00 in every
capture; an L2 (220 V) charge should push it to 0x01.

Status flags (`data[4]`) follow the Elcon convention:

| Bit | Mask | Elcon meaning                                        | Observed |
|-----|------|------------------------------------------------------|----------|
| 0   | 0x01 | Hardware failure                                     | never    |
| 1   | 0x02 | Charger over-temperature                             | never    |
| 2   | 0x04 | Input (AC) voltage abnormal / absent                 | yes      |
| 3   | 0x08 | Battery voltage not detected (output not connected)  | yes      |
| 4   | 0x10 | Communication timeout (no 1806 command received)     | yes      |

Observed flag-byte values and contexts:

| data[4] | Context                                                          |
|---------|------------------------------------------------------------------|
| 0x00    | Actively delivering charge — the only context where it reads 0  |
| 0x08    | Plug inserted, not delivering                                    |
| 0x14, 0x1C | Key-on wake-up with no AC source                              |
| 0x08 → 0x0C → 0x1C | Shutdown cascade after charging ends: output disconnects, then AC input drops, then the command stream stops |

The 0x4000 DID mirrors FF50 `data[0..3]` at its bytes 5..8 during charging;
0x4000 byte 9 is a separate unknown dynamic/status byte — see `bms/README.md`
§`0x4000`.

**Charge-current profile is constant-power, not constant-current** — CONFIRMED,
but unrelated to the status byte. Across a full L1 charge the OBC holds DC
output power at **1475 W ± 3 W (σ/μ = 0.20 %)** while pack voltage rises 70 →
83 V and current smoothly tapers 21 → 18 A. The regulator is
mains-power-limited (≈110 V × 13 A × ~96 % efficiency). L2 (220 V) charging is
expected to land at ~3 kW DC, matching the brochure's 3.3 kW rating. A brief
true-CV taper (18 A → 9.9 A in ~6 min) occurs at the very end of charge before
a clean `3 → 2 → 1 → 0` shutdown.

**The voltage field is only a pack-V reading while flags = 0x00** (output
connected, charge flowing). With the plug inserted but no charge running
the frame still beacons, reading ~0.2 V / 0 A / flags 0x08; during
key-on wake-up with no AC it reads its own rising output voltage with
flags 0x14/0x1C.

FF50E5 alone does **not** distinguish charger absent / present-but-idle /
BMS-inhibited. Plug-presence detection lives on the BMS side: F106F3 byte 0 =
0x80 asserts at the instant the J1772 is inserted (with F108F3 byte 5 = 0x01
and byte 7 = 0xBB co-asserting), and clears once active charging begins. A
robust plug-present signal across all states is therefore
`(F106F3 b0 == 0x80) OR (FF50E5 flags == 0x00)` — pre-charge handshake
plus active charging. Mid-charge unplug behavior remains uncharacterized.

#### 1806E5F4 — BMS charging command — CONFIRMED (max V, max A, enable)

The BMS→charger half of the Elcon/TC protocol, sent from the BMS's
charger-interface SA 0xF4 to the charger at 0xE5 — the bus's only
recurring PDU1 frame. Bytes 6..8 are 0xFF padding.

| Byte | data[]  | Meaning                                            |
|------|---------|----------------------------------------------------|
| 1..2 | data[0..1] BE | **Max allowed charging voltage**, 0.1 V/bit  |
| 3..4 | data[2..3] BE | **Max allowed charging current**, 0.1 A/bit  |
| 5    | data[4] | **0x00 = charge enable, 0x01 = stop**              |

Two steady command patterns observed:

| Pattern          | Decode                  | Context                                        |
|------------------|-------------------------|------------------------------------------------|
| `03 4E 01 86 00` | 84.6 V / 39.0 A, enable | Default request whenever the BMS wants charge available |
| `00 00 00 00 01` | 0 V / 0 A, stop         | Plug present but BMS not requesting charge (near-full pack, post-charge) |

The 39.0 A ceiling matches the BMS GUI's rated AC charge current
exactly, and 84.6 V = 20 × 4.23 V/cell sits just above the 83 V
charging-target voltage. When the request activates, the current
command slews 0 → 39.0 A in 10.0 A steps rather than stepping directly
— the same slew-limited style as the F107 limits ramp. Note the command
is an upper bound, not a setpoint: the L1 OBC is mains-power-limited
and delivers only ~21 A against the 39 A allowance.

Transmission gating is not fully characterized: the frame beacons
continuously while the J1772 is inserted, appears as a brief
ramp-and-stop burst around some key-ons without a plug, and is absent
entirely in other no-plug stretches.


### Vehicle controller (SA 0xD0)

#### F100D0 — VC heartbeat — CONFIRMED (byte 0 OPC state)

Same PGN as the BMS pack-status frame, disambiguated by source
address. Broadcast at ~40 Hz.

| byte 0 | Meaning                          |
|--------|----------------------------------|
| 0x00   | Operator unseated / OPC cut off  |
| 0x0C   | Operator seated / OPC enabled    |

Byte 0 is the authoritative OPC (Operator Presence Control) state on
the CAN bus. The transition is a single clean step in both directions,
confirmed across three captures (`otp-seatedon-unseatedoff.asc`,
`otp-unseatedoff-seatedon.asc`, `otp-bouncing-5s.asc`). Other bytes
remain 0xFF and have not been decoded.

**OPC timer.** The VC does not trip instantly when the operator leaves
the seat — there is a hardware grace timer (the OPC timer module; see
below). The service manual specifies the timer as **7 s** (§Tractor
Controls SOP).

**Dashboard wrench indicator.** A blinking wrench appears on the
cluster immediately when the operator leaves the seat, even before the
OPC timer fires and byte 0 transitions. The wrench is therefore driven
by a discrete seat-switch input directly to a cluster pin, not by the
CAN OPC state. This is consistent with schematic 5.9, which wires the
seat switch through discrete signals.

**OPC shutdown sequence** (from `otp-seatedon-unseatedoff.asc`,
relative to OPC trip):

```
t+0ms     18F100D0 b0:  0x0C → 0x00   VC declares operator absent
t+354ms   0CFF21CA:     last motor frame (motor controller goes silent)
t+361ms   18F100D0:     last VC frame (VC goes silent)
t+10.4s   18F108F3:     BMS fires codes 124, 140, 142, 143, 144, 145
t+41.7s   18F108F3:     BMS stops broadcasting (end of capture)
```

The BMS fault codes that fire ~10 s after shutdown are **not real
faults** — they are the BMS reacting to CAN silence after the rest of
the bus goes dark. Code 124 ("Clock fault") and the maintenance codes
(140/142/143/144/145) appear because the BMS loses contact with the
other nodes and interprets the silence as communication errors. The BMS
is the last node still broadcasting, talking to a dead bus.

SA 0xF4 is no longer treated as a separate vehicle-controller home. It sends
only `1806E5F4` to the charger at 0xE5, and that frame carries the BMS's
charger voltage/current request. The documentation therefore treats 0xF4 as a
BMS charger-interface logical SA rather than an unresolved physical module.

The parts catalog (Table 65, Ref 10) names a separate **OPC (Operator
Presence Control) timer module** — "UNIT ENGINE SHUT OFF CONTROLLER
TIMER (SEAT AND PARK OPC)" — gating shutdown on the seat switch and
park brake. The F100D0 heartbeat's OPC-state byte is consistent with
this module broadcasting its interlock status on CAN.

**Tension with service manual schematic 5.10.** The physical home of SA 0xD0
remains unresolved. Schematic 5.10 shows the main CAN bus carrying exactly four
nodes (MC, BMS, Charger, Cluster), with no CAN-speaking OPC module drawn.
Service manual schematic **5.9 (Seat OPC)** wires the OPC entirely through
discrete signals (CSS, DIS, CT, charge-drive interlock relay, seat switch, PTO
bypass, park-brake switch) — no CAN H/L on the OPC connector. That argues
against a fifth dedicated OPC CAN node and leaves 0xD0 most plausibly as a
logical source address emitted by one of the documented ECUs, with the cluster
the natural candidate because it aggregates accessory state. A BMS bridge or
an undocumented accessory controller cannot be ruled out from captures alone.


## Instrument cluster hardware

The dashboard cluster is a **COBO ECO MATRIX VT3** (Italian Tier-1
off-highway cluster, also marketed under COBO's "Unideck" sub-brand).

### Identification

| Property                          | Value                       |
|-----------------------------------|-----------------------------|
| Manufacturer                      | COBO S.p.A. (Leno, Brescia) |
| Family                            | ECO MATRIX VT3              |
| Platform                          | ECO HW UNICO VT3            |
| COBO internal part number         | 2050394                     |
| Solectrac OEM part number         | 2167780 REV.04              |
| Software revision                 | 102                         |
| Year of manufacture               | 2022 (Solectrac label 2021-08-23) |
| Display                           | 128 × 64 dot-matrix LCD + 2 cross-coil gauges + 21 LEDs |
| LCD temperature readout           | Pack temp from F104F3 data[0] (BMS max module temp, °C) |
| Housing                           | 230 × 120 mm                |
| Supply                            | 12 V (accessory)            |
| Protocols                         | CAN J1939 / ISOBUS          |
| Internal CAN termination          | None (mid-bus tap)          |
| Supported baud                    | 125 / 250 / 500 kbaud       |

Symbol layout and warning-light assignments (L1..L21) are
Solectrac-specific firmware loaded via COBO's VT3 WYSIWYG tool. A
generic ECO MATRIX VT3 unit sourced from another OEM (e.g. the Faresin
12 V variant sold by si-parts.com) would need reflashing before it
would behave correctly on the Solectrac harness.

### Connector

| Component                          | Part number          |
|------------------------------------|----------------------|
| Header on cluster                  | Tyco AMP 36-way (4 cols × 9 rows) |
| Mating connector (harness side)    | Tyco / TE 1-0640526-0 |
| Terminals                          | Tyco / TE 0-0641294-1 |

Cavity numbering is **row-major, left-to-right** (J1 = row 1 col 1,
J4 = row 1 col 4, J5 = row 2 col 1, ..., J36 = row 9 col 4). This was
inferred from the COBO datasheet J-table cross-referenced with the
Solectrac harness wiring diagram, and confirmed empirically by DMM
probing on 2026-05-13.

Observed population on this unit (15 of 36 cavities, viewed from the
harness mating face):

    col→  1 2 3 4
    row 1 . x x x      J1 empty;  J2,  J3,  J4  populated
    row 2 . x x x      J5 empty;  J6,  J7,  J8  populated
    row 3 . x . x      J9 empty;  J10 pop; J11 empty; J12 pop
    row 4 . x . .      J13 empty; J14 pop; J15, J16 empty
    row 5 x x . x      J17 pop;   J18 pop; J19 empty; J20 pop
    row 6 . . x .      J21, J22 empty; J23 pop; J24 empty
    row 7 . . . .      J25..J28 all empty
    row 8 . . . .      J29..J32 all empty
    row 9 . . x x      J33, J34 empty; J35, J36 populated

### Pinout

★ = minimum pins required for a powered cluster on CAN (J3, J4, J8,
J35, J36). Solectrac populates 15 cavities total: the five required
plus 10 discrete-input wires.

`(r,c)` = (row, col) on the populated-grid diagram above.

| Pin | (r,c) | COBO ID | Generic function                                    | Solectrac usage                |
|-----|-------|---------|-----------------------------------------------------|--------------------------------|
| J1  | 1,1   | RELE'   | Out 1 high-side, 150 mA (relay drive)               | (unused)                       |
| J2  | 1,2   | IDBL    | Positive digital input                              | BACK LIGHT (+)                 |
| J3  | 1,3   | 30      | + Battery (constant 12 V)                       ★   | + BATTERY                      |
| J4  | 1,4   | 15      | + Key (ignition / KL15)                         ★   | IGN ON (+)                     |
| J5  | 2,1   | FR1     | Frequency input, ≤1500 Hz                           | (unused — speed via CAN)       |
| J6  | 2,2   | ID9     | Positive digital input                              | TURN RIGHT (+)                 |
| J7  | 2,3   | ID10    | Positive digital input                              | TURN LEFT (+)                  |
| J8  | 2,4   | 31      | GND                                             ★   | GND                            |
| J9  | 3,1   | ID3     | Negative digital input                              | (unused)                       |
| J10 | 3,2   | ID1     | Negative digital input                              | 4WD (−) — forward indicator    |
| J11 | 3,3   | ID5     | Negative digital input                              | (unused)                       |
| J12 | 3,4   | ID20    | Positive digital input                              | TURN TRAILER (+)               |
| J13 | 4,1   | ID2     | Negative digital input                              | (unused)                       |
| J14 | 4,2   | ID8     | Positive digital input                              | HEADLIGHTS (+)                 |
| J15 | 4,3   | AN2     | Analog resistive input, 90 Ω pull-up (sender)       | (unused)                       |
| J16 | 4,4   | AN1     | Analog resistive input, 90 Ω pull-up (sender)       | (unused)                       |
| J17 | 5,1   | ID12    | Positive digital input                              | RUNNING LIGHTS (+)             |
| J18 | 5,2   | ID13    | Negative digital input                              | PTO (−)                        |
| J19 | 5,3   | P/BR    | Positive digital input (probable Park Brake)        | (unused)                       |
| J20 | 5,4   | ID17    | Positive digital input                              | BATTERY CHARGING (+)           |
| J21 | 6,1   | ID6     | Positive digital input                              | (unused)                       |
| J22 | 6,2   | ID21    | Negative digital input                              | (unused)                       |
| J23 | 6,3   | ID18    | Negative digital input                              | PARKING BRAKE (−)              |
| J24 | 6,4   | ID16    | Positive digital input                              | (unused)                       |
| J25 | 7,1   | PB/L    | Positive digital input (probable Park Brake Light)  | (unused)                       |
| J26 | 7,2   | ID15    | Negative digital input                              | (unused)                       |
| J27 | 7,3   | ID19    | Negative digital input                              | (unused)                       |
| J28 | 7,4   | ID14    | Negative digital input                              | (unused)                       |
| J29 | 8,1   | ID11    | Negative digital input                              | (unused)                       |
| J30 | 8,2   | ID7     | Positive digital input                              | (unused)                       |
| J31 | 8,3   | ID4     | Negative digital input                              | (unused)                       |
| J32 | 8,4   | BUZZER  | Out 2 low-side, 150 mA (audible alert)              | (unused)                       |
| J33 | 9,1   | D+      | D+ alternator excite, neg. digital input            | (unused — no alternator)       |
| J34 | 9,2   | CS      | CAN shield                                      ★   | (unused — no shield drain)     |
| J35 | 9,3   | CL      | CAN L                                           ★   | CAN L                          |
| J36 | 9,4   | CH      | CAN H                                           ★   | CAN H                          |

### Diagram errata

The Solectrac harness wiring diagram has three labelling issues and
one omission relative to the as-built tractor:

1. The + BATTERY pin is labelled "Pin 1" on the diagram. The actual
   cavity is J3 (J1 is an empty cavity in the populated grid).
2. J14 is labelled "DIPPED BEAM (+)". Solectrac uses it as the general
   HEADLIGHTS indicator. ("Dipped beam" is the EU term for low-beam
   headlights.)
3. J18 is populated but not on the diagram. Identified as PTO
   indicator (−), switch-to-ground when PTO is engaged.

### Diagnostic tap

A non-destructive diagnostic harness can T-tap J35/J36 (row 9 cols
3-4) without unplugging the cluster — the display stays functional
while a capture tool reads the live bus.


## Vendor error code tables

Reproduced from the operator manual for cross-reference. Detecting
conditions in parentheses are from the service manual DTC
troubleshooting section; codes without a parenthetical have no
explicit threshold data in this corpus. The disambiguation in the
F108F3 and DM1 sections above maps these numbers to bit positions and
SPN values respectively. The two ranges do not overlap, so a dashboard
"code 47" is unambiguously MC and "code 124" is unambiguously BMS.

### BMS codes (100..146)

    100  SOC is too high                   (pack V > 84 V)
    101  SOC is too low                    (SOC ≤ 15 %; pack V < 60 V)
    102  Total voltage is too high         (pack V > 84 V)
    103  Total voltage is too low          (SOC ≤ 15 %; pack V < 60 V)
    104  Charge current fault              (charge I differs from programmed)
    105  Discharge current fault           (discharge I differs from programmed)
    106  Battery temperature is too low    (cell temp < −10 °C)
    107  Battery temperature is too high   (cell temp > 54 °C)
    108  Battery under voltage             (SOC ≤ 15 %; pack V < 60 V)
    109  Battery over voltage              (pack V > 84 V)
    110  Battery temperature unbalance
    111  Battery voltage unbalance
    112  The battery does not match
    113  The temperature of the output pole is too high
    [114, 115 not in manual — reserved]
    116  The parameters of memory fault
    117  Data memory fault
    118  Cell voltage detection fault
    119  Temperature detection fault
    120  Current detection fault
    121  Internal total voltage detection fault
    122  External total voltage detection fault
    123  Insulation monitoring fault
    124  Clock fault
    125  Internal CAN communication fault
    126  Serious insulation fault
    127  Slight insulation fault
    [128..139 not in manual — reserved]
    140  System fault level
    [141 not in manual — reserved]
    142  BMS fault need maintenance
    143  Battery fault need maintenance
    144  Battery system fault needs maintenance
    145  The battery needs to maintenance (full charging and full discharging)
    146  Maintenance mode status

32 codes; F108 has 64 bits, so the layout has plenty of headroom.

### MC codes (12..99)

    12  Controller Over Current          (current > limit or phase short; motor phase R < 9 mΩ)
    13  Current Sensor Fault             (sensor reading invalid or absent)
    15  Controller Severe Undertemp      (controller temp < −10 °C)
    16  Controller Severe Overtemp       (controller temp > 75 °C)
    17  Severe B+ Undervoltage           (B+ input < 62 V)
    18  Severe B+ Overvoltage            (regen pushes pack > 84 V)
    18  Severe KSI Overvoltage           (KSI pin > 84 V) [duplicate S.No. 18]
    22  Controller Over temp Cutback     (controller temp > 60 °C; cutback, not shutdown)
    23  B+ Undervoltage Cutback
    24  B+ Overvoltage Cutback           (pack > 84 V)
    25  +5V Supply Failure               (pin 26 load impedance too low)
    28  Motor Temp Hot Cutback           (motor temp > 125 °C)
    29  Motor Temp Sensor Fault
    31  Coil1 Driver Open/Short          (contactor coil; 150 Ω at J1-6↔J1-13)
    31  Main Open/Short                  [duplicate S.No. 31]
    32  Coil2 Driver Open/Short
    32  EM Brake Open/Short              [duplicate S.No. 32]
    36  Encoder Fault                    (signal invalid; 12 V on pins 1&4, signal on pins 2&3)
    36  Sin/Cos Sensor Fault             [duplicate S.No. 36]
    37  Motor Open                       (phase open; motor phase R < 9 mΩ)
    38  Main Contactor Welded            (won't open after IGN off)
    39  Main Contactor Did Not Close     (didn't close at startup)
    41  Throttle Wiper High
    42  Throttle Wiper Low
    43  Pot2 Wiper High
    44  Pot2 Wiper Low
    45  Pot Low Over Current
    46  EEPROM Failure
    47  HPD/Sequencing Fault
    49  Parameter Change Fault
    51  Vehicle lock without applying hand brake   [out of order in manual]
    72  PDO Timeout
    73  Stall Detected
    83  Driver Supply
    87  Motor Characterization Fault
    88  Encoder Pulse Count Fault
    89  Motor Type Fault
    92  EM Brake failed to set
    99  Parameter Mismatch

39 entries, 35 distinct S.No. values. Codes 18, 31, 32, 36 each have two
definitions sharing a number — a numeric code on the dashboard does not
uniquely identify the underlying fault for those four; disambiguation needs
additional context (which subsystem is implicated by other simultaneous
symptoms, vendor service-tool readout, etc.). Code 51 is listed out of numeric
order in the manual.


## Open questions

- **SOH confirmation.** F100F3 data[5] (0xFA = 100 %) is the leading
  candidate, but every capture is at SOH 100 % — confirming needs a
  pack whose SOH differs. See the F100F3 section.
- **F106F3 vocabulary.** Byte 0's bitfield ladder (0x00 / 0x40 / 0x44 /
  0x45 / 0x80 / 0x84 / 0x85) and byte 1's five status values are mapped,
  but the vendor GUI implies more states (Calibrating, Fault, Sleep) —
  whether those get their own codes is unobserved. See the F106F3
  section.
- **F107F3 limit semantics.** Static configured ratings vs computed
  limits; pulse vs continuous (the BMS GUI rates charge at 78 A DC,
  below every broadcast 100–130 A value); SOC taper latched at frame
  activation vs live-tracked; SOC vs cell-Vmax vs temperature as the
  taper's driver. Discriminators: read the stored current thresholds
  off the UDAN config page; capture a drive crossing a SOC band edge;
  capture a sustained >60 s heavy pull; capture a cold-pack drive. See
  the F107F3 section.
- **0x7FD wake frame on the BMS diagnostics bus.** One 8-byte all-zero
  11-bit frame ~10 ms after each key-on. Origin and purpose unknown.
- **FF21CA data[6].** `0x00` in every one of 425,941 corpus frames;
  meaning unknown.
- **SA 0xD0 physical home.** Likely a logical SA emitted by one of the
  four documented ECUs (cluster the natural candidate), but captures
  alone can't rule out a BMS bridge or undocumented accessory
  controller.
- **Motor encoder PPR.** Not in any manual. Readable as the Curtis
  "Encoder Steps" parameter via a 1313 programmer, or by counting
  pulses on encoder pins 2/3 at a known RPM. See "Motor speed encoder".


## Sources

- [COBO ECO MATRIX VT3 datasheet](https://www.si-parts.com/cataloghi_cobo/display-quadri-bordo/ECO_MATRIX_VT3.pdf):
  documentation for the screen/cluster.
- [COBO product page (Faresin 12 V variant)](https://www.si-parts.com/en/instruments-clusters/13181-eco-matrix-faresin-12v-panel.html): product page for the screen/cluster.
- ["BMS Update" document](https://docs.thebackyard.engineer/solectrac/troubleshooting-guides/documentation)
- [Solectrac master schematic set](https://solectracsupport.com/support/manuals): Harness wiring + CAN topology
  diagrams (a few errors are detailed in the document).
- [FT 25G service manual](https://solectracsupport.com/FT_25G_Service_manual-10-08-2023.pdf) (319 pages, dated 2023-07-13) — the
  primary electrical/CAN authority for this vehicle.
- [CET Operator Manual](https://solectracsupport.com/FT25GUSAOPM.pdf) (63 pages, the international "Compact
  Electric Tractor" rebadge of the FT 25G):
- [UDAN iBMS Upper Utility](https://www.ievcloud.com/burner_en.html) from [Anhui UDAN Technology
  Co., Ltd.](https://www.udantech.com/en/) — Chinese BMS firmware/diagnostic-tool vendor.
  * [User manual](https://img1.wsimg.com/blobby/go/e508f0fc-822a-4879-b2a3-a5a9c4be953e/downloads/985f520a-9b18-4315-bb99-f803a6a45437/UDAN%20BMS%20upper%20computer%20software%20user%20manual%20V.pdf?ver=1752289934030)
- [Solectrac Parts Catalog (e25)](https://docs.thebackyard.engineer/solectrac/troubleshooting-guides/documentation):
  source for the named components referenced throughout this
  document.
- [Curtis 1238 controller manual](https://www.thunderstruck-ev.com/Manuals/1234_36_38%20Manual%20Rev%20Feb%2009.pdf):
  public fault-code-list reference for the MC short codes reproduced above.
- [Kelly KLS7218MC / KLS7218NC CAN protocol](https://docs.thebackyard.engineer/solectrac/troubleshooting-guides/documentation):
  kept for
  reference, but the e-hydraulic controller on this vehicle is not
  wired to a CAN bus, so the Kelly protocol is not applicable here.
