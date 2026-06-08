# Solectrac BMS Diagnostic Port

Reference for the diagnostic CAN port on the UDAN BMS shipped in the
Solectrac e25 tractor (India 72V 300Ah variant). Covers wire protocol,
session lifecycle, and the Data Identifier (DID) map. Reverse-engineering
provenance and unresolved investigations are in the Appendix.

Confidence markers used throughout: **CONFIRMED**, **TENTATIVE**, **UNKNOWN**.

---

## Pack identity

| Field                              | Value                                 |
|------------------------------------|---------------------------------------|
| Variant                            | India 72V 300Ah, original (`印度系列72V300Ah原版`) |
| UI / firmware project              | `C121.082.001.01`                     |
| Hardware build string (DID 0xA50F) | `A650_C121.074.001.01_T1.0.2`         |
| Firmware version (DID 0xF195)      | `3.0.4.4`                             |
| BMS family                         | UDS-capable (P700 / U600 / X700 class) |
| MCU (per UDAN symbol table)        | NXP S32K142 / S32K314 (ARM Cortex-M)  |
| Flash                              | GD25Q64 (SPI NOR, 64 Mbit) + W25N01G (NAND, 1 Gbit) |

## Pack structure (CONFIRMED)

- Chemistry: NCM
- Configuration: 20S × 1 subsystem
- Rated capacity: 300 Ah; rated current: 500 A
- Nominal pack voltage: 72 V (≈ 78.5 V at high SOC)
- Temperature probes: 7 per subsystem
- HV rails: B+, HV1 (Main+), HV2, HV3 active; HV4 / HV5 not used
- Contactors: HSS1 (Main+), HSS2–HSS5, LSS1 (only HSS1 closed when idle)
- "Calibrating" Running mode — TENTATIVE: this BMS's normal idle state,
  not a special diagnostic mode

---

## Wire protocol

### CAN parameters

| Direction               | 11-bit ID | Notes                                |
|-------------------------|-----------|--------------------------------------|
| Tester → BMS (UDS req)  | `0x740`   | Only request ID this BMS responds to |
| BMS → Tester (UDS resp) | `0x748`   |                                      |

ISO-TP (ISO 15765-2) over CAN, 11-bit standard IDs. Bitrate: not measured
directly, but the bus is shared with the OBD-II side at 250 kbit/s
(see `DOCUMENTATION.md`).

### UDS services in use

| SID    | Service                 | Observed use                                  |
|--------|-------------------------|-----------------------------------------------|
| `0x10` | DiagnosticSessionControl | Enter extended session (`10 03`) before unlock |
| `0x22` | ReadDataByIdentifier    | All live-data and identity reads              |
| `0x27` | SecurityAccess          | Level 1 unlock (`27 01` seed, `27 02` key)    |
| `0x31` | RoutineControl          | Six `0xF009`–`0xF011` routines, trigger UNKNOWN |
| `0x34` / `0x36` / `0x37` | RequestDownload / TransferData / TransferExit | Bootstrap 1528 B write to `0x00003A00`, purpose UNKNOWN |

DID notation: `0xXXXX` everywhere. Wire format is the standard UDS form:

```
Request:  03 22 02 09          PCI=3, SID=22, DID=0x0209
Response: 05 62 02 09 00 00    PCI=5, SID|0x40=62, DID echoed, data
```

Responses longer than 7 bytes use ISO-TP first/consecutive framing.

### Session lifecycle

Default session is read-only for live data. Extended session + SecAccess L1
are required before any write or routine call is honored.

```
1.  02 10 03                                 → 06 50 03 00 32 00 C8 00    DSC: enter extended session (P2 = 50 ms, P2* = 200 ms)
2.  02 27 01                                 → 06 67 01 <4-byte seed>     SecAccess L1: request seed
3.  06 27 02 <4-byte key>                    → 02 67 02                   SecAccess L1: send key
```

### Connection bootstrap

The iBMS PC tool performs a fixed 11-step sequence on every connection
(elapsed ~0.5 s, before any UI polling starts):

| Step | Request                              | Purpose                                       |
|------|--------------------------------------|-----------------------------------------------|
| 1    | `22 A5 00`                           | Discovery probe (1-byte response, `01`)       |
| 2    | `22 01 06`                           | UNKNOWN (2-byte response)                     |
| 3    | `22 F1 95`                           | Firmware version (one-shot only)              |
| 4    | `22 A5 0F`                           | Hardware/build string                         |
| 5    | `22 28 00`                           | System state snapshot                         |
| 6    | `10 03`                              | Enter extended session                        |
| 7    | `27 01` / `27 02`                    | SecAccess L1 unlock                           |
| 8    | `34 00 24 00 00 3A 00 05 F8`         | RequestDownload: 1528 B to `0x00003A00`       |
| 9    | `36 01` / `36 02` / `36 03`          | TransferData (3 × ~516 B)                     |
| 10   | `37`                                 | TransferExit                                  |
| 11   | `22 A5 03`, `22 A5 05`, `22 A5 0D`   | Additional identity / status reads            |

Steps 8–10 are the open mystery — see "Bootstrap RequestDownload" in the reverse-engineering notes appendix.

---

## Data Identifiers

Organized by data category. UDAN message IDs (`0xNN`) refer to the
iBMS-internal symbol table that names CSV exports; see "Message-ID symbol
table" in the iBMS-software-notes appendix.

### Identity (one-shot, or polled in baseline)

| DID    | Type             | Sample value                              | Confidence |
|--------|------------------|-------------------------------------------|------------|
| `0xF195` | ASCII string   | `"3.0.4.4"` (FW version)                  | CONFIRMED  |
| `0xA50F` | ASCII string   | `"A650_C121.074.001.01_T1.0.2"`           | CONFIRMED  |
| `0xA500` | 1-byte flag    | `0x01` (discovery / liveness)             | TENTATIVE  |
| `0xA503`, `0xA505`, `0xA50D` | varying (4–19 B) | Identity / status block | UNKNOWN |

### Per-cell live data

| DID    | UDAN tag           | Format                                       | Confidence |
|--------|--------------------|----------------------------------------------|------------|
| `0x0101` | `0x08` Voltages  | 20 × BE u16, mV                              | CONFIRMED  |
| `0x0102` | `0x09` Temperatures | 7 × u8, `°C = raw − 40` (offset TENTATIVE) | CONFIRMED  |

Sample `0x0101` payload (76.8 % SOC, idle):
`3925 3925 3926 3926 3925 3924 3928 3927 3925 3925 3925 3923 3928 3926 3929 3929 3925 3927 3930 3929` mV.

Sample `0x0102` payload: `41 41 41 41 40 41 41` → ~22 °C across 7 probes.

### Pack-level state — DID `0x2800` (UDAN `0x93`)

12 data bytes.

| Offset | Type    | Field                                | Sample          |
|--------|---------|--------------------------------------|-----------------|
| 0..1   | BE u16  | Real SOC × 10 (%)                    | `0x0312` = 78.6 % |
| 2..3   | BE u16  | SOH × 10 (%)                         | `0x03E8` = 100.0 % |
| 4..5   | BE u16  | HV1 / Pack voltage × 10 (V)          | `0x0311` = 78.5 V |
| 6..7   | BE i16  | Signed pack current × 10 (A). **Positive = charging into pack, negative = discharging** — opposite sign convention from J1939 `F100F3` data[2..3] | `0xFFED` = −1.9 A (idle standby draw); +194 (`0x00C2`) during 120 V charging |
| 8      | u8      | `0x00` padding                       | constant        |
| 9      | u8      | Cell-mV spread (max − min)           | 4–15 mV; r ≈ 0.83 vs `0x2820`/`0x2828`; exact match ≈ 76 % of the time (small skew from staggered polls) |
| 10     | u8      | Pack-current state code — TENTATIVE. Three observed values: **50 = discharging (drive)**, **51 = idle / brief transient**, **52 = actively charging**. Drive captures are dominated by 50 with 51 between current bursts; a multi-hour L1 charge is dominated by 52 (~87 %), 51 the remainder. Mirrored 99.9 % by `0x2810` byte 9. Also rules out max-temp raw (r = 0.0 vs `0x2830` top-1) | charging → mostly 52; discharge → mostly 50; idle → mostly 51 |
| 11     | u8      | `0x06` constant (struct version tag) | constant        |

### Peak data — DID cluster `0x2820`/`0x2828`/`0x2830`/`0x2838` (UDAN `0x06`)

Each DID carries the top-4 extremes for one quantity. The iBMS UI and CSV
export only the #1 entry per column; the BMS internally tracks four.

| DID      | Tuple format                                                       | Sorted | Quantity                |
|----------|--------------------------------------------------------------------|--------|-------------------------|
| `0x2820` | 4 × (u16 BE voltage_mV, u8 subsys_0based, u8 cell_idx_0based)      | DESC   | Top-4 **max** cell V    |
| `0x2828` | same                                                               | ASC    | Top-4 **min** cell V    |
| `0x2830` | 4 × (u8 temp_raw, u8 subsys_0based, u8 probe_idx_0based)           | DESC   | Top-4 **max** probe T   |
| `0x2838` | same                                                               | ASC    | Top-4 **min** probe T   |

Cross-checks against `0x0101`: top-1 entries match the cell-array max
(3930 mV at cell 18) and min (3923 mV at cell 11) exactly. Subsys byte is
0-based internally (CSV reports 1-based).

### Counters

#### DID `0x2801` — (Dis)charged time (UDAN `0x95`)

16 data bytes = 4 × BE uint32, all in seconds:

| Offset | Sample      | Field                                                      |
|--------|-------------|------------------------------------------------------------|
| 0      | 832,857,443 | TENTATIVE: lifetime counter (ms or epoch-like); ticks 1/s  |
| 4      | 1,329       | Session uptime; zero at boot, ticks 1/s                    |
| 8      | 3,873,795   | Accumulated charge time (1076 h)                           |
| 12     | 576,772     | Accumulated discharge / usage time (160 h)                 |

**Heartbeat byte:** byte 3 of the payload (low byte of the offset-0 u32)
increments by 1 every ~1 s — this is the byte exported as the `Heartbeat`
column in the iBMS System-state CSV.

#### DID `0x2810` — (Dis)charged energy (UDAN `0x89`)

20 data bytes:

| Offset | Type    | Field                                                 |
|--------|---------|-------------------------------------------------------|
| 0..1   | BE u16  | Cell count (= 20)                                     |
| 2..3   | BE u16  | Cycle count (= 7)                                     |
| 4..5   | BE u16  | Average cell voltage (mV) — r ≈ 1.000 vs mean(`0x0101`); mean error 0.3 mV |
| 6      | u8      | Average cell temperature (°C = raw − 50) — matches round(mean(`0x0102`)) ≥99 % |
| 7      | u8      | `0x00` padding                                        |
| 8      | u8      | Cell-mV spread (mirrors `0x2800` byte 9)              |
| 9      | u8      | Pack-current state code (mirrors `0x2800` byte 10; 99.9 % match): **50 = discharging, 51 = idle/transient, 52 = active charging** — TENTATIVE |
| 10     | u8      | `0x00` padding                                        |
| 11     | u8      | `0x1E` constant (struct version tag)                  |
| 12..15 | BE u32  | Accumulated charge capacity, **raw u32 = lifetime Ah** (one LSB per Ah delivered) — CONFIRMED. Cross-checked across a 13.9 h L1 charge (10 → 99 % SOC): integrating signed pack current from `0x2800` yields 269 Ah delivered; counter advanced raw 7763 → 8032 (+269). Match at × 1 Ah scale is <1 %; the documented × 0.01 Ah scale would have moved ~26,900 LSBs. The wire format may nominally be × 0.01 Ah resolution but the firmware quantizes to whole-Ah steps. |
| 16..19 | BE u32  | Accumulated discharge capacity, same scale convention as bytes 12..15 — TENTATIVE on the discharge side only (cross-check on the charge side is solid; same field on the discharge counter held flat at 8082 Ah lifetime during the charge session, so a long drive capture is still needed to independently verify the discharge scale). |

### `0x4000` — active-session status (UDAN `0x87` mapping QUESTIONED)

31 data bytes. The original interpretation (31 severity-level enums, one
per fault category) **does not fit the wire data**: active-charge dual
captures show several bytes carrying numeric measurements and counters, not
severity enums. This was first visible in
`data/dual-capture/dual-capture-charging-120.asc` (~215 s of L1/120 V
charging at ~19 A), then repeated across 30,235 diagnostic polls in
`dual-capture-charging-120-10p-to-100p.asc` (9.6% to 99.2% SOC).

Observed structure (144 polls in the short dual capture, no broadcast fault
on F108F3, charger delivering ~19 A throughout; same byte roles in the long
dual capture):

| Byte    | Idle (charger off) | Active (charging) | Interpretation                          |
|---------|--------------------|-------------------|-----------------------------------------|
| 0       | `00`               | `02`              | Active-alarm count? (matches CSV "Alarm number" col) |
| 1       | `00`               | `03`              | Status bit — set when charging engages   |
| 2       | `00`               | `01`              | Static `01` once charger present         |
| 3–5     | `00 00 00`         | `01 01 02`        | Status bits — set when charging engages  |
| 6..7    | `00 00`            | `EE..F1, 00`      | **Pack-V LE u16, low byte first** — mirrors FF50 `data[1..2]`. With FF50 status (= mirror byte 5 here) selecting the encoding offset, this is pack terminal V = raw × 0.1 + offset (offset 51.2 V for status 0x02, 76.8 V for 0x03). |
| 8..9    | `00 00`            | `C7, 00`          | **DC charge current LE u16, low byte first** — mirrors FF50 `data[3..4]`; raw × 0.1 A/bit (19.9 A at `C7 00`). |
| 10      | `00..D7`           | `00..D7`          | **1-Hz tick counter** (rolls 0x00..0xFF) |
| 11      | `FF` → `53..56`    | `53..56`          | Slowly-varying u8 (semantics UNKNOWN; possibly time-remaining or temp) |
| 12      | `FF`               | `00..FF` (varies) | UNKNOWN dynamic u8 (124 unique values in 144 polls) |
| 13–20   | `00`               | `00`              | Reserved / zero                          |
| 21      | `FF`               | `00`              | Status — `FF` when no charger, `00` during charge |
| 22–23   | `00`               | `00`              | Reserved                                 |
| 24      | `FF`               | `00`              | Status — same flip as byte 21            |
| 25      | `FF`               | `FC`              | Status — `FF` no charger, `FC` charging  |
| 26–30   | `FF`               | `FF`              | True sentinels (constant)                |

**Bytes 5–9 mirror FF50 `data[0..4]`** — CONFIRMED by simultaneous
diagnostic-port (0x4000) and broadcast (FF50E5) frame capture. The
status byte (mirror byte 5) selects the pack-V encoding offset for the
u16 LE at mirror bytes 6..7 (same low/high variant scheme as F100F3
byte 0); see DOCUMENTATION.md §FF50E5 for the full table.

**Implication for the UDAN `0x87 = DID 0x4000` mapping.** Byte 0 plausibly
mirrors the CSV "Alarm number" column (count of active alarms), and the
historical on-pack alarm CSV showing `ChgOV` + `ChgPackOV` as persistent
Lvl 1 alarms is consistent with byte 0 = `0x02` in our capture (= 2
alarms still active). But the rest of the 31-byte block is dominated by
charging telemetry, not severity codes. Two possibilities remain open:

  - **Mixed-purpose DID.** `0x4000` carries both an alarm header (byte 0
    = active-alarm count, maybe a few flag bytes) *and* a charging-status
    block — and the UDAN `0x87` "Alarm state" CSV is sourced from byte 0
    alone (or from a different DID merged with it).
  - **Mis-attributed DID.** UDAN `0x87` actually maps to a DID we haven't
    polled, and `0x4000` is the (mis-labeled in our notes) "ChgState"
    block that UDAN tag `0x99` ("Charging state / ChgState — UNKNOWN")
    refers to.

Still TENTATIVE. The 73-column Alarm CSV's per-byte-to-column mapping
remains unresolved — a capture with a *new* fault firing (i.e. byte 0
incrementing past `0x02`) is needed to pin it down. The historical
on-pack CSV only ever shows the same two alarms across all 240 rows, so
its column ordering alone cannot anchor the byte order.

### Charging — DID cluster `0x0900` + `0x0901` + `0x0902` (UDAN `0x94`)

Three DIDs polled in parallel during the Charge-info / BMS tab. Combined
35 data bytes covers the 16 non-time CSV columns of `Charging 0x94.csv`
(Charger conn., elapsed time, req V/A, output V/A, fault stat., S2 state,
CC/CC2 resistance, CP freq./duty, lock state, 3× port temp).

| DID      | Data (B) | Idle sample                                       | Active-charge behavior                           |
|----------|----------|---------------------------------------------------|--------------------------------------------------|
| `0x0900` | 7        | `01 00 01 00 00 00 00`                            | **Invariant** across multi-hour active charging — same value as charger-disconnected baseline. Byte 0 = `0x01` = "AC Chg" enum (CSV "Charger conn." column) — CONFIRMED. Remaining bytes are TENTATIVELY the categorical enums that stay at their pack-default values on this BMS variant (Charger fault stat. = "Status", S2 state = "Open", Elec. lock state = "Unlocked"). |
| `0x0901` | 14       | `33 0c 00 00 33 0c 00 00 00 00 ff ff ff ff`       | Bytes 0..1 BE and 4..5 BE are **paired dynamic u16 measurements** in a narrow 13100..13250 raw range across a full SOC sweep — they don't track pack V monotonically and the per-bit scale doesn't land on a clean V at observed pack voltages, so the CSV's "Charge Req. Volt." / "Charger Output Volt." mapping is still TENTATIVE. A clean × 0.01 V scale would put them at 131..132 V (close to L1 mains nominal). Byte 7 toggles `00 ↔ 0B` irregularly throughout charging (uncorrelated with status changes; likely a heartbeat — UNKNOWN). Trailing 4 bytes (10..13) are **`FF FF FF FF` = CC Resistance + CC2 Resistance "Invalid" sentinels** (CSV value 65535) — CONFIRMED. |
| `0x0902` | 14       | `00 00 00 00 00 00 00 00 00 00 00 00 00 00`       | **Invariant all-zero** across multi-hour active charging — CONFIRMED. Consistent with the CSV showing 0 / "Invalid" for CP freq., CP duty, and the three Charger-port-temp columns on this pack variant. Unused fields padded to zero. |

### X700 IoT subsystem — DIDs `0xA501`, `0xA502`, `0xA506`, `0xA507`, `0xA50E`

The BMS contains a built-in cellular telemetry subsystem ("X700"). Visible
in the iBMS UI but unprovisioned on the shipped Solectrac unit. Schema
exposed (all fields empty in observed unit):

```
HWID, FWVersion, HWVersion, DeviceName, Host, Port,
APN UserName, APN Password, MQTT UserName, MQTT Password
```

UDAN message IDs `0x98` (WiFi info) and `0x9D` (WiFi / DTU) likely cover
this data; explicit DID-to-field mapping UNKNOWN.

### Calibration tables — `0x30xx` / `0x40xx` (~80 DIDs)

Triggered by the SOC tab "Read" button: one-shot dump of ~80 DIDs in the
`0x3010`, `0x3030`–`0x3093`, `0x30A0`–`0x30E6`, `0x3140`–`0x3153`,
`0x4011`/`0x4012`, `0x4019`/`0x401A` ranges (plus `0x0E11`, `0x0E61`).
Almost all responses are 35 B (32 B data after the `62 XX XX` header).

Populate the iBMS Cap.config / SOC calib.config / HighSoc / LowSoc threshold
tables. Numerically-paired DIDs (e.g. `0x3030`/`0x3031`) TENTATIVE:
charge-vs-discharge or high-vs-low of the same parameter.

### `0x28xx` address-space map

The `0x28xx` range is segmented by purpose, not a single state block:

| DID                | Content                                          |
|--------------------|--------------------------------------------------|
| `0x2800`           | System state                                     |
| `0x2801`           | (Dis)charged time                                |
| `0x2810`           | (Dis)charged energy                              |
| `0x2820` / `0x2828`| Peak data — max-V / min-V                        |
| `0x2830` / `0x2838`| Peak data — max-T / min-T                        |
| `0x2832` / `0x283A`| Empty on this pack — TENTATIVE: subsystem-2 slots |
| `0x2850`           | UNKNOWN 2-byte block                             |
| `0x2803`, `0x2804` | Cell-level extremum / index (Cell info tab)      |

### Pack topology — DIDs `0x0202` and `0x0205` (CONFIRMED static)

Two one-shot lookup tables that describe the BMS-internal channel layout
for this pack. Both have been static across every capture, so they are
read once at connection and stored in `BmsState.cell_index_map` and
`BmsState.probe_channel_map`.

| DID    | Bytes | Sample value                                              | Meaning                                  |
|--------|-------|-----------------------------------------------------------|------------------------------------------|
| `0x0202` | 20  | `00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 14 15 16 17` | Logical → physical cell index map        |
| `0x0205` | 7   | `00 01 02 04 05 06 07`                                    | Logical → physical NTC channel map       |

Two findings:

- `0x0202` shows the BMS uses a **24-channel cell-monitoring chip** on a
  20-series pack: physical slots 0..15 are used contiguously, then 20..23;
  physical slots 16..19 are skipped.
- `0x0205` shows the **NTC probe map skips channel 3** — probes wired to
  physical channels 0, 1, 2, 4, 5, 6, 7 only. Aligned with the 7-populated
  layout in `0x1620` and J1939 `F155F3..F15EF3`.

### BMU on-board telemetry — DIDs `0x0E00`, `0x0E40`, `0x1600`, `0x1620` (CONFIRMED layouts)

The BMS-tab DIDs that surface internal sensor state. All four have explicit
byte layouts now:

#### `0x0E00` — HV detection (12 data bytes)

| Offset | Type   | Field                                | Notes |
|--------|--------|--------------------------------------|-------|
| 0..1   | u16 BE | HV1 pack voltage × 10 (V)            | Matches F100F3 data[1] decode within 0.1 V across the dual-bus capture |
| 2..3   | u16 BE | HV1 pack voltage × 10 (V) — duplicate | Always equal to bytes 0..1 in observed captures |
| 4..7   | bytes  | State flags (UNKNOWN)                | Idle: `00 01 00 01` |
| 8..11  | u32    | Sentinel `0xFFFFFFFF`                | "Not present" |

#### `0x0E40` — Hall current sensing (7 data bytes)

| Offset | Type   | Field                                | Notes |
|--------|--------|--------------------------------------|-------|
| 0..1   | i16 BE | Hall current × 10 (A)                | Same sign convention as 0x2800 pack_a: positive = charging into pack, negative = discharge. In the 14-hour charge capture, 30,234 paired 0x0E40/0x2800 samples correlate at r=0.9991 and Hall reads ~+1.4 A above 0x2800. Earlier driving captures show larger transient residuals. |
| 2..6   | bytes  | Constant `FD FF F4 00 02`            | Likely calibration / status — UNKNOWN |

The Hall current and 0x2800 shunt current use the same sign convention but
are not interchangeable: they agree at steady-state and through the long
charge, but track different waveforms during rapid driving current changes.
Use 0x2800 as the authoritative pack current; treat 0x0E40 as a cross-check
/ sensor-health indicator.

#### `0x1600` — BMU power-supply rail (22 data bytes)

| Offset | Type   | Field                                | Notes |
|--------|--------|--------------------------------------|-------|
| 0..1   | u16 BE | BMU 12 V rail × 1000 (V)             | Observed 12.66 – 12.79 V across captures |
| 2..21  | bytes  | All `0x00` on this pack              | Likely other rails not populated |

#### `0x1620` — BMU on-board NTC temps (7 data bytes)

| Offset | Type  | Field                                | Notes |
|--------|-------|--------------------------------------|-------|
| 0      | u8    | `BoardTempNum` — count of valid temps | `0x00` on this pack |
| 1..6   | u8 × N | First `N` bytes are temps; °C = raw − 50 | Bytes beyond the count are firmware-default stubs and must be ignored |

`BoardTempNum` is always `0` on this pack — the BMS-side board-temperature
feature is not configured on the India 72V 300Ah Solectrac variant, and
the iBMS PC tool reflects this by exporting no `BoardTemp` CSV
(inventory under "Historical CSV exports" in the appendix lists 8 files,
none of them BoardTemp). The trailing bytes carry a constant stub
pattern (`47 47 00 00 45 00` across every observed capture); decoding
those as live temps gives plausible-looking but spurious 17–21 °C
readings even when ambient is 15 °C. The decoder gates on byte 0 and
returns an empty list when count is zero.

### Battery config — DID `0x0100` (CONFIRMED layout, partial field names)

35-byte static payload, read once per session. Defines the pack's nameplate
ratings and topology. Sample from `dual-capture-charging-120-10p-to-100p.asc`:

```
06 0B B8 13 88 02 D0 05 DC 01 A0 00 14 00 14 00 07
01 F4 03 E8 00 00 00 00 10 CC 0A 8C 00 71 00 00 13 88
```

Dashboard currently decodes only the first 21 bytes:

| Offset | Type    | Value (this pack) | Field                                        | Status |
|--------|---------|-------------------|----------------------------------------------|--------|
| 0      | u8      | `0x06`            | Chemistry enum (LFP candidate)                | TENT   |
| 1..2   | BE u16  | 3000 → 300.0 Ah   | Rated capacity (× 0.1 Ah)                     | CONFIRMED |
| 3..4   | BE u16  | 5000 → 500.0 A    | Rated current (× 0.1 A)                       | TENT   |
| 5..6   | BE u16  | 720 → 72.0 V      | Rated voltage (× 0.1 V) — 20 cells × 3.6 V nominal | CONFIRMED |
| 7..8   | BE u16  | 1500 → 150.0      | Field@7 (×0.1)                                | TENT   |
| 9..10  | BE u16  | 416 → 41.6        | Field@9 (×0.1)                                | TENT   |
| 11..12 | BE u16  | 20                | Series cell count — matches `0x0101` length / 2 | CONFIRMED |
| 13..14 | BE u16  | 20                | Field@13 — value equals series count, **does not fit "parallel strings"** (capacity math implies parallel = 1; see below) | TENT |
| 15..16 | BE u16  | 7                 | NTC probe count — matches `0x0102` length and `0x0205` map | CONFIRMED |
| 17..18 | BE u16  | 500 → 50.0        | Field@17 (×0.1)                               | TENT   |
| 19..20 | BE u16  | 1000 → 100.0 %    | Initial SOC (× 0.1 %) candidate                | TENT   |
| 21..34 | 14 B    | (see hex above)   | Unmapped tail; currently ignored by dashboard  | UNKNOWN |

Capacity cross-check: a 13.9 h L1 charge delivered 269 Ah for a 10.2 → 99.0 %
SOC sweep (88.8 %), implying pack capacity ≈ 303 Ah. That matches 300 Ah rated
× 1 parallel within 1 %, so the pack is **20S1P** with ~300 Ah cells.
Consequently the field at bytes 13..14 (reading 20) cannot be a parallel-string
count — it duplicates the series-count value and the actual field meaning is
unknown.

### DIDs observed but not yet identified

These are polled by the iBMS but not yet mapped to a known UDAN message:

| Range                                                  | Notes |
|--------------------------------------------------------|-------|
| `0x0103`–`0x0105`                                      | Mostly empty; `0x0100` decoded above |
| `0x0200`, `0x0201`, `0x0203`, `0x0204`, `0x0206`–`0x020B` | Mostly empty; `0x0202` and `0x0205` decoded above |
| `0x0620`, `0x0621`, `0x0648`                           | Mostly-empty sub-block, UNKNOWN |
| `0x0641`–`0x0647`                                      | Per-channel 1-byte values (7 total), UNKNOWN |
| `0x0E21`                                               | UNKNOWN small value |
| `0x0F50`                                               | 1-byte response — **leading `WakeupSignal` candidate** (BatteryPackMessage protobuf field 3, varint enum with sources KL15 / OBC / RTC). KL15-woken captures show `0x01`; the 14-hour OBC charge capture is dominated by `0x02` during active charging, with short `0x03` transients. |
| `0x0F60`, `0x0F10`                                     | 3-byte constants (`07 00 80` and `00 01 00` respectively) in KL15 captures; sit next to `0x0F50` in the "On-board volt" sub-tab — possibly KL15/OBC/RTC rail voltages or wake-related status. |
| `0x0F30`                                               | 4 bytes, toggles between `00 00 00 00` and `00 0B 00 00` with byte 1 flipping briefly to `0x0B` — signal-detection event flag. |
| `0x0E70`–`0x0E72`, `0x0EF0`                            | Signal detection / on-board rails — all-zero in observed captures |
| `0x0EA0`, `0x0EA1`                                     | Cell info tab — balancing. Both all-`0xFF` in observed captures (no balancing active; cell delta < 11 mV) |
| `0x0ED0`–`0x0ED7`                                      | Open-wire / cell-monitor flags. Of 8 DIDs only `0x0ED0`, `0x0ED1`, `0x0ED2`, `0x0ED5` respond on this pack; `0x0ED3`/`0x0ED4`/`0x0ED6`/`0x0ED7` return no response. `0x0ED0`/`0x0ED1` 4-byte payloads: bytes 0..1 `FF FF` (no-fault sentinel mask), bytes 2..3 BE u16 oscillating in a narrow 4982–4998 range, with 30,234 normal `FFFF13xx` responses in the 14-hour charge capture (purpose UNKNOWN — likely a per-DID ADC self-check reading; not a counter, not monotonic). `0x0ED2` and `0x0ED5` are all-zero on this pack |
| `0x0960`, `0x0961`, `0x0905`, `0x0962`                 | UNKNOWN |

---

## Cross-reference: DIDs mirrored on the vehicle J1939 bus

Several pack-state DIDs the BMS publishes over the diagnostic port are
also broadcast on the main vehicle CAN (OBD2 port) as J1939 PGNs from
source-address 0xF3 (the BMS's own SA on the broadcast bus). See
`DOCUMENTATION.md` "BMS (SA 0xF3)" for the full PGN decodes.

Confirmed mirrors (correlation r ≈ 1.0 between the diagnostic-port DID
value and the broadcast-frame byte across a time-synced dual-bus
capture):

| BMS-internal DID (diag port)               | Broadcast PGN (vehicle bus)          | Quantity                                  |
|--------------------------------------------|--------------------------------------|-------------------------------------------|
| `0x2800` bytes 0..1 BE (SOC × 10)          | `F100F3` data[4]                     | SOC                                       |
| `0x2800` bytes 4..5 BE (HV1 / pack V × 10) | `F100F3` data[1]                     | Pack terminal voltage                     |
| `0x2800` bytes 6..7 BE (pack current)      | `F100F3` data[2..3], `FF2112` data[0]| Signed pack current (motor ctlr mirrors)  |
| `0x0101` (20 × cell mV, BE u16)            | `F113F3`..`F13CF3` (per-cell PGNs)   | Per-cell voltages                         |
| `0x0102` (7 × probe T)                     | `F155F3`..`F15EF3` (per-probe PGNs)  | Module temperatures                       |
| `0x2820` top-1 max-cell tuple              | `F102F3` data[1..2] BE + data[5]     | Max cell mV + 1-based cell number         |
| `0x2828` top-1 min-cell tuple              | `F102F3` data[3..4] BE + data[6]     | Min cell mV + 1-based cell number         |
| `0x2830` top-1 max-temp tuple              | `F104F3` data[0] + data[2]           | Max module °C + 1-based probe number      |
| `0x2838` top-1 min-temp tuple              | `F104F3` data[1] + data[3]           | Min module °C + 1-based probe number      |
| `0x4000` byte 0 (active-alarm count, TENTATIVE) | `F108F3` (active fault bitmap)  | `F108F3` is the authoritative broadcast bitmap; `0x4000`'s relationship to alarms is now questioned — see the `0x4000` section above. |
| `0x4000` bytes 5..9 (charger telemetry block) | `FF50E5` `data[0..4]`         | Variant-tagged pack V (LE u16, status-selected offset) + DC current (LE u16 × 0.1 A/bit) |

The diagnostic-port DIDs expose the **full** BMS internal state (cell
extrema as top-4 sorted tuples, calibration tables, identity blocks,
balancing/open-wire flags, etc.), while the J1939 broadcast surface
only re-publishes the summary signals listed above. Anything in the
"DIDs observed but not yet identified" section or the `0x30xx`/`0x40xx`
calibration cluster has **no known broadcast equivalent**.

Methodology: `util/capture_dual_can.py` records both buses to one
ASC with a shared host clock; `util/correlate_did.py` walks every DID
response and ranks per-byte Pearson correlation against every OBD2
broadcast byte. The mirrors above all hit r ≈ 1.0; lower-confidence
correlations are easily contaminated by mostly-static bytes in idle
captures and are not listed.

---

## Polling patterns

| Phase                         | Frequency | DIDs                                                                  |
|-------------------------------|-----------|-----------------------------------------------------------------------|
| Bootstrap (~0.5 s)            | one-shot  | See §"Connection bootstrap" steps 1–11                                |
| Baseline (continuous)         | ~1 Hz     | ~30 DIDs covering identity + per-cell + pack state + peak data + counters + alarms |
| Cell info tab (additive)      | ~1 Hz     | `0x0EAx`, `0x0EDx`, `0x2803`/`0x2804`, `0x096x`                       |
| BMS tab (additive)            | ~1 Hz     | `0x0900`–`0x0902`, `0x0E00`/`0x0E40`/`0x0E7x`/`0x0Exx`/`0x0Fxx`, `0x1600`/`0x1620`, `0xA50x` (X700), `0x064x` |
| SOC tab Read                  | one-shot  | ~80 calibration DIDs in `0x30xx` / `0x40xx`                           |
| Late-session routine burst    | ~1 Hz, transient | `31 01 F0 09`–`31 01 F0 11`, plus `0x0905`/`0x0962`/`0x064E`/`0x067x` — trigger UNKNOWN |

The session is kept open by continuous baseline polling; explicit `0x3E`
TesterPresent is referenced in the iBMS binary's symbol table but not
observed on the wire during baseline polling.

---

## Writes

Not yet observed on the wire. The iBMS UI exposes Sync / Import / Export /
Read / Write buttons on the SOC calibration tab; **Write** is gated by
SecAccess L1 unlock. Specific write services are TENTATIVE pending an
observed write transaction.

The internal protobuf message types in `app/iBMSUpper.exe`
(`MosForceControlMessage`, `WorkModeControlMessage`,
`ChgForceControl`/`Time`, `U600ElecLockForceContrl`, `U600HLSSForceContrl`)
indicate write/control surfaces are available; see "Protobuf message types"
in the iBMS-software-notes appendix.

---

# Appendix

## iBMS software notes

Facts about the UDAN iBMS PC Utility itself: what's in the binary, what
adapters it supports, what its UI looks like, what it does on connect,
and what file formats it produces. Reference material, not active
investigation.

### Working with the binary

Recipe to unpack from the installer (re-runnable; outputs are
`.gitignore`-able):

```sh
mkdir -p extract && innoextract -d extract 'docs/iBMSUpper-setup-x86(v3.1.7).exe'
cp extract/app/iBMSUpper.exe extract/iBMSUpper.unpacked.exe
upx -d extract/iBMSUpper.unpacked.exe         # 29 MB packed → 37 MB unpacked
```

The unpacked binary is a Windows PE32 (x86, 32-bit) Go 1.15.15 build
with cgo. PE-side symbols are stripped, but Go's pclntab is intact at
file offset `0x2014000` (15578 function entries). Recovery tool:

```sh
python3 util/parse_pclntab.py extract/iBMSUpper.unpacked.exe --filter <regex>
```

`r2 -A` is unusable on this binary (silent on stdout for unclear
reasons in r2 6.1.4). The working pattern is:
1. Locate function address via `parse_pclntab.py` (fast, no analysis).
2. Disassemble directly with `r2 -2 -q -c 'e scr.color=0; s <addr>; pd N' file`.

### Architecture: Go + cgo C functions

The seed/key, both CRCs, and at least some I/O helpers are **compiled
C functions called from Go via cgo**. Naming convention: a Go function
named `_Cfunc_Foo` (e.g. `main._Cfunc_CalculateKey`, `main._Cfunc_Crc_CalculateCRC16`)
is a thin marshalling stub; the real implementation is a C function the
stub dispatches to via `runtime.asmcgocall` with a pointer to an
args/return struct on the Go stack. To find the C function: disassemble
the `_Cfunc_*` Go function, look for `mov eax, [<fixed addr>]` followed
by `call <asmcgocall-helper>` — the fixed address holds a pointer to a
C-side trampoline, which in turn calls the real C function. The
trampoline uses the cgo `_cgo_topofstack` pattern: `call topofstack;
mov edi, eax; ... do work ...; call topofstack; sub eax, edi; mov
[args + eax + N], result` (handles Go stack moves during the cgo call).

### Function-address map (for future reversing)

Targets identified but not yet reversed (Go symbols + PE virtual
addresses, recovered from pclntab):

| Function                                  | VA         | What it does                                              |
|-------------------------------------------|------------|-----------------------------------------------------------|
| `main.UDSKeyCalculateForUDAN`             | `0x9b9e80` | Go wrapper: validate seed-len=4, call C, return key       |
| `main._Cfunc_CalculateKey`                | `0x9b61a0` | cgo stub for the C key function                           |
| C function (no Go symbol)                 | `0xa4bca0` | C-side cgo trampoline                                     |
| C function (no Go symbol)                 | `0xa4bb90` | The actual key algorithm — REVERSED, see SecAccess L1      |
| CRC-16-CCITT LUT (256 × u16)              | `0x2731960`| Same poly 0x1021 LUT used by both key CRC steps            |
| `main._Cfunc_Crc_CalculateCRC16`          | `0x9b6210` | cgo stub — Modbus CRC (F700 protocol)                      |
| `main._Cfunc_Crc_CalculateCRC32`          | `0x9b62c0` | cgo stub                                                  |
| `main.(*ConnectionCan).unlock`            | `0x958ec0` | Full SecAccess unlock flow on UDS connection               |
| `main.(*ConnectionCan).tryUnlock`         | `0x958ec0` | (verify VA, near unlock)                                  |
| `main.(*DeviceData).P700UnlockSys`        | `0x8b54a0` | Public unlock entry for P700-family BMS (Solectrac uses this) |
| `main.(*ConnectionCan).uploadData`        | `0x957800` | UDS RequestUpload (0x35) wrapper — generic memory read     |
| `main.(*ConnectionCan).uploadData_UDM`    | `0x955440` | Variant — possibly for UDM/dataflash addressing            |
| `main.(*ConnectionCan).ReadHistoryData`   | `0x88bb40` | Event/log history read from W25N01G NAND                   |
| `main.(*GD25Q64).*` (~10 methods)         | `0x95f9e0+`| SPI NOR address arithmetic (page/sector/block/spare)       |
| `main.(*W25N01G).*` (~12 methods)         | `0x95fc30+`| SPI NAND address arithmetic                                |
| `main.UniversalBurn_P7_boot`              | `0x99a950` | Write firmware *to* bootloader region (P7 family)          |
| `main.UniversalBurn_P7_app2boot`          | `0x99d0e0` | Transition app→boot                                       |
| `main.UniversalBurn_P7_app`               | `0x99f880` | Write firmware *to* app region                            |
| `main.(*ConnectionCan).P700CheckBootMode` | `0x88fb00` | Probe whether BMS is in bootloader mode                    |
| `main.(*ConnectionCan).P700UpdateRun`     | `0x890a00` | Drive a firmware-update session                            |
| `main.(*ConnectionCan).SetUpdatePage`     | `0x88b4c0` | Per-page write during update                              |

A look-once string also notable: `UdsRequestUploadNRC7F3531` — confirms
the tool issues UDS service `0x35` and handles negative-response code
`7F 35 31` (request-out-of-range). So the BMS *does* implement
RequestUpload; the address range that it accepts is the open question
for any "read MCU flash via UDS" attempt.

### UI login (separate from UDS SecAccess)

The iBMS app's username/password screen is NOT gated by the UDS
seed/key algorithm — it's a separate, app-level check. `main.LoginCache`
(`0x882870`) reads a local JSON cache (fields `userName`, `passWord`,
`userType` + timestamp). If empty, `main.LoginLocal` (`0x882b40`) calls
the UDAN cloud at `udandtu-web-admin/client/...` over the configured
host/port from `handlLoginAddress`/`handlLoginPort`. No hardcoded
backdoor in either function. Failure strings:
`"username/password authentication failed"` (remote rejection),
`"invalid username/password version"` (cache schema mismatch).
Local UDS / Modbus operations don't appear to require the UI to be
logged in — the login gates cloud-routed features (uploads, OTA).


### iBMS PC Utility — software provenance

The protocol map above was derived from a Solectrac-specific install of
the iBMS PC Utility (UDAN's vendor tool) plus traces taken while the tool
was running.

#### Installer

- File: `docs/iBMSUpper-setup-x86(v3.1.7).exe`
- Type: Inno Setup installer (PE32, 36 MB), Company: UDAN
- Product: iBMS PC Utility v3.1.6 (build 2020-03-14)

#### Application

- Language: Go 1.15.15 → Windows PE32, UPX-compressed
- UI: embedded web app, served on localhost via HTTP
  - JS bundles `static/js/app.e064497ec6be8a62ce23.js`, `vendor.ee7bb8e9289003d7cac7.js` + 9 numbered chunks
- Serialization: Protocol Buffers (protobuf) for internal types
- Connection types (Go iface `Connection`):
  - `ConnectionCan` — UDS/CanTp transport (Solectrac uses this)
  - `ConnectionUart` — Modbus over UART-over-CAN (F700 family — see "Sibling BMS family" below)
  - `ConnectionDemo` — simulation

#### Embedded Go source paths

```
D:/golang/gopath/src/iBMSUpper/uds_read_data.go
D:/golang/gopath/src/iBMSUpper/uds_read_data_A7.go
D:/golang/gopath/src/iBMSUpper/uds_read_data_dataflash_gd25q64.go
D:/golang/gopath/src/iBMSUpper/uds_read_data_dataflash_w25n01g.go
D:/golang/gopath/src/iBMSUpper/uds_save_data_P7.go
D:/golang/gopath/src/iBMSUpper/uds_save_data_U6.go
D:/golang/gopath/src/iBMSUpper/uds_udan_key_calculator_YJC.go
```

#### Supported CAN adapters (kerneldll.ini)

47 ZLG (周立功) adapters across USBCAN / CANDTU / CANWIFI / PCI families,
plus Peak PCAN (separate driver) and CAN232 serial. CANFD support via
`zpcfd_x86.dll` / `usbcanfd.dll`.

#### Message-ID symbol table

The iBMS binary names internal data records by byte ID. These appear in
CSV export filenames (e.g. `Voltages 0x08.csv`); they are *not* CAN
arbitration IDs.

| ID    | Name                            | Mapped to DID(s) |
|-------|---------------------------------|------------------|
| 0x06  | Peak data                       | `0x2820`/`0x2828`/`0x2830`/`0x2838` |
| 0x08  | Voltages                        | `0x0101`         |
| 0x09  | Temperatures                    | `0x0102`         |
| 0x0A  | Heat and Pole Temperatures      | not on this pack |
| 0x0B  | Heat Pole MOS Temperatures      | not on this pack |
| 0x79  | Balancing state                 | UNKNOWN          |
| 0x80  | Device list                     | —                |
| 0x81  | Device info                     | —                |
| 0x82  | Device list (alt)               | —                |
| 0x83  | System state                    | alt name for `0x93` |
| 0x84  | DTU info                        | —                |
| 0x85  | Charging                        | alt name for `0x94` |
| 0x86  | Balancing state                 | UNKNOWN          |
| 0x87  | Alarm state                     | `0x4000` byte 0 only (TENTATIVE — the rest of `0x4000` is charger telemetry; see above) |
| 0x88  | (Dis)charged energy             | alt name for `0x89` |
| 0x89  | (Dis)charged energy (alt)       | `0x2810`         |
| 0x91  | List of supported commands      | —                |
| 0x92  | Device info (alt)               | —                |
| 0x93  | System state (alt)              | `0x2800`         |
| 0x94  | Charging (alt)                  | `0x0900`+`0x0901`+`0x0902` (`0x0900` byte 0 + `0x0901` CC sentinels CONFIRMED; rest of `0x0901` head TENTATIVE) |
| 0x95  | (Dis)charged time               | `0x2801`         |
| 0x96  | DTU info                        | —                |
| 0x97  | Enable/disable data             | UNKNOWN          |
| 0x98  | WiFi info                       | X700 subsystem (UNKNOWN DID) |
| 0x99  | Charging state / ChgState       | UNKNOWN          |
| 0x9A  | Voltages (alt)                  | UNKNOWN          |
| 0x9B  | Peak data (alt)                 | UNKNOWN          |
| 0x9D  | WiFi / DTU                      | X700 subsystem (UNKNOWN DID) |
| 0x9F  | System state                    | —                |
| 0xB6  | System state                    | —                |
| 0xBB  | DTU / "Enter programming session" | —              |
| 0xBE  | Temperature disabled data       | —                |
| 0xC0  | Host diagnostic data            | —                |

#### Protobuf message types

Found in the Go binary:

- `ChargeMessage` — charge request V/A, connect state, fault flags
- `DiagnosisMessage` — alarm count, diagnosis info
- `ExtremumMessage` — max/min cell V/T, SOC parameters
- `TotalPackageMessage` — wraps the above + Remote + CloudConfig
- `MosForceControlMessage` — force MOS index switch state
- `WorkModeControlMessage` — system lock/unlock, reset
- `RemoteControlMessage` — control type + cell-balance state envelope
- `CloudServiceConfigMessage` — cloud config

#### Remote / force-control surfaces

The Go binary exposes:

- `F700WriteForceContrl` (F700 only)
- `P700MOSForceContrl` — force MOS control
- `U600ElecLockForceContrl` — electric-lock control
- `U600HLSSForceContrl` — HLSS contactor control
- `ChgForceControl` / `ChgForceControlTime`

These imply UDS write or routine services exist on the BMS for
force-set, but the wire-level mapping is UNKNOWN until an observed write.

#### Product models listed in the binary

```
F700 / F702 / F715–F723 / F728–F753 / F780–F788
E700 / E720 / E721 / E730 / E750–E753
P700 (parallel BMS), U600, X700
```

### Sibling BMS family — F700 (Modbus over UART-over-CAN)

The iBMS tool also supports an entirely different protocol family used
by F700-class BMSs: **Modbus RTU framed over UART-over-CAN**, not UDS.
The Solectrac BMS is *not* F700 — it ignores the F700 probe — but this
is documented here because the iBMS tool always probes both families on
connect.

- Modbus client lib: `gitlab.udantech.com/wenjun.ye/go-modbus`
- CAN framing: `gitlab.udantech.com/xqp/can.(*RawClient)`, ≤ 8 B Modbus
  payload per CAN frame; transports over TCP to a CANDTU / CANWIFI adapter

#### F700 test mode (`F700TestModeSwitch`)

Writes Modbus holding register `0x0E11` using FC `0x10` (Write Multiple
Registers), slave address `0x01`:

| Direction      | Modbus RTU bytes                                  |
|----------------|---------------------------------------------------|
| Test mode ON   | `01 10 0E 11 00 01 02 00 01 8B 11`                |
| Test mode OFF  | `01 10 0E 11 00 01 02 00 00 4A D1`                |

Field breakdown: `[slave=01] [FC=10] [reg-hi=0E] [reg-lo=11] [qty-hi=00]
[qty-lo=01] [byte-count=02] [data-2-bytes] [CRC-lo] [CRC-hi]`

Split into CAN frames of ≤ 8 B each:

- Frame 1 (both): `01 10 0E 11 00 01 02 00`
- Frame 2 (ON):   `01 8B 11`
- Frame 2 (OFF):  `00 4A D1`

Related registers in the same template (purpose UNKNOWN): `0x0EDC`,
`0x0E13`. Related Go functions: `F700SwitchRunState` (reg `0x0E10`),
`F700SwitchProtocol`. Modbus slave address is configurable via
`ReinitSlaveAddress`. CAN arbitration ID is runtime-configured (not
hardcoded).

### Connection discovery sweep

The iBMS tool does not know in advance which protocol family or which CAN
ID the BMS uses. On connect, it broadcasts probe frames on both families,
sweeping multiple candidate IDs, until something responds. The cycle
repeats every ~2.7 s with no back-off.

#### UDS probe

Sent on each candidate UDS request ID. Observed sweep IDs: `0x740`,
`0x7D0`, `0x36E`.

| Field        | Value                          |
|--------------|--------------------------------|
| Frame bytes  | `03 22 A5 00 00 00 00 00`      |
| UDS request  | `0x22` ReadDID, DID `0xA500`   |

Solectrac BMS responds only on `0x740`/`0x748`.

A separate UDS-shaped frame `01 22 0F 1A 02 08 0F` is also seen on
`0x34E` each cycle. Purpose UNKNOWN.

#### F700 probe

Two CAN frames ~100 ms apart on the configured Modbus-over-CAN ID
(observed: `0x750`):

| # | Bytes                       | Meaning                                                   |
|---|-----------------------------|-----------------------------------------------------------|
| 1 | `AA AA 55 01`               | TENTATIVE: sync/handshake preamble (classic `AA AA 55`)   |
| 2 | `01 03 0B 36 00 0A 27 E7`   | Modbus FC `0x03` Read Holding Registers from `0x0B36`, qty 10, CRC `0x27E7` |

Solectrac BMS does not respond on `0x750`.

The probes contain no target addressing — the tool discriminates by
which ID + framing first gets an answer.

### iBMS UI tab structure

The iBMS PC Utility presents five top-level tabs (with sub-navigation
where present). Pairing tab transitions against trace polling-burst
boundaries was the primary technique for mapping DIDs to data — see
"DID-mapping methodology" in the reverse-engineering notes below.

| Top tab          | Right sub-nav                                                                                                                    | Driving DIDs              |
|------------------|----------------------------------------------------------------------------------------------------------------------------------|---------------------------|
| System overview  | —                                                                                                                                | Baseline DIDs only        |
| Cell info        | —                                                                                                                                | Baseline + `0x0EAx` / `0x0EDx` / `0x2803-4` / `0x096x` |
| Charge info      | —                                                                                                                                | Baseline + `0x09xx` cluster |
| BMS              | Hlss state · HV detection · Hall state · Shunt state · Signal detection · On-board volt · On-board temp · BMU info · X700        | Baseline + `0x09xx`/`0x0E*xx`/`0x0F*xx`/`0x16xx`/`0xA50x`/`0x064x` (all polled in parallel) |
| SOC              | Cap. config · SOC calib. config · HighSoc · LowSoc                                                                               | Baseline + one-shot `0x30xx` / `0x40xx` dump on "Read" |

The SOC tab also exposes Sync / Import / Export / Read / Write buttons.

### Historical CSV exports

The iBMS tool offers to pull historical state from the BMS's onboard
logger and save it as CSVs. Filename pattern: `<timestamp>_<UDAN-name
0xNN>.csv`. There is also an aggregate Excel file named after the pack
serial: `<pack-serial>_<timestamp>.xlsx`.

The UDAN message ID in the filename is the iBMS-internal record-type
tag (see "Message-ID symbol table" above), *not* a CAN ID. CSV row
timestamps come from the BMS RTC, which is not set — don't use these
timestamps to correlate against trace data.

CSV file inventory observed:

```
(Dis)charged energy 0x89.csv      — maps to DID 0x2810
(Dis)charged time 0x95.csv        — maps to DID 0x2801
Alarm state 0x87.csv              — maps to DID 0x4000 byte 0 (count); per-column severity bytes still UNMAPPED
Charging 0x94.csv                 — maps to DID 0x0900+0x0901+0x0902 (head TENTATIVE, CC tail CONFIRMED)
Peak data 0x06.csv                — maps to DID cluster 0x2820/0x2828/0x2830/0x2838
System state 0x93.csv             — maps to DID 0x2800
Temperatures 0x09.csv             — maps to DID 0x0102
Voltages 0x08.csv                 — maps to DID 0x0101
```

---

## Reverse-engineering notes

Active investigations: methodology, captured data, unresolved findings,
and open questions. Anything here is subject to change as more captures
arrive.

### DID-mapping methodology

DID-to-data assignments were derived by aligning trace polling-burst
boundaries against screenshots of the iBMS UI taken at known wall-clock
times. Procedure:

1. Note the active iBMS UI tab (and sub-nav) at each screenshot timestamp.
2. Segment the trace into windows where the set of polled DIDs is stable.
3. Intersect each window's DIDs with the "Driving DIDs" expected for the
   active tab (see "iBMS UI tab structure" above).
4. For each candidate DID, compare its response payload to the matching
   CSV's column count + scale and to live-UI values; promote to
   CONFIRMED if both check out.

CSV row *values* are not usable for correlation (BMS RTC is unset). CSV
*schema* (column names, widths, units) is the reliable cross-reference.

### Bootstrap RequestDownload (UNKNOWN purpose, CONFIRMED static)

Every iBMS connection includes a 1528-byte write to BMS memory at
`0x00003A00` (3 × ~516 B `TransferData` blocks, then `TransferExit`),
inserted into the bootstrap sequence after the SecAccess unlock and
before any UI polling. The payload is too small to be firmware.

The payload is **static across sessions** (CONFIRMED): the first frames of
each `TransferData` chunk are byte-identical across five separate
connection captures (`bms-connection.asc`, `-2`, `-3`, `-4`, `-5`):

```
36 01 → 01 00 3A BC ...
36 02 → 34 37 07 2B ...
36 03 → 0A C6 80 7B ...
```

This rules out a per-session challenge / session ticket and supports a
fixed blob (calibration table or stored auth credential).

TENTATIVE remaining hypotheses:

- A bootstrap / authentication blob the tool installs into RAM
- A calibration lookup table re-uploaded each session

Resolution now requires decompiling the iBMS Go binary to find what
prepares this blob (the per-session-vs-static question is settled).

### Late-session routine burst (UNKNOWN trigger)

~80 seconds into the navigation-tour capture, a parallel burst of
RoutineControl calls appears alongside additional DID reads at ~1 Hz:

| Request                                  | Response (B) |
|------------------------------------------|--------------|
| `31 01 F0 09`–`31 01 F0 11` (6 RIDs)     | 3–4 each     |
| `22 09 05`, `22 09 62`                   | 10 / 4       |
| `22 06 4E`, `22 06 70`, `22 06 71`       | 3 each       |

Not visible in any captured screenshot. TENTATIVE: triggered by a Sync /
Write button or one of the SOC tab inner sub-tabs (Total volt / Current /
Cell volt / Temp) that wasn't screenshotted.

### Open questions / TODO

- **`0x94 Charging` cluster — finish per-byte ↔ CSV-column mapping.**
  `data/dual-capture/dual-capture-charging-120.asc` (215 s, ~19 A L1
  charging, 144 polls) advanced this: `0x0900` is invariant during charge
  (byte 0 = `0x01` = "AC Chg" enum); `0x0901` trailing `FF FF FF FF` =
  CC + CC2 Resistance sentinels CONFIRMED; `0x0902` invariant all-zero
  during active charge (refutes "fault/state machine" guess). Still open:
  decoding the two dynamic paired u16 fields at `0x0901` bytes 0..1 BE
  and 4..5 BE (raw values don't land on a clean V at the observed pack
  voltage) — needs a screenshotted iBMS UI value alongside the wire
  capture to anchor units.
- **`0x4000` interpretation — UDAN `0x87` mapping is incorrect or partial.**
  The above capture shows `0x4000` is dominated by charger telemetry
  (bytes 5..9 mirror FF50 `data[0..4]`, byte 10 is a 1-Hz tick, bytes
  6/8/11/12 carry numeric measurements) rather than 31 severity-per-
  category bytes. Open: identify the actual Alarm-state DID (candidates:
  UDAN tag `0x99` "Charging state / ChgState — UNKNOWN" might in fact
  point at `0x4000`, and the real Alarm DID is unpolled), and lock down
  the byte-to-CSV-column order for the 73-column alarm CSV. Still
  requires a *new* fault firing (byte 0 incrementing past `0x02`) — the
  on-pack historical CSV only records `ChgOV` + `ChgPackOV` across all
  240 rows, so its column ordering alone cannot anchor the byte order.
- **Thermal offset.** `0x0102` is currently TENTATIVE `°C = raw − 40`.
  Wire-vs-UI alignment in `bms-screenshots.asc` (raw `41 41 41 41 40 41 41`)
  against Screenshots (2)/(3)/(4) shows all 7 probes displayed as 1°C,
  which is inconsistent with both `raw − 40` and `raw − 64` — the single
  `0x40` outlier should produce a distinct °C value but doesn't. Needs a
  capture with non-uniform probe temperatures to disambiguate.
- **Bootstrap RequestDownload payload.** Confirmed static across sessions
  (see above); remaining open question is what the blob *is*.
- **Late-session routine burst trigger.** See above.
- **Remaining tentative payload fields:** `0x2800` offsets 6/8/10;
  `0x2810` offsets 4–11; `0x0EA0`/`0x0EA1` (balancing); `0x0ED0`–`0x0ED7`
  (open-wire / short flags); `0x09xx` BMS-tab block (Hlss / HV / Hall).
- **Unmapped UDAN tags** from the iBMSUpper symbol table:
  - `0x0A` Heat and Pole Temperatures, `0x0B` Heat Pole MOS Temperatures —
    likely **features absent** on the Solectrac pack (no heat-pole MOS
    observed).
  - `0x79`, `0x86` Balancing state — UNKNOWN, but **no balancing visible
    in the capture** (cell delta < 7 mV), so possibly inactive rather than
    not implemented.
  - `0x88` (Dis)charged energy, `0x9A` Voltages, `0x9B` Peak data —
    likely **alt names** for already-mapped data (`0x89` → `0x2810`,
    `0x08` → `0x0101`, `0x06` → `0x2820`/`0x2828`/`0x2830`/`0x2838`).
  - `0x97` Enable/disable data — UNKNOWN.
  - `0x99` Charging state / ChgState — UNKNOWN; possibly fed by the
    `0x09xx` cluster currently labeled TENTATIVE Charging.

  Net: most are probably duplicate names or absent features. Indirect
  support: the BMS's own historical CSV export contains exactly 8 files,
  one per already-mapped tag (`0x06`, `0x08`, `0x09`, `0x87`, `0x89`,
  `0x93`, `0x94`, `0x95`) — no CSV for any of the unmapped tags above.
  The pack itself doesn't log these tags. Definitive confirmation still
  needs (a) an active-charge / active-fault / balancing capture, and (b)
  an attempt to read each unmapped UDAN tag's likely DID range to see
  what (if anything) responds.
- **F700 sibling family** (informational): which hardware variants use
  TestModeSwitch alt registers `0x0EDC` / `0x0E13`.
