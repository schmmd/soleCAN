# Curtis 1238E CANopen interface — Solectrac e25G

The traction motor controller on the Solectrac e25G is a Curtis 1238E
(E-series AC controller, OEM VCL program by Solectrac). Besides its J1939
broadcasts (source address `0xCA`, see `DOCUMENTATION.md`) it runs a CANopen
SDO server on the **same** 250 kbit/s main bus. Every parameter and monitor
variable of the controller is readable, and parameters are writable, through
that server. This document is the reference for that interface.

Everything here is empirical. Each decode carries a confidence marker:
**CONFIRMED** (cross-validated against a second source, an official name, or
a physical check), **TENTATIVE** (consistent behaviour, one line of
evidence), **UNKNOWN**. The evidence trail, in chronological order, is in
[`NOTES.md`](NOTES.md); this file states only the current conclusions.

Contents

1. [Bus and node parameters](#1-bus-and-node-parameters)
2. [Object dictionary layout](#2-object-dictionary-layout)
3. [Identity and communication profile](#3-identity-and-communication-profile)
4. [Decoded objects](#4-decoded-objects)
5. [Mechanisms](#5-mechanisms)
6. [Writing parameters](#6-writing-parameters)
7. [PDOs](#7-pdos)
8. [Tools and data files](#8-tools-and-data-files)
9. [Open questions](#9-open-questions)
10. [Sources](#10-sources)

---

## 1. Bus and node parameters

| Item | Value | Confidence |
|---|---|---|
| Bus | Main vehicle bus, 250 kbit/s, shared with J1939 | CONFIRMED |
| Frames | 11-bit standard IDs (all J1939 traffic is 29-bit) | CONFIRMED |
| Node ID | **Range-switch selected at key-on**: R1 = 38, R2 = 39, R3 = 40, both = 41 (see [Node ID](#node-id)) | CONFIRMED |
| SDO request / response | `0x600 + node` / `0x580 + node` (R3: `0x628` / `0x5A8`) | CONFIRMED |
| Transfer type | Expedited only; every object is ≤ 4 bytes, no segmented or string objects | CONFIRMED |
| Heartbeat | `0x1017` producer heartbeat time = 25 ms, but no heartbeat frames are seen | CONFIRMED |
| NMT state | Pre-operational (`0x3328 CAN_NMT_State` = 127); PDOs configured but dormant | CONFIRMED |
| EMCY | `0x80 + node`; none observed yet (no fault provoked, see §9) | — |
| SDO server capacity | One outstanding request; a ~200 SDO/s poller silently drops contended requests. Retry. | CONFIRMED |

### Node ID

The Curtis picks its node ID at every key (KSI) turn-on from switch inputs
Sw5/Sw6 (manual: "CAN Node ID 1..4"), and on this tractor Sw5/Sw6 are the R2/R3
range switches. The four stored slots read from this controller:

| Object | Name | Sw6 / Sw5 | Value | Range position |
|---|---|---|---|---|
| `0x3140` | CAN_Node_ID_1 | off / off | 38 | R1 |
| `0x3141` | CAN_Node_ID_2 | off / on | 39 | R2 |
| `0x3146` | CAN_Node_ID_3 | on / off | 40 | R3 |
| `0x3147` | CAN_Node_ID_4 | on / on | 41 | (both) |
| `0x3145` | active node ID (monitor) | | | |

The J1939 side (SA `0xCA`) is unaffected. All tools here and the firmware
poller auto-discover the node (probe 40, 39, 38, 41, then sweep 1..127) rather
than hard-coding 40. The dumps in this directory were taken in R3 (node 40).

## 2. Object dictionary layout

| Index range | Contents |
|---|---|
| `0x1000`–`0x1A03` | CiA 301 communication profile: identity, heartbeat, SDO/PDO parameters and mappings |
| `0x2000`, `0x2001` | Curtis manufacturer records (`0x2000` sub 7 = ASCII `"CUR "`; `0x2001` 22 subs, all zero) |
| `0x3000`–`0x32FF` | Curtis stock parameters and monitor variables, named in the E-series manual |
| `0x3300`–`0x36FF` | **OEM block**: almost entirely unnamed monitors, i.e. the Solectrac VCL program's RAM variables (User/AutoUser) plus a few named Curtis CAN/VCL objects (`0x3308`, `0x332F`, `0x3328`, …) |
| `0x3800`–`0x3CFF` | Curtis motor-characterisation / dealer parameters and encoder monitors; partly named in the OS 31 manual |
| `0x5700`–`0x5742` | Empty history arrays (all zero) |

Counts (full walk, `canopen_full.txt`): 1420 objects with a sub 0; 2261
non-zero sub-indices across 378 objects; 299 objects carry an official Curtis
name (OS 30 + OS 31 manuals merged).

**Parameter vs monitor.** The sub-index layout distinguishes the two kinds of
object without needing a name (CONFIRMED against every named object):

| Kind | Sub 0 | Sub 3 | Sub 4 | Writable |
|---|---|---|---|---|
| Parameter (EEPROM-backed) | value | minimum | maximum | yes (RAM; EEPROM if `0x332F` set) |
| Monitor variable | value | — | — | no |

This splits the dictionary into 366 parameters (178 unnamed) and 1040 monitors
(929 unnamed). Sub-index 5 of **every** object returns 0 (a firmware quirk;
ignore it). Subs 1/2 appear only on the communication-profile records and the
`0x2000`/`0x2001`/`0x57xx` arrays.

The min/max pair types an unnamed parameter: 0..1 boolean, 0..32767 percent,
100..8000 or 0..6000 rpm, 0..4096 cutback fraction (4096 = 100 %),
1408..12800 volts in 1/64 V.

**Scaling rule.** Each Curtis monitor variable carries its own scale; take it
from the manual's raw-range / display-range pair for that object. Do not
assume a common scale across objects (capacitor voltage is /64 V, keyswitch
voltage is /100 V).

## 3. Identity and communication profile

| Object | Value | Meaning | Confidence |
|---|---|---|---|
| `0x1009:00` | `"3079"` | manufacturer hardware version | CONFIRMED |
| `0x100A:00` | `"3178"` | manufacturer software version (OS 31.78) | CONFIRMED |
| `0x1017:00` | 25 | producer heartbeat time, ms | CONFIRMED |
| `0x1018:01` | `0x4349` | vendor ID, ASCII `"CI"` = Curtis Instruments | CONFIRMED |
| `0x1018:04` | 15198 | serial number | TENTATIVE |

Quote SW 3178 / HW 3079 / serial 15198 when asking Curtis for the EDS of this
OS version.

## 4. Decoded objects

All values are little-endian. `s16` = signed 16-bit, `u16` = unsigned 16-bit.
"J1939" notes the equivalent field in the `FF21CA` broadcast where one exists.

### 4.1 Drive state and switch inputs

| Object | Name | Encoding | Confidence |
|---|---|---|---|
| `0x3226` | Switches | u16 bitfield, bit n = Sw_(n+1) (see below) | CONFIRMED |
| `0x3224` / `0x3225` | Switches low / high byte | Sw_1–8 / Sw_9–16 | CONFIRMED |
| `0x33E6` | OEM packed range + direction | `(range << 12) \| (dir << 10)`; range R1=0 R2=1 R3=2; dir N=0 F=1 R=2 | CONFIRMED |
| `0x322B` | System_Flags1 ("Interlock") | bit 5 = interlock closed (throttle accepted); bit 2 = at rest; bit 0 always set | CONFIRMED |
| `0x3223` | Main_State | main-contactor state machine; 5 while driving-ready | CONFIRMED |
| `0x3328` | CAN_NMT_State | 127 = pre-operational | CONFIRMED |
| `0x3892` | EMBrakeState | 2 in every capture | CONFIRMED (name) |
| `0x3228` | OEM flag word | bits 6/7 set while moving; bits 3–6 latch once lever/range have been used; 1404 during OPC power-down | TENTATIVE |
| `0x3540` | OEM stationary flag | 1 at rest, 0 while moving | TENTATIVE |
| `0x3602` | OEM motion flag | 5 at rest, 640 whenever rpm ≠ 0 | TENTATIVE |

Switch word bits (`0x3226`), pin numbers from the manual's 35-pin connector table:

| Bit | Input | Pin | Function on the e25G |
|---|---|---|---|
| 2 | Sw_3 | 9 | always on (enable/interlock-type input) |
| 3 | Sw_4 | 10 | range R1 (turtle) |
| 4 | Sw_5 | 11 | range R2 (also selects node ID) |
| 5 | Sw_6 | 12 | range R3 (rabbit) (also selects node ID) |
| 6 | Sw_7 | 22 | FORWARD lever |
| 7 | Sw_8 | 33 | REVERSE lever |
| 10 | Sw_11 | 4 | OPC (operator presence) enable; drops first at seat power-down |

Examples: neutral + R3 = `0x424`, forward + R3 = `0x464`, reverse + R3 =
`0x4A4`, neutral + R1 = `0x40C`.

**Not wired to the Curtis** (no object changes when they are toggled,
CONFIRMED by stationary sweep): brake pedal, parking brake, hydraulics switch,
PTO switch. These land on the cluster (`DOCUMENTATION.md`, J23/ID18) or the
Kelly hydraulic controller. `0x321A Brake_Command` and `0x3212 Mapped_Brake`
read 0 always: the Curtis brake-pedal (regen) input is unused on this machine.

### 4.2 Speed limits

| Object | Name | Value / encoding | Confidence |
|---|---|---|---|
| `0x3103` / `0x3104` | OEM table, R3 forward / reverse | 2800 / 2240 rpm | CONFIRMED |
| `0x3105` / `0x3106` | OEM table, R1 forward / reverse | 2000 / **2000** rpm (stock 1600, see §6) | CONFIRMED |
| `0x3107` / `0x3108` | OEM table, R2 forward / reverse | 2500 / **2500** rpm (stock 2000, see §6) | CONFIRMED |
| `0x3011` | Max_Speed_SpdM | the **active** cap, rewritten by the VCL per range + direction; 1200 after power-up until the lever first leaves neutral | CONFIRMED |
| `0x306E`, `0x3593` | ramped copies of `0x3011` | follow `0x3011` in ~200 rpm steps over ~1 s | CONFIRMED |
| `0x3840`, `0x33D1` | Max_Speed_SpdMx / RPDO speed-limit input | direct copies of `0x3011` | CONFIRMED |
| `0x3021` | Max_Speed_TrqM | 4000 (torque-mode cap, unused: `0x3010 Control_Mode_Select` = 1 speed mode) | CONFIRMED |
| `0x3559` | Max_Speed_Controller_Limit | 8000, the controller ceiling, not the range cap | CONFIRMED |
| `0x3213` | Throttle_Multiplier | 128 normal; 0 after power-up until the lever leaves neutral | CONFIRMED |
| `0x354B` | OEM speed-ramp target / limit selector | steps between Max_Speed-like values | TENTATIVE |

The stock table pairs each forward cap with exactly 80 % of itself in reverse,
which is the controller-side reverse limiter documented for the J1939 decode.

### 4.3 Motor and electrical monitors

| Object | Name | Scale | Typical | J1939 | Confidence |
|---|---|---|---|---|---|
| `0x3207` | Motor_RPM | s16, 1 rpm, sign = direction | 0 / 2792 at R3 full throttle | bytes 2–3 = \|rpm\| + 3200 | CONFIRMED |
| `0x3209` | Current_RMS | s16 × 0.1 A, motor phase current | 3 A idle spin, 143 A peak chipping | bytes 0–1 = whole amps | CONFIRMED |
| `0x3206` | Frequency | ≈ 2.0 × rpm (electrical speed; 4-pole motor) | | | CONFIRMED |
| `0x3208` | Modulation_Depth | 0–1182 = 0–100 % (observed up to 1235) | saturates near 2800 rpm | | CONFIRMED |
| `0x320A` | Vehicle_Speed | 0.1 mph, = 0.0394 × rpm, fixed ratio for HIGH mechanical range; over-reads in M/L | 112 = 11.2 mph at 2800 rpm | | CONFIRMED |
| `0x320B` | Motor_Temperature | s16 × 0.1 °C | | yes | CONFIRMED |
| `0x322A` | Controller_Temperature | s16 × 0.1 °C | | yes | CONFIRMED |
| `0x3581`, `0x35F3`, `0x3604`, `0x3605` | cutbacks (motor temp / ctrl temp / over-V / under-V) | 4096 = 100 % = no cutback | | | CONFIRMED |
| `0x306C` | Swap_Two_Phases | bit 3 = swap direction (Victron reading) | 211, bit 3 clear | | TENTATIVE |
| `0x393A` | \|rpm\| copy | u16 | | | CONFIRMED |
| `0x33FC` | OEM staging: \|rpm\| + 3200 | the variable the VCL copies into J1939 bytes 2–3 | | | CONFIRMED |
| `0x33EA` | OEM staging: Current_RMS in whole amps | = J1939 bytes 0–1 exactly | | | CONFIRMED |
| `0x35D1`, `0x35D2`, `0x35D6` | MotorspeedA/B and a third encoder-phase speed | ≈ \|rpm\| with up to 1000 rpm lag | | | TENTATIVE |
| `0x355E` | filtered ≈ 2 × rpm | | | | TENTATIVE |
| `0x354E`, `0x35D3`, `0x3553`, `0x35AC` | current mirrors on other filters (≈ 10–14 × Current_RMS) | | | | TENTATIVE |
| `0x38C7` | rotor_position_raw | | | | CONFIRMED (name) |

The headline result: the J1939 `FF21CA` bytes 0–1, previously read as
"torque/effort", **are motor RMS current in amps** (`0x33EA` fits with slope
1.0006, zero offset, worst residual 2 over 75 samples). Torque is
proportional, so the older reading is not wrong, only unscaled.

### 4.4 Battery and DC side

| Object | Name | Scale | Typical | Confidence |
|---|---|---|---|---|
| `0x324C` | Capacitor_Voltage | u16 / 64 V (0–12800 = 0–200 V) | 4634 = 72.4 V; sags to 70.3 V under load | CONFIRMED |
| `0x324D` | Keyswitch_Voltage | u16 / 100 V (0–10500 = 0–105 V) | 7233 = 72.3 V | CONFIRMED |
| `0x359E` | Battery_Current | s16 × 0.1 A, the traction controller's DC draw only | 0 idle, 34–47 A chipping | CONFIRMED |
| `0x3308` | BDI_Percentage | 0–100 %; **a relay of the BMS SOC** written by the VCL from J1939 `F100F3`, not an independent estimate | tracks dashboard SOC within 1 point | CONFIRMED |
| `0x3048` | Nominal_Voltage | /64 V | 4864 = 76 V | CONFIRMED (value) |
| `0x3170`–`0x3172` | BDI_Reset/Full/Empty_Volts_Per_Cell | mV | 2090 / 2040 / 1730 (lead-acid defaults, unused) | CONFIRMED (value) |
| `0x3806` | unnamed parameter, 1408..12800 | /64 V | 4619 = 72.2 V, a nominal-pack-voltage setting | TENTATIVE |

Neither Battery_Current nor Capacitor_Voltage is in the J1939 broadcast, so
these are genuinely new quantities; together they give controller DC power.
Pack current (BMS) = Curtis Battery_Current + Kelly hydraulic pump (~20–33 A
whenever the hydraulics switch is on) + ~2 A auxiliaries (CONFIRMED, session
137).

### 4.5 Throttle

| Object | Name | Scale | Typical | Confidence |
|---|---|---|---|---|
| `0x3215` | Throttle_Pot_Raw | 0–36044 = 0.0–5.5 V at the pedal wiper | 4000 = 0.61 V rest, 29204 = 4.46 V floored | CONFIRMED |
| `0x30D2` | copy of Throttle_Pot_Raw | | | CONFIRMED |
| `0x3211` | Mapped_Throttle | s16, ±32767 = ±100 %, sign = direction | | CONFIRMED |
| `0x3216` | Throttle_Command | s16, ±32767 = ±100 % | ±1 at zero throttle with a direction selected | CONFIRMED |
| `0x3218`, `0x3521`, `0x3402` | OEM copies of Throttle_Command; `0x3218` is the RPDO0 input | | | CONFIRMED |
| `0x3217` | Pot2_Raw | unused input | 5280 constant | CONFIRMED |
| `0x3204` | Analog1 | unused input | 5 constant | CONFIRMED |
| `0x3000` / `0x300A` | Throttle_Type / Brake_Type | 2 / 2 | | CONFIRMED |
| `0x3001`–`0x3008` | Forward/Reverse Deadband, Map, Max, Offset | throttle shaping parameters | | CONFIRMED (names) |

### 4.6 Stock configuration of note

| Object | Name | Value | Meaning |
|---|---|---|---|
| `0x3010` | Control_Mode_Select | 1 | speed mode (0 speed-express, 2 torque) |
| `0x3012` / `0x3015` | Kp_SpdM / Ki_SpdM | 2458 / 300 | speed-loop gains |
| `0x305B` | Drive_Current_Limit | 16384 | 50 % of controller rated current |
| `0x305C` / `0x305D` | Regen / Brake_Current_Limit | 32767 / 32767 | 100 % |
| `0x3149` | CAN_PDO_Timeout_Period | 25 | PDO timeout monitoring **enabled** (see §7) |
| `0x332F` | CAN_EE_Writes_Enabled | 0 | SDO writes go to RAM only (see §6) |

Full list: `canopen_named.csv` (filter `vcl_name != ""`) and
`canopen_params.csv`.

### 4.7 Faults

| Object | Name | Observed | Confidence |
|---|---|---|---|
| `0x3238` / `0x3239` | UserFault1 / 2 (OEM VCL fault bits) | 0 | CONFIRMED (name) |
| `0x3231` / `0x3232` | Hist_UserFault1 / 2 | | CONFIRMED (name) |
| `0x389A` / `0x389B` | UserFault1/2_History | `0x389A` = 1: a user fault has been logged at some point | CONFIRMED (name) |
| `0x323B`–`0x324A` | User_Fault_Action_01..16 | | CONFIRMED (name) |
| `0x3472` | Last_VCL_Error | | CONFIRMED (name) |
| `0x3897` | Supervision_Error | | CONFIRMED (name) |

No fault has yet been provoked while polling; whether the node emits EMCY and
how EMCY codes map to the J1939 DM1 codes is open (§9). The idle DM1 pattern
from `0xCA` is `00 00 00 00 00 00 FF FF` at ~1 Hz.

### 4.8 Counters and timers

| Object | Name | Behaviour | Confidence |
|---|---|---|---|
| `0x3160` | Master_Timer | key-on run-time counter, 9.57 ticks/s while powered, EEPROM-backed, stops when off. 6,520,553 ticks ≈ 189 h at end of session 137 vs 118.1 h on the dash hour meter (they count different conditions). Not a clock. | CONFIRMED |
| `0x35BF` | Time_to_Capture_Speed_1 | stopwatch, 0.01 s (3363 = 33.6 s after first drive) | CONFIRMED (name) |
| `0x3508` / `0x350F` | OEM countdown | 610 at rest, dips to 4..9 when moving off | TENTATIVE |
| `0x3332`, `0x3338`, `0x3339`, `0x3330`, `0x3336`, `0x3510` | free-running modulo counters | exclude from state analysis | CONFIRMED |

### 4.9 Other OEM-block monitors

| Object | Behaviour | Confidence |
|---|---|---|
| `0x35B7`, `0x35AA`, `0x3591`, `0x3590`, `0x35FC`, `0x35FF`, `0x35B8`, `0x35B9`, `0x35EA` | "headroom" cluster: 9192 at rest, drops with throttle (to ~2460–3670), ramps up from ~4656 over ~1 s at power-up, collapses to 200 in the power-up state. Reads as an available-current / torque-headroom limit. `0x35EA`'s ceiling is parameter `0x3826` (4973). | TENTATIVE |
| `0x33EF` | ±32k sweeps under motion, dithering in steps of 256 at rest; uncorrelated with rpm or current. Earlier "load current" and "rotor angle" hypotheses both dropped. | UNKNOWN |
| `0x33E8`, `0x33E9`, `0x33F1`, `0x361A`, `0x373E`, `0x361B`–`0x361F`, `0x35F5` | slow monotonic drifts over a session: temperatures or filtered analog inputs, units unknown | TENTATIVE |
| `0x330F` | the only OEM-block *parameter*, 39 [1..127], node-ID-like range | UNKNOWN |
| `0x3529`–`0x352E` | a coherent six-parameter set (111 / 44 / 1124 / 200 / 40 / 1500) | UNKNOWN |
| `0x38A6` | 70 [45..90], a temperature threshold in °C | TENTATIVE |
| `0x3858`, `0x3827`, `0x381E` | 4 [1..6], 1301 [178..2364], 1792 [150..8000]: motor-characterisation values | TENTATIVE |

### 4.10 Excluded / artifacts

- `0x35C6`, `0x350E`, `0x3554`, `0x3555`, `0x38CC`, `0x35C1`, `0x35EB`,
  `0x350A`, `0x360A`, `0x3285`, `0x324F`: zero-centred dither or AC samples.
  `0x35C6` only looked large when read unsigned.
- Idle-noise objects (`0x3334`, `0x3564`–`0x3569`, `0x3573`, `0x3588`,
  `0x3601`, …): compare only changes larger than their resting spread.
- Sub-index 5 of every object: constant 0.

## 5. Mechanisms

### 5.1 Range switch → speed cap (CONFIRMED)

```
range knob closes Sw_4 / Sw_5 / Sw_6           (pins 10 / 11 / 12)
  → OEM VCL reads the switch word 0x3226
  → looks up the static per-range table 0x3103–0x3108
  → writes Max_Speed_SpdM 0x3011 (+ mirrors 0x306E/0x3593 ramped, 0x3840, 0x33D1)
  → publishes packed state 0x33E6, the source of the J1939 data[7] nibbles
```

The table itself never changes with the knob; the VCL only selects from it.
The active cap latches on a direction change (forward → forward cap, reverse
→ reverse cap) and holds through neutral, so in a neutral-only sweep nothing
but `0x33E6` moves. After power-up `0x3011` = 1200 and `0x3213` = 0 until the
lever first leaves neutral.

### 5.2 Operator presence (seat) → power-down (CONFIRMED)

Leaving the seat does nothing for 7 s (the service manual's OPC timer). Then,
within 100 ms: `0x3226` bit 10 (Sw_11) drops, then all inputs; `0x3011` → 0;
`0x322B` → 4 → 68; `0x33E6` → 0; the controller stops answering and its J1939
stops. The OPC relay cuts the Curtis keyswitch. Sitting back down restarts the
controller in its power-up state (§5.1). The seat is therefore not the Curtis
interlock input; it acts through KSI power.

### 5.3 Interlock gates the drive path (CONFIRMED)

With `0x322B` bit 5 clear the controller ignores the pedal entirely:
`Throttle_Pot_Raw` sweeps its full range while `Throttle_Command` and
`Motor_RPM` stay 0, and no throttle-sequencing fault (HPD, code 47) can be
provoked. Any drive-related test must first confirm `0x322B` = 37.

### 5.4 Relationship to the J1939 broadcast

`FF21CA` bytes 0–1 = `0x33EA` = Current_RMS in amps; bytes 2–3 = `0x33FC` =
|rpm| + 3200; data[7] packed state derives from `0x33E6`. Motor and controller
temperatures are duplicated. Battery current/voltage, throttle position,
switch inputs, speed caps and all parameters exist only on CANopen.
`solecan_proto.py` already decodes bytes 0–1 as motor current
(`MOTOR_CURRENT_A_PER_BIT`).

## 6. Writing parameters

Source: Curtis E-series manual, CAN section. SDO downloads are accepted and
take effect in RAM at once. They are **volatile across a key cycle** unless
`0x332F CAN_EE_Writes_Enabled` is non-zero, in which case every subsequent
write is committed to EEPROM immediately. The manual cautions against leaving
`0x332F` set during normal operation (EEPROM wear). A RAM-only write is
therefore self-reverting and the safe way to experiment.

`sdo_write.py` is the only writer in this repository. Before a single write
frame goes out it requires: `0x332F` = 0, motor stopped (`0x3207` = 0), lever
in neutral (`0x33E6` direction bits 0), the target has sub 3/4 (is a
parameter) and the value is within its min..max, and `--apply` on the command
line. `--persist` wraps one write in `0x332F` := 1 … := 0. The firmware poller
is paused for the duration; expect to retry, as the controller drops contended
requests. Some parameters may raise fault 49/99 "Parameter Change Fault",
cleared by a key cycle.

**Deviations from stock on this tractor** (owner decision, 2026-09-24,
committed to EEPROM and verified after a key cycle):

| Object | Meaning | Stock | Now |
|---|---|---|---|
| `0x3106` | R1 reverse cap | 1600 rpm | 2000 rpm (= R1 forward) |
| `0x3108` | R2 reverse cap | 2000 rpm | 2500 rpm (= R2 forward) |

`0x3104` (R3 reverse, 2240) is unchanged. Undo is a `--persist` write of the
stock value. Any analysis of reverse behaviour after that date must account
for it. The stock dump is `canopen_full.txt`.

## 7. PDOs

**Curtis packs PDO mapping entries byte-reversed from CiA 301.** Decode a
mapping value `v` as `index = v & 0xFFFF`, `sub = (v >> 16) & 0xFF`,
`bits = (v >> 24) & 0xFF`. 16-bit values are mapped as two 8-bit halves
(sub 00 + sub 01).

| PDO | COB-ID (node 40) | Transmission | Contents |
|---|---|---|---|
| TPDO0 | `0x1A8` | event-driven (254), 64 bits | `0x33D3` (OEM), `0x3226` Switches, `0x3207` Motor_RPM, `0x3209` Current_RMS |
| TPDO1 | `0x2A8` | event-driven (254), 64 bits | `0x3204` Analog1, `0x322A` Ctrl_Temp, `0x320B` Motor_Temp, `0x3217` Pot2_Raw, `0x3215` Throttle_Pot_Raw |
| RPDO0 | `0x228` | 40 bits | `0x33D1` speed limit, `0x3218` throttle, `0x33D2` |
| RPDO1 | `0x328` | empty | |

RPDO0 is a designed CAN command path into the controller (speed limit and
throttle) that this tractor does not use; it is driven by the hard-wired
switch inputs instead.

**Do not NMT-Start this node.** PDO timeout monitoring is enabled
(`0x3149` = 25) while RPDO0 is never transmitted, so going Operational risks
fault code 72 (PDO Timeout) on the traction controller. Every TPDO object is
already SDO-readable; the only gain would be rate.

## 8. Tools and data files

All tools talk to the bus through the ESP32 firmware's USB SLCAN mode
(`switch_to_slcan` in `canopen_dump.py`) and auto-discover the node. Only
`sdo_write.py` transmits anything other than SDO upload requests.

| Script | Purpose |
|---|---|
| `canopen_dump.py` | Dump the whole dictionary (expedited SDO upload, one line per sub-index). Exports `discover_node`. |
| `canopen_snapshot.py` | Render one full sweep from a capture with names, scaling and confidence markers. |
| `canopen_timeseries.py` | Poll a fixed object set in a loop, log a time-series. |
| `canopen_capture.py` | Capture one labelled window of values to CSV. |
| `canopen_session.py` / `analyze_canopen_session.py` | Guided active capture through machine states, and its per-stage mover analysis. |
| `canopen_stages.py` / `analyze_stages.py` | Guided **passive** capture: listen to the firmware poller (`-DCANOPEN_POLL`, or `-DCANOPEN_FAST` for ~15 Hz on a short table) and tag replies by stage; then diff stages against baseline. |
| `analyze_asc.py` | Correlate SDO replies from an SD-card `can_NN.asc` against J1939 rpm/current (linear fits per object). |
| `fault_capture.py` | Log CANopen EMCY, J1939 DM1 and a fast state poll to one CSV while a fault is provoked. |
| `subindex_walk.py` | Split `canopen_full.txt` into parameters (sub 3/4 present) and monitors; write `canopen_params.csv`. |
| `sdo_write.py` | Guarded single-parameter SDO write (see §6). |
| `gen_canopen_table.py` | Emit `esp32-s3/src/canopen_objects.h` (the firmware poll table) from a dump. |

Data files (untracked, regenerate from captures):

| File | Contents |
|---|---|
| `canopen.txt`, `canopen_full.txt` | Dictionary dumps, sub 0 only / all sub-indices (stock configuration, R3, node 40) |
| `curtis_e_od_map.csv` | 308 indices → VCL name / display name / ranges parsed from the OS 30 manual |
| `canopen_named.csv` | all 1420 sub-0 objects joined to the manual names (OS 30 + OS 31) |
| `canopen_params.csv` | index, name, value, min, max for every parameter |
| `canopen_stages.csv`, `seat_10s.csv`, `limit.csv`, `pbrake.csv`, `fault.csv`, `s137.csv` | stage and session captures referenced in `NOTES.md` |

## 9. Open questions

- Whether a real fault raises EMCY on this node, and how EMCY codes map to
  J1939 DM1 codes. Retry `fault_capture.py` with the interlock closed
  (`0x322B` = 37), pedal pressed in neutral.
- Names for the ~1100 unnamed OEM-block objects. Public sources are exhausted
  at ~300 names; the remainder needs Curtis's EDS for OS 31.78 or Solectrac's
  VCL project. Behavioural decoding is the only route.
- Identity of `0x33EF` and the headroom cluster (`0x35B7` …).
- What the dashboard hour meter counts relative to `0x3160` (note both, drive,
  compare increments).
- Long-session and thermal behaviour of the slow-drift monitors; session 137
  showed no undiscovered fast-moving quantity.

## 10. Sources

1. Curtis Instruments, *1232E/34E/36E/38E & 1232SE/34SE/36SE Enhanced AC
   Controllers* manual, OS 30, p/n 53134. Every parameter and monitor
   variable is printed with its CAN index and sub-index, so the manual is the
   object dictionary. Parsed into `curtis_e_od_map.csv`.
2. Same manual, OS 31 revision, May 2017:
   `docs/Curtis_1232E-38E_manual_OS31_2017-05.pdf`. Adds the UserFault,
   Interlock_Type and encoder objects. Neither revision names the OEM block.
3. Victron Energy, `dbus-canopen-motordrive`, `src/drivers/curtis_e.c`
   (`readRoutine()`): the E-series objects and scalings Victron polls
   (`0x3207`, `0x359E`, `0x320B`, `0x322A`, `0x306C`, `0x324C`). All
   confirmed here. <https://github.com/victronenergy/dbus-canopen-motordrive>
4. acolomb, python-canopen wrapper for the Curtis 1232E (GitHub gist): source
   of the EMCY bit-to-code table (`0x1000` = Status1–5, `0x1001` = Status6–9,
   HPD = Status2 bit 0). Its EDS is not public; request from Curtis support.
5. CiA 301, CANopen application layer and communication profile: SDO
   expedited transfer, COB-ID conventions, NMT states, PDO mapping (which
   Curtis deviates from, §7).
6. Solectrac / Farmtrac FT25G service manual
   (`docs/FT_25G_Service_manual-10-08-2023.pdf`): OPC 7 s timer, cluster
   connector wiring (parking brake, PTO). Contains no Curtis parameter list.
7. This repository: `DOCUMENTATION.md` (J1939 decode, range caps, wiring),
   `solecan_proto.py`, and the captures listed in §8. Chronological findings
   and superseded hypotheses: [`NOTES.md`](NOTES.md).
