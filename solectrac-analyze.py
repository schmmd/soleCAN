#!/usr/bin/env python3
"""
Decode J1939-style CAN logs from a small electric tractor BMS / charger.

Usage:
    python3 solectrac-analyze.py [-o OUTDIR] file1.asc [file2.blf ...]

Inputs are read via python-can's LogReader, so any format python-can
understands works: .asc (Vector ASCII), .blf, .log (canutils), .trc, and
python-can's own .csv format. (SavvyCAN's CSV export is *not* supported
because python-can doesn't read that dialect.)

Outputs (written into OUTDIR, default: current working directory):
    signals.csv   one tidy row per scalar measurement:
                  file, timestamp, frame_index, signal, value, unit
    frames.csv    one row per decoded frame (frames that produced >=1 signal):
                  frame_index, file, timestamp, can_id, pgn, source, len,
                  b0, b1, b2, b3, b4, b5, b6, b7
    decoders.csv  per-signal decode rule catalog:
                  signal, pgn, source, bytes, formula, unit, confidence, notes
    can_ids.csv   one row per unique CAN ID seen, with J1939 decode
    stdout        per-scenario summary

`frame_index` joins signals.csv -> frames.csv so any decoded value can be
traced back to its source bytes; decoders.csv documents the formula used
for each signal name. Together they let you re-derive any value by hand.

The long format is what pandas calls "tidy" data; pivot to a wide table
in one line:

    df = pd.read_csv("signals.csv")
    wide = df.pivot_table(index="timestamp", columns="signal", values="value")

Signal names use a `domain.name` (or `domain.NN.name`) convention:
    cell.NN.voltage_v          per-cell voltage (NN = 0-based BMS index)
    temp.NN.c                  per-channel module temp (NN = 0-based)
    pack.cell_max_mv           PGN F102 derived pack-wide stats
    pack.cell_min_mv
    pack.cell_spread_mv
    pack.cell_max_n            F102 byte 4: max-cell number, 1-based (BMS GUI numbering)
    pack.cell_min_n            F102 byte 5: min-cell number, 1-based (BMS GUI numbering)
    pack.cell_spread_mv_reported  F102 byte 7: BMS-reported spread (max-min) in mV.
                                  Verified to equal pack.cell_spread_mv in
                                  36,950 of 36,950 corpus frames; previously
                                  labelled 'pack.flags' but never carried flags.
    pack.v_estimate            20 * mean(min, max) / 1000
    pack.voltage_v             F100 bytes 0-1 BE: pack voltage, raw * 0.1 V/bit
    pack.current_raw           F100 bytes 2-3 (raw biased u16)
    pack.current_a             F100 signed pack current, A
    pack.power_w               F100 derived: pack.voltage_v * pack.current_a (signed; + draw / - charge)
    pack.temp_max_c            F104 byte 0: pack max module temp, b - 40
    pack.temp_min_c            F104 byte 1: pack min module temp, b - 40
    pack.temp_max_n            F104 byte 2: max-temp channel # (1-based)
    pack.temp_min_n            F104 byte 3: min-temp channel # (1-based)
    pack.temp_spread_c         F104 byte 4: max - min temp (°C)
    bms.state.byte0/1          F106 raw state bytes
    bms.state.output_enable    F106 byte 0 bit 0 (BMS output command active)
    bms.state.main_contactor   F106 byte 0 bit 2 (main contactor closed)
    bms.state.operating        F106 byte 0 bit 6 (operating mode: power flowing)
    bms.state.standby          F106 byte 0 bit 7 (standby: charger present, no
                               main current; mutex with .operating)
    bms.state.charging         F106 byte 1 bit 3 (charger active)
    bms.state.no_drive         F106 byte 1 bit 2 (drive not enabled)
    bms.state.drive_mode       F106 byte 1 bit 5 (motor enabled)
    bms.state.contactors       F106 byte 1 bit 6 (vehicle awake)
    bms.limit.discharge_a      F107 bytes 0-1 BE * 0.01: max discharge current
    bms.limit.charge_a         F107 bytes 2-3 BE * 0.01: max charge current
    bms.limit.mode             F107 byte 4: 0=charging, 1=driving
    bms.limit.byte5            F107 byte 5: pack-voltage echo at coarse quantization
                               (~0.221 V/bit, offset ~57.0 V; R^2=0.97 vs F100F3 V_pack
                               in driving captures); 0x00 in charging mode; rare
                               transient values 0x4D / 0x6B / 0xA5 / 0xA7 in init/teardown
    bms.limit.charge_power_extra_w
                               F107 bytes 6-7 BE * 10 W: charge-power allowance
                               above the 100 A baseline, verified against
                               (bms.limit.charge_a - 100 A) * pack.voltage_v
    bms.fault.byteN            F108 bytes 0..7 raw (only emitted when frame is non-zero;
                               corresponds to DBC FaultByteN_Raw signals).
                               Bytes 0..6 are a 2-bit-per-code bitmap covering codes
                               100..127 (4 codes per byte; see bms.fault.code_NNN). Byte 7
                               is the dashboard system/maintenance bitmap (1 bit per code:
                               124, 140, 142..146). Codes 124 and 140 also fall in the
                               bytes-0..6 layout (byte 6) and are emitted once per frame.
    bms.fault.code_NNN         vendor BMS error code asserted (=1). F108 bytes 0..6 use
                               mixed encoding: bytes 0..3 are 2 bits per code (codes
                               100..113; 114/115 reserved -> bits 4..7 of byte 3 silent),
                               bytes 4..5 are 1 bit per code (byte 4 = 116..123, byte 5
                               = 124..127 with bits 4..7 silent), byte 6 is silent.
                               Byte 7 uses 1 bit per code with gaps (bit 0 = 140; bits
                               1,2 silent; bit 3 = 142; bit 4 = 143; bits 5,6 both =
                               144; bit 7 = 145). Code 146 is not encoded in F108.
                               Per-bit tables live in BMS_FAULT_CODES_BYTES_0_TO_6 and
                               BMS_FAULT_CODES_BYTE7. All mappings injection-confirmed
                               on 2026-05-10.
    charger.flags              FF50 byte 4: Elcon fault flags (0x00 = delivering)
    charger.v_raw              FF50 bytes 0-1 BE (raw, always emitted)
    charger.voltage_v          FF50 charger output voltage, raw * 0.1 V/bit.
                               Equals pack V only while flags == 0x00;
                               otherwise reads the bare output rail
    charger.i_raw              FF50 bytes 2-3 BE (raw, always emitted)
    charger.current_a          FF50 charger output current, raw * 0.1 A/bit
    chgr_cmd.voltage_v         0600 bytes 0-1 BE * 0.1: BMS->charger V setpoint
                               (no +76.8 V offset; suppressed when idle)
    chgr_cmd.current_a         0600 bytes 2-3 BE * 0.1: BMS->charger I setpoint
                               (suppressed when idle)
    chgr_cmd.enable            0600 byte 4: 0=active command, 1=idle
    chgr_cmd.v_raw             0600 bytes 0-1 BE raw
    chgr_cmd.i_raw             0600 bytes 2-3 BE raw
    vc.state                   F100D0 byte 0 (vehicle-controller mode flag).
                               Only ever 0x00 (init/transition) or 0x0C (ready)
                               across 22,338 frames; bytes 1..7 are 0xFF padding
    motor.rpm_signed           FF21CA RPM with directional sign
    motor.rpm_magnitude        FF21CA RPM unsigned
    motor.direction            +1 forward / 0 neutral / -1 reverse (F/N/R lever, byte 7 low nibble)
    motor.range                R1/R2/R3 range-switch selector, value 1..3
                               (byte 7 high nibble; RPM cap selector — NOT the
                               mechanical L/M/H gear, which is sensor-less)
    motor.torque_raw           FF21CA bytes 0-1 (LE u16) — unsigned magnitude
                               of the controller's commanded motor effort
                               (torque / current command). Rises during both
                               drive and regen; direction of work comes from
                               sign(pack.current_a). See DOCUMENTATION.md
                               §FF21CA.
    motor.controller_temp_c    FF21CA byte 4 (only emitted when nonzero)
    motor.motor_temp_c         FF21CA byte 5 (only emitted when nonzero)
    dash.alive                 FF2112 byte 0 (0=booting, 1=alive; 10 Hz heartbeat from SA 0x12)
    pack.soc_raw               F100F3 byte 4 (raw)
    pack.soc_pct               F100F3 byte 4 -> percent (b4 * 0.4 - 0.8)
    dm1.lamp.byte0/1           FECA bytes 0/1 raw (lamp & flash status, when nonzero)
    dm1.lamp.NAME_state        FECA byte 0 per-lamp 2-bit state (NAME in
                               {malfunction, red_stop, amber_warning, protect})
    dm1.lamp.NAME_flash        FECA byte 1 per-lamp 2-bit flash status
    dm1.dtc.spn                FECA SAE J1939-73 SPN (19-bit)
    dm1.dtc.fmi                FECA J1939-73 FMI (5-bit failure mode)
    dm1.dtc.cm                 FECA SPN Conversion Method bit
    dm1.dtc.oc                 FECA Occurrence Count (7-bit)

Decoder assumptions (verify against the BMS spec before trusting numerically):
  * Source 0xF3 is the BMS (broadcasts), 0xE5 is the external charger,
    0xCA is the motor / drive ECU, 0xD0 is the vehicle controller, 0xF4
    is the BMS again in its charger-interface role (sends only PGN
    0x000600 destination-addressed to 0xE5).
    Byte numbering below is 0-based throughout (matches data[N] indexing
    in code and the DECODERS table; NOTES.txt uses 1-based, so data[1] in
    code = "byte 2" in NOTES).
  * PGN 0xF113..0xF13C: 4 cell voltages per frame, big-endian uint16 mV.
        cell_index = (PGN - 0xF113) * 4 + slot
        Indexes >= NUM_CELLS (20) and 0xFFFF "not present" sentinels are
        suppressed.
  * PGN 0xF155..0xF15E: 8 module temperatures per frame, uint8 with the
    J1939-style +40 C offset (raw 0x35 = 13 C).
        temp_index = (PGN - 0xF155) * 8 + slot
        Indexes >= NUM_TEMPS (7) and 0xFF "not present" sentinels are
        suppressed.
  * PGN 0xF102: bytes 0-1 BE = max cell mV, bytes 2-3 BE = min cell mV,
                byte 4 = max-cell number (1-based BMS GUI numbering),
                byte 5 = min-cell number (1-based BMS GUI numbering),
                byte 6 = 0x00 padding (constant across corpus),
                byte 7 = cell spread in mV (max - min, 1 mV/bit; verified
                         to match the computed (max-min) in 36,950/36,950
                         corpus frames).
  * PGN 0xF100 bytes 0-1 BE = pack voltage at the BMS terminals,
        one u16 at 0.1 V/bit. The 60..84 V operating window keeps byte 0
        at 0x02/0x03, which can make the field masquerade as a
        range-selector byte plus 8-bit voltage. Confirmed by linear
        regression against 20 * mean cell mV across the full voltage
        range, and cross-checked against the FF50 charger frame which
        carries the same BE-16 encoding.
  * PGN 0xF100 bytes 2-3 BE = signed pack current at 0.1 A/bit, biased so that
        raw 0x7D00 = 0 A (positive = drawing from pack, negative = charging).
        Cross-validated by the amp-*.asc dashboard-anchored set (1, 18, 35, 42,
        58 A): mean decoded current matches the displayed dashboard reading
        within ~1 A across the full range, including across the 0x7D->0x7E and
        0x7F->0x80 high-byte rollovers.
  * PGN 0xF108 = BMS active fault bitmap. All per-bit assignments
        established by injection sweep (solectrac-inject-f108.py) on
        2026-05-10. The per-bit code tables live in
        BMS_FAULT_CODES_BYTES_0_TO_6 and BMS_FAULT_CODES_BYTE7.
        Bytes 0..6 use MIXED encoding by byte:
          byte 0: 2 bits per code → 100, 101, 102, 103
          byte 1: 2 bits per code → 104, 105, 106, 107
          byte 2: 2 bits per code → 108, 109, 110, 111
          byte 3: 2 bits per code → 112, 113 (bits 4..7 silent; 114/115
                  are reserved per the manual and take zero bits)
          byte 4: 1 BIT per code  → 116, 117, 118, 119, 120, 121, 122, 123
          byte 5: 1 bit per code  → 124, 125, 126, 127 (bits 4..7 silent)
          byte 6: fully silent
        Byte 7 uses 1 bit per code with gaps and a duplicate:
          bit 0 = 140, bits 1,2 silent, bit 3 = 142, bit 4 = 143,
          bits 5 AND 6 both display 144 (re-verified duplicate), bit 7
          = 145. Code 146 ("Maintenance mode status") does NOT appear in
          F108 — the operator's "146" in bms-124-140-142-143-144-146.asc
          was almost certainly a 145 transcription.
        The bytes-0..6 and byte-7 active code sets are merged and
        deduplicated.
  * PGN 0xFF50 from 0xE5: standard Elcon/TC charger status frame.
                          bytes 0-1 BE = charger output voltage, 0.1 V/bit.
                          Tracks pack V while delivering (confirmed by
                          linear regression across a multi-hour L1 charge,
                          slope 0.099 V/LSB); reads the bare output rail
                          otherwise (~0.2 V plug-idle, slow decay after
                          charge end).
                          bytes 2-3 BE = charger output current, 0.1 A/bit
                          — CONFIRMED.
                          byte 4 = fault flags; 0x00 = actively delivering.
                            bit 0 = hardware fault
                            bit 1 = over-temperature
                            bit 2 = no AC input
                            bit 3 = battery voltage not detected at output
                            bit 4 = no BMS command (1806 timeout)
  * PGN 0x000600 from 0xF4 to 0xE5 (charger): the Elcon BMS->charger
        command frame. Decode confirmed by correlating
        58,584 frames in charging-120V-90ish-to-100.asc against
        contemporaneous F100 (pack V/I/SoC), FF50 (charger V/I/flags),
        and F107 (BMS current limits). Source 0xF4 sends only this PGN,
        and only to destination 0xE5 -- consistent with a dedicated SA
        for the BMS's charger-control role (likely the same physical
        BMS module that uses 0xF3 for broadcasts).
            bytes 0-1 BE u16 = voltage setpoint, 0.1 V/bit, no offset.
                               0x034E = 84.6 V (4.23 V/cell * 20 cells)
                               in every active-request frame.
            bytes 2-3 BE u16 = current setpoint, 0.1 A/bit, no offset.
                               Observed 3.0..39.0 A across the charge.
                               When the request <= the charger's delivery
                               capability (~14 A from a 120V/15A wall
                               outlet at 84 V), the charger tracks within
                               ~0.5 A. When the request exceeds capability
                               the charger saturates ~18 A regardless.
            byte 4           = enable: 0x00 = active command,
                               0x01 = idle / no-request (charger raises
                               its no-BMS-command flag within a few
                               frames).
            bytes 5-7        = padding 0xFF.
  * PGN 0xFECA from 0xCA: DM1 (Active Diagnostic Trouble Codes), per
        SAE J1939-73. Single-frame layout (multi-DTC BAM not observed):
            byte 0     = lamp status, 4 lamps x 2 bits each:
                           bits 7-6 MIL, 5-4 Red Stop,
                           3-2 Amber Warning, 1-0 Protect
            byte 1     = flash status, same lamp layout as byte 0
            bytes 2-5  = first DTC (4 bytes):
                           SPN  = b2 | (b3<<8) | ((b4>>5)&7)<<16
                           FMI  = b4 & 0x1F
                           CM   = (b5 >> 7) & 1
                           OC   = b5 & 0x7F
            bytes 6-7  = padding 0xFF for single-DTC frames
        All observed frames in our captures are the J1939 idle pattern
        (00 00 00 00 00 00 FF FF), which the decoder skips. Decoder is
        validated against the J1939-73 spec rather than against fault
        data; trust the lamp/state decode but treat any future SPN as
        TENTATIVE until cross-checked against vendor documentation.
  * PGN 0xFF21 from 0xCA: motor controller / drive ECU telemetry.
        bytes 0-1  = torque, little-endian uint16. Unsigned magnitude of
                     the controller's commanded motor effort (torque /
                     current command), observed 0..262 — peak forward
                     acceleration pushes past 255, which is why byte 1
                     occasionally reads 0x01. Symmetric across drive and
                     regen — the value rises whether the motor is being
                     driven or used as a generator. Direction of work
                     comes from sign(pack.current_a) on F100F3, not from
                     anywhere in FF21CA. Idle resting offset ~3 (sensor
                     noise floor); below raw ~14 the controller's internal
                     dead-low keeps RPM near 0. See DOCUMENTATION.md
                     §FF21CA for the full interpretation.
        bytes 2-3  = motor RPM magnitude, little-endian uint16, biased by 0x0C80
                     (rpm = ((b3<<8)|b2) - 0x0C80; verified against a
                     0->2500 RPM acceleration trace). Always positive; sign of
                     motion comes from byte 7.
        byte 4     = main controller temperature, J1939 +40 C offset.
        byte 5     = motor temperature, J1939 +40 C offset.
        byte 6     = always 0x00 across 45,086 frames in 30 captures
                     (reserved padding)
        byte 7     = packed transmission state: high nibble = RANGE GEAR
                     (1..3), low nibble = F/N/R LEVER. Confirmed by two
                     controlled captures:
                       drive-r-n-f.asc   walks the F/N/R lever R -> N -> F
                                         in Range 3, byte 7 = 0x28 -> 0x20
                                         -> 0x24 (low nibble 8 -> 0 -> 4).
                       range-1-2-3.asc   walks the range selector 1 -> 2 -> 3
                                         in Forward, byte 7 = 0x04 -> 0x14
                                         -> 0x24 (high nibble 0 -> 1 -> 2).
                     Encoding:
                       high nibble 0x0/0x1/0x2 = range switch R1/R2/R3
                       low  nibble 0x0/0x4/0x8 = N / F / R
                     Direction sign (for signed RPM) comes from the low
                     nibble; range is reported separately.
    Frame is suppressed entirely while charging (contactors open for traction).
  * PGN 0xFF21 from 0x12: dashboard / instrument-cluster heartbeat.
        Same PGN as the motor telemetry above, but a different sender (SA
        0x12 vs 0xCA), so the on-the-wire ID 0x18FF2112 is distinct from
        0x18FF21CA. SA 0x12 broadcasts only this PGN, at 10 Hz.
        byte 0     = alive flag: 0x00 during the first ~700 ms after key-on
                     (boot), 0x01 thereafter.
        bytes 1..7 = always 0x00 padding.
        SA 0x12 isn't in any standard J1939 SA table; the "dashboard" label
        is by elimination (the boot-then-alive pattern coincides exactly
        with the key-on transitions in the two ignition-* captures).
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import Tuple

try:
    import can
except ImportError:
    print("python-can is required: pip install python-can", file=sys.stderr)
    sys.exit(1)

from solectrac_proto import (
    SRC_BMS, SRC_BMS_CHGR_IF, SRC_CHARGER, SRC_VEHICLE, SRC_MOTOR, SRC_DASH,
    PGN_CELL_FIRST, PGN_CELL_LAST, PGN_TEMP_FIRST, PGN_TEMP_LAST,
    PGN_F100, PGN_F102, PGN_F104, PGN_F106, PGN_F107, PGN_F108,
    PGN_FF50, PGN_FF21, PGN_FECA, PGN_PROP_0600,
    DM1_LAMP_NAMES, VC_STATE_NAMES,
    NUM_CELLS, NUM_TEMPS, TEMP_OFFSET_C,
    PACK_CAPACITY_AH, PACK_NOMINAL_V, PACK_CAPACITY_WH,
    PACK_CURRENT_LSB_A, PACK_CURRENT_BIAS_RAW,
    PACK_VOLTAGE_LSB_V,
    CHARGER_V_LSB_V, CHARGER_I_LSB_A,
    RPM_BIAS, LIMIT_CURRENT_LSB_A,
    BMS_FAULT_CODES_BYTE7, BMS_FAULT_CODES_BYTES_0_TO_6,
    parse_id, be16, le16, data_bytes, c_to_f,
    decode as proto_decode,
)


# --- emit-time output shaping -----------------------------------------------

# Per-signal rounding precision (decimals) for the CSV output. Names not in
# this dict are written as-is (integers stay integers, floats keep full
# precision -- but most floats in the shared decoder are produced from
# round-trippable integer arithmetic, so they print cleanly).
_ROUND_PRECISION = {
    "pack.voltage_v": 2,
    "pack.current_a": 1,
    "pack.power_w": 1,
    "pack.soc_pct": 1,
    "pack.v_estimate": 3,
    "bms.limit.discharge_a": 2,
    "bms.limit.charge_a": 2,
    "charger.voltage_v": 2,
    "charger.current_a": 1,
    "chgr_cmd.voltage_v": 1,
    "chgr_cmd.current_a": 1,
}

# Signal names where value == 0 means "not asserted / absent" and the row
# is suppressed from the CSV to keep it compact. Matches the conditional
# emits in the original per-frame decoder.
_SUPPRESS_ZERO_PREFIXES = ("bms.fault.byte",)
_SUPPRESS_ZERO_EXACT = (
    {"dm1.lamp.byte0", "dm1.lamp.byte1"}
    | {f"dm1.lamp.{n}_state" for n in DM1_LAMP_NAMES}
    | {f"dm1.lamp.{n}_flash" for n in DM1_LAMP_NAMES}
)


def _make_emit(emissions: list):
    def emit(name, value, unit):
        if value == 0 and (
                name in _SUPPRESS_ZERO_EXACT
                or any(name.startswith(p) for p in _SUPPRESS_ZERO_PREFIXES)):
            return
        prec = _ROUND_PRECISION.get(name)
        if prec is not None:
            value = round(value, prec)
        emissions.append((name, value, unit))
    return emit


# --- script-local protocol tables -------------------------------------------

# DM1 lamp-status enum per J1939-73 (2 bits per lamp, same encoding for byte 0
# "lamp on/off" and byte 1 "flash status"):
#   0b00 = off / no flash
#   0b01 = on  / slow flash (1 Hz)
#   0b10 = reserved / fast flash (2 Hz)
#   0b11 = not available
DM1_LAMP_STATE = {0: "off", 1: "on", 2: "reserved", 3: "n/a"}
DM1_FLASH_STATE = {0: "no_flash", 1: "slow_1hz", 2: "fast_2hz", 3: "n/a"}


# Known PGN descriptions (SAE-defined plus what we've identified locally).
PGN_NAMES = {
    0x00EB00: "TP.DT (Transport Protocol Data Transfer)",
    0x00EC00: "TP.CM (Transport Protocol Connection Mgmt)",
    0x00EE00: "Address Claimed",
    0x00EF00: "Proprietary A",
    0x00FECA: "DM1 (Active Diagnostic Trouble Codes)",
    0x00FECB: "DM2 (Previously Active DTCs)",
    # Solectrac BMS broadcasts (vendor-defined within the J1939 envelope):
    0x00F100: "BMS pack status (incl. signed pack current)",
    0x00F102: "BMS cell min/max summary",
    0x00F104: "BMS temp min/max summary",
    0x00F106: "BMS state (charger-dependent)",
    0x00F107: "BMS current/voltage limits",
    0x00F108: "BMS active fault bitmap",
    0x00FF50: "Charger telemetry (V, A)",
    0x00FF21: "Motor telemetry (RPM, torque, state)",
    0x000600: "BMS->Charger command (V/I setpoint, enable)",
}


def describe_pgn(pgn: int) -> str:
    if pgn in PGN_NAMES:
        return PGN_NAMES[pgn]
    if PGN_CELL_FIRST <= pgn <= PGN_CELL_LAST:
        slot0 = (pgn - PGN_CELL_FIRST) * 4
        return f"BMS cell voltages {slot0}-{slot0 + 3}"
    if PGN_TEMP_FIRST <= pgn <= PGN_TEMP_LAST:
        slot0 = (pgn - PGN_TEMP_FIRST) * 8
        return f"BMS module temps {slot0}-{slot0 + 7}"
    if 0xFF00 <= pgn <= 0xFFFF:
        return "Proprietary B"
    if 0xF000 <= pgn <= 0xFEFF:
        return "Broadcast (vendor / unassigned)"
    return ""


def decode_can_id(can_id: int, is_extended: bool) -> dict:
    """Decode a CAN ID (11- or 29-bit) into J1939 fields."""
    if not is_extended:
        return {
            "can_id": f"{can_id:03X}",
            "ext": False,
            "priority": "",
            "r": "",
            "dp": "",
            "pf": "",
            "ps": "",
            "sa": "",
            "pgn": "",
            "pdu": "",
            "ps_role": "",
            "name": "non-J1939 (11-bit)",
        }
    priority = (can_id >> 26) & 0x7
    r = (can_id >> 25) & 0x1
    dp = (can_id >> 24) & 0x1
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF
    pdu2 = pf >= 0xF0
    pgn = (dp << 16) | (pf << 8) | (ps if pdu2 else 0)
    return {
        "can_id": f"{can_id:08X}",
        "ext": True,
        "priority": priority,
        "r": r,
        "dp": dp,
        "pf": f"{pf:02X}",
        "ps": f"{ps:02X}",
        "sa": f"{sa:02X}",
        "pgn": f"{pgn:06X}",
        "pdu": "PDU2" if pdu2 else "PDU1",
        "ps_role": "GE" if pdu2 else "DA",
        "name": describe_pgn(pgn),
    }


# --- per-frame decoders ------------------------------------------------------

def decode_file(path: Path, scenario: str, rows: list, frames: list,
                counts: dict, id_counts: dict):
    """Stream one log via python-can; append decoded rows + frames in-place.

    The actual byte-level decoding lives in solectrac_proto.decode(); this
    wrapper drives it with an emit callback that captures (name, value, unit)
    tuples (with per-name rounding and zero-suppression applied at emit
    time) and a category-driven counter update.
    """
    sc = counts.setdefault(scenario, {
        "total": 0, "cells": 0, "temps": 0, "f100": 0, "f102": 0,
        "f108": 0, "charger": 0, "vc": 0, "motor": 0,
        "skipped_zero": 0, "extended_false": 0,
    })

    reader = can.LogReader(str(path))
    try:
        for msg in reader:
            sc["total"] += 1
            can_id = msg.arbitration_id
            is_ext = bool(msg.is_extended_id)
            id_key = (can_id, is_ext)
            id_counts[id_key] = id_counts.get(id_key, 0) + 1

            emissions: list = []
            emit = _make_emit(emissions)
            category = proto_decode(msg, emit)

            if category == "non_extended":
                sc["extended_false"] += 1
                continue
            if category == "parse_error":
                # Malformed 29-bit ID; original analyze would have raised
                # here. Silently drop instead -- not worth crashing the
                # whole batch on a single bad frame.
                continue
            if category == "skipped_zero":
                sc["skipped_zero"] += 1
                continue
            if category is not None:
                sc.setdefault(category, 0)
                sc[category] += 1

            if emissions:
                _, pgn, src = parse_id(can_id)
                ts = msg.timestamp
                data = data_bytes(msg.data)
                frame_index = len(frames)
                frames.append((
                    frame_index, scenario, ts,
                    f"{can_id:08X}", f"{pgn:04X}", f"{src:02X}",
                    len(msg.data),
                    *(f"{b:02X}" for b in data),
                ))
                for signal, value, unit in emissions:
                    rows.append(
                        (scenario, ts, frame_index, signal, value, unit))
    finally:
        if hasattr(reader, "stop"):
            try:
                reader.stop()
            except Exception:
                pass


# --- writers / summary -------------------------------------------------------

SIGNALS_HEADER = ["file", "timestamp", "frame_index", "signal", "value", "unit"]
FRAMES_HEADER = ["frame_index", "file", "timestamp",
                 "can_id", "pgn", "source", "len",
                 "b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7"]
DECODERS_HEADER = ["signal", "pgn", "source", "bytes", "formula",
                   "unit", "confidence", "notes"]

# Per-signal decode rule catalog, written verbatim to decoders.csv.
# `bytes` is described relative to data byte 0 (i.e., J1939 SPN byte index 0,
# which corresponds to "byte 1" in some vendor-spec conventions).
DECODERS = [
    ("cell.NN.voltage_v", "F113..F13C", "F3", "2*slot, 2*slot+1 (slot 0..3)",
     "BE u16 / 1000", "v", "verified",
     "NN = (PGN-0xF113)*4 + slot; capped at NUM_CELLS=20; "
     "0-mV and 0xFFFF (not-present) sentinels suppressed"),
    ("temp.NN.c", "F155..F15E", "F3", "slot 0..7",
     "u8 - 40", "c", "verified",
     "NN = (PGN-0xF155)*8 + slot; capped at NUM_TEMPS=7; J1939 +40C offset; "
     "0 and 0xFF (not-present) sentinels suppressed"),
    ("pack.cell_max_mv", "F102", "F3", "0-1", "BE u16",
     "mv", "verified", ""),
    ("pack.cell_min_mv", "F102", "F3", "2-3", "BE u16",
     "mv", "verified", ""),
    ("pack.cell_spread_mv", "F102", "F3", "0-3", "max - min",
     "mv", "verified", ""),
    ("pack.cell_max_n", "F102", "F3", "4", "u8 (raw)",
     "", "verified",
     "max-cell number, 1-based (BMS GUI numbering); subtract 1 for 0-based cell_index"),
    ("pack.cell_min_n", "F102", "F3", "5", "u8 (raw)",
     "", "verified",
     "min-cell number, 1-based (BMS GUI numbering); subtract 1 for 0-based cell_index"),
    ("pack.cell_spread_mv_reported", "F102", "F3", "7", "u8",
     "mv", "verified",
     "BMS-reported cell spread in mV; matches computed (max-min) in "
     "36,950/36,950 corpus frames. Previously labelled 'pack.flags' but "
     "in fact carries the spread, not flag bits."),
    ("pack.v_estimate", "F102", "F3", "0-3", "20 * (max+min)/2 / 1000",
     "v", "verified", "assumes 20-cell pack"),
    ("pack.temp_max_c", "F104", "F3", "0", "u8 - 40",
     "c", "verified",
     "max module temp; cross-validated against per-channel temp.NN.c "
     "decoded from F155..F15E in every capture"),
    ("pack.temp_min_c", "F104", "F3", "1", "u8 - 40",
     "c", "verified", "min module temp; same cross-validation as temp_max_c"),
    ("pack.temp_max_n", "F104", "F3", "2", "u8 (raw)",
     "", "verified",
     "max-temp channel number, 1-based BMS GUI numbering; "
     "subtract 1 for 0-based temp_index"),
    ("pack.temp_min_n", "F104", "F3", "3", "u8 (raw)",
     "", "verified",
     "min-temp channel number, 1-based BMS GUI numbering; "
     "subtract 1 for 0-based temp_index"),
    ("pack.temp_spread_c", "F104", "F3", "4", "u8",
     "c", "verified", "= byte 0 - byte 1 in every observed capture"),
    ("pack.voltage_v", "F100", "F3", "0-1",
     "BE u16 * 0.1",
     "v", "verified",
     "pack terminal voltage; 60-84 V window keeps byte 0 at 0x02/0x03, "
     "which can masquerade as a range selector. Anchored by regression vs "
     "20*mean(cell mV); confirmed by FF50"),
    ("pack.current_raw", "F100", "F3", "2-3", "BE u16 (biased)",
     "", "verified", "subtract 0x7D00 for signed amps"),
    ("pack.current_a", "F100", "F3", "2-3", "(BE u16 - 0x7D00) * 0.1",
     "a", "verified",
     "+draw / -charge; cross-validated against amp-*.asc dashboard captures"),
    ("pack.power_w", "F100", "F3", "1, 2-3", "pack.voltage_v * pack.current_a",
     "w", "verified",
     "derived (not transmitted): instantaneous pack power, signed; "
     "+ = discharging, - = charging. Emitted on every F100F3 frame."),
    ("pack.soc_raw", "F100", "F3", "4", "u8 (raw)",
     "", "verified",
     "BMS-published SoC raw byte; saturates at 250 in soc-100-idle.asc"),
    ("pack.soc_pct", "F100", "F3", "4", "u8 * 0.4 - 0.8",
     "%", "verified",
     "calibrated from two direct screen readings: raw=202 at 80%, raw=227 at 90%; "
     "slope=0.4 intercept=-0.8; raw saturates at 250 (=99.2%) in soc-100-idle.asc"),
    ("bms.state.byte0", "F106", "F3", "0", "u8 (raw)",
     "", "verified",
     "BMS top-level mode bitfield; only six values observed across 36,955 "
     "frames in 30 captures: 0x00 (init), 0x44/0x45 (operating), 0x80/0x84/"
     "0x85 (standby). Bits decoded into bms.state.output_enable / "
     "main_contactor / operating / standby below; bits 6 and 7 are perfectly "
     "mutually exclusive (operating vs standby)"),
    ("bms.state.output_enable", "F106", "F3", "0 (bit 0)", "(b0 >> 0) & 1",
     "", "verified",
     "BMS output command active (drive request or active-charge request). "
     "Set during driving and active charging; cleared during plug-in "
     "handshake, post-charge teardown, fault-blocked charging, and init. "
     "Vendor-anchored: across 4 paired iBMS-UI + CAN state captures "
     "(idle / noseat / drive / plugin) this bit matches the operating-mode "
     "condition shown on the iBMS Charge-info tab"),
    ("bms.state.main_contactor", "F106", "F3", "0 (bit 2)", "(b0 >> 2) & 1",
     "", "verified",
     "main pack contactor closed. Cleared during init (b0=0x00) and after "
     "the contactor opens at end of charge (b0=0x80); set in all other "
     "observed states. Vendor-anchored: matches iBMS BMS-tab "
     "`BCU HSS1 (MainP+) State` Close/Open 1:1 across 4 paired state "
     "captures"),
    ("bms.state.operating", "F106", "F3", "0 (bit 6)", "(b0 >> 6) & 1",
     "", "verified",
     "operating mode — BMS ready to source/sink current, main contactor "
     "closed, not in standby. Initial semantic was 'power flowing' but the "
     "idle state-capture shows the bit set with pack current = 0.0 A, so "
     "readiness rather than active power flow. Cleared in init (b0=0x00) "
     "and in standby states (b0=0x80). Mutex with bms.state.standby holds "
     "in steady state across 36,955 frames; a ~1 s b0=0x85 transient at "
     "plug-in is the only observed overlap"),
    ("bms.state.standby", "F106", "F3", "0 (bit 7)", "(b0 >> 7) & 1",
     "", "verified",
     "standby mode: charger plugged in but no main-bus current (covers "
     "plug-in handshake, post-charge idle, and fault-blocked charging). "
     "Mutex with bms.state.operating holds except for ~1 s b0=0x85 "
     "transient at plug-in. Vendor-anchored: set in noseat / plug-in "
     "captures where iBMS shows BCU HSS1 = Open"),
    ("bms.state.byte1", "F106", "F3", "1", "u8 (raw)",
     "", "verified",
     "BMS state bitmap; bits decoded into bms.state.charging / "
     "no_drive / drive_mode / contactors below"),
    ("bms.state.charging", "F106", "F3", "1 (bit 3)", "(b1 >> 3) & 1",
     "", "verified",
     "set only while the charger is actively delivering "
     "(charger.flags == 0x00)"),
    ("bms.state.no_drive", "F106", "F3", "1 (bit 2)", "(b1 >> 2) & 1",
     "", "tentative",
     "set whenever drive is not enabled (boot before drive engages, key-off "
     "shutdown, charge sessions); near-complement of drive_mode. Previously "
     "misread as charger-present: plug.asc shows it set from key-on ~10 s "
     "before plug-in, and ignition-without-charger-inserted.asc shows it "
     "set at key-off with no charger anywhere"),
    ("bms.state.drive_mode", "F106", "F3", "1 (bit 5)", "(b1 >> 5) & 1",
     "", "verified",
     "set only in captures with motor (FF21CA) traffic; clear during charging"),
    ("bms.state.contactors", "F106", "F3", "1 (bit 6)", "(b1 >> 6) & 1",
     "", "verified",
     "set whenever the BMS is broadcasting / vehicle is awake — including "
     "noseat state where the main contactor is open. The name 'contactors' "
     "is therefore a misnomer (kept for backward compatibility): the bit "
     "tracks BMS-awake, not contactor-closed. For contactor state use "
     "bms.state.main_contactor (b0 bit 2). Vendor-anchored across 4 paired "
     "iBMS-UI + CAN state captures"),
    ("bms.limit.discharge_a", "F107", "F3", "0-1", "BE u16 * 0.01",
     "a", "verified",
     "max discharge current; 145.0 A in every drive capture, "
     "100.0 A during charging; matches 200 A peak / 100 A continuous spec"),
    ("bms.limit.charge_a", "F107", "F3", "2-3", "BE u16 * 0.01",
     "a", "verified",
     "max charge current; observed 100.0..130.0 A across current corpus"),
    ("bms.limit.mode", "F107", "F3", "4", "u8 (raw)",
     "", "verified",
     "0x00 in charging captures, 0x01 in drive captures"),
    ("bms.limit.byte5", "F107", "F3", "5", "u8 (raw)",
     "", "tentative",
     "coarse-quantized pack voltage echo: 0x71..0x77 (113..119) in "
     "drive captures tracks F100F3 V_pack with R^2=0.97 (V_pack ~= "
     "b5*0.2212 + 57.01, ~0.22 V/bit step); 0x00 while charging; "
     "rare transients 0x4D/0x6B/0xA5/0xA7 during ignition/teardown"),
    ("bms.limit.charge_power_extra_w", "F107", "F3", "6-7", "BE u16 * 10",
     "w", "verified",
     "charge-power allowance above the 100 A baseline. Across 558,309 paired "
     "F107/F100 frames in 66 captures, including a 14-hour 9.6%-to-99.2% "
     "charge capture, raw ~= (charge_a - 100 A) * pack.voltage_v / 10; "
     "all rows within 1 raw count."),
    ("bms.fault.byteN", "F108", "F3", "0..7", "u8 (raw, when nonzero)",
     "", "verified",
     "raw bitmap bytes; bytes 0..6 carry codes 100..127 at 2 bits per "
     "code (see bms.fault.code_NNN); byte 7 carries the system / "
     "maintenance code group at 1 bit per code"),
    ("bms.fault.code_NNN", "F108", "F3", "0..7", "see notes",
     "", "verified",
     "vendor BMS error code asserted (=1). Mixed encoding per "
     "BMS_FAULT_CODES_BYTES_0_TO_6: bytes 0..3 are 2 bits per code "
     "(byte 0=100-103, byte 1=104-107, byte 2=108-111, byte 3=112-113; "
     "114,115 reserved); bytes 4..5 are 1 bit per code (byte 4=116-123, "
     "byte 5=124-127); byte 6 silent. Byte 7 per BMS_FAULT_CODES_BYTE7 "
     "is 1 bit per code with gaps: bit 0=140, bits 1,2 silent, bit 3=142, "
     "bit 4=143, bits 5,6=144 (duplicate), bit 7=145. All mappings "
     "injection-confirmed on 2026-05-10; code 146 does not appear in F108"),
    ("charger.flags", "FF50", "E5", "4", "u8 (bitmask)",
     "", "verified",
     "Elcon/TC fault flags; 0x00 = actively delivering. Bit 0 hardware "
     "fault, bit 1 over-temperature, bit 2 no AC input, bit 3 battery "
     "voltage not detected at output, bit 4 no BMS command (1806 "
     "timeout). Observed: 0x08 plug-idle; 0x14/0x1C key-on wake with no "
     "AC; 0x08->0x0C->0x1C cascade after charge end"),
    ("charger.v_raw", "FF50", "E5", "0-1", "BE u16",
     "", "verified",
     "raw bytes always emitted"),
    ("charger.voltage_v", "FF50", "E5", "0-1", "BE u16 * 0.1",
     "v", "verified",
     "charger output-terminal voltage; same BE-16 encoding as F100 "
     "bytes 0-1. Tracks pack V only while flags == 0x00; otherwise the "
     "bare rail (~0.2 V plug-idle, slow decay after charge end)"),
    ("charger.i_raw", "FF50", "E5", "2-3", "BE u16",
     "", "verified",
     "raw bytes always emitted"),
    ("charger.current_a", "FF50", "E5", "2-3", "BE u16 * 0.1",
     "a", "verified",
     "charger DC output current; 0.0 A whenever not delivering. L1 "
     "charging tops out ~21.5 A so byte 2 has read 0x00 in every "
     "capture; an L2 charge should exercise it"),
    ("chgr_cmd.voltage_v", "0600", "F4", "0-1", "BE u16 * 0.1",
     "v", "verified",
     "BMS-commanded charger voltage setpoint; always 84.6 V during "
     "active requests (20s NMC * 4.23 V/cell); no +76.8 V offset "
     "(unlike F100/FF50). Suppressed during idle frames."),
    ("chgr_cmd.current_a", "0600", "F4", "2-3", "BE u16 * 0.1",
     "a", "verified",
     "BMS-commanded charger current setpoint; 3.0..39.0 A observed "
     "across the 90%->100% charge in charging-120V-90ish-to-100.asc; "
     "charger.current_a tracks within ~0.5 A when request <= charger "
     "delivery capability, saturates ~18 A on a 120V/15A wall outlet "
     "when request exceeds it. Suppressed during idle frames."),
    ("chgr_cmd.enable", "0600", "F4", "4", "u8 (raw)",
     "", "verified",
     "0x00 = active charging command, 0x01 = idle / no-request "
     "(charger.flags picks up the no-BMS-command bit within a few "
     "frames)"),
    ("chgr_cmd.v_raw", "0600", "F4", "0-1", "BE u16",
     "", "verified",
     "raw setpoint, emitted alongside the engineering value for parity "
     "with charger.v_raw / pack.current_raw"),
    ("chgr_cmd.i_raw", "0600", "F4", "2-3", "BE u16",
     "", "verified", ""),
    ("vc.state", "F100", "D0", "0", "u8 (raw)",
     "", "verified",
     "vehicle-controller mode flag; only 0x00 (init/transition) and 0x0C "
     "(ready) observed across 22,338 frames in 30 captures. The 0x00 frames "
     "(19 total, all in ignition-without-charger-inserted.asc) burst briefly "
     "~0.5-1 s before BMS F106 byte 1 transitions between drive_mode and "
     "no_drive, suggesting it leads BMS mode changes. Bytes 1..7 of "
     "F100D0 are 0xFF (J1939 'not available') in every frame."),
    ("motor.rpm_signed", "FF21", "CA", "2-3, 7",
     "(LE u16 - 0x0C80) * direction(b7)", "rpm", "verified", ""),
    ("motor.rpm_magnitude", "FF21", "CA", "2-3", "LE u16 - 0x0C80",
     "rpm", "verified", "verified against 0->2500 RPM acceleration trace"),
    ("motor.direction", "FF21", "CA", "7",
     "low nibble: 0x4->+1, 0x8->-1, 0x0->0",
     "", "verified",
     "F/N/R lever; verified by drive-r-n-f.asc walking R->N->F"),
    ("motor.range", "FF21", "CA", "7",
     "(b7 >> 4) + 1", "", "verified",
     "range switch R1/R2/R3 (RPM cap selector); verified by range-1-2-3.asc walking 1->2->3"),
    ("motor.torque_raw", "FF21", "CA", "0-1", "u16 LE (raw)",
     "", "verified",
     "unsigned magnitude of controller's commanded motor effort "
     "(torque / current command), observed 0..262; symmetric across "
     "drive and regen. Direction comes from sign(pack.current_a). Idle "
     "offset ~3, controller dead-low ~14. See DOCUMENTATION.md §FF21CA."),
    ("motor.controller_temp_c", "FF21", "CA", "4", "u8 - 40",
     "c", "tentative",
     "main controller temp; consistently warmer than byte 5 and ramps up "
     "from cold-start; 0 = not present and suppressed"),
    ("motor.motor_temp_c", "FF21", "CA", "5", "u8 - 40",
     "c", "tentative",
     "motor temp; cooler/steadier than byte 4; 0 = not present and suppressed"),
    ("dash.alive", "FF21", "12", "0", "u8 (raw)",
     "", "verified",
     "dashboard / instrument-cluster heartbeat at 10 Hz; 0x00 during the "
     "first ~700 ms after key-on (boot), 0x01 thereafter; bytes 1..7 are "
     "always 0x00 padding; SA 0x12 sends only this PGN"),
    ("dm1.lamp.byte0", "FECA", "CA", "0", "u8 (raw, when nonzero)",
     "", "verified",
     "SAE J1939-73 DM1 lamp-status byte; per-lamp decode below; "
     "every observed frame in current captures = 0x00 (no faults active)"),
    ("dm1.lamp.byte1", "FECA", "CA", "1", "u8 (raw, when nonzero)",
     "", "verified",
     "SAE J1939-73 DM1 lamp-flash-status byte; per-lamp decode below"),
    ("dm1.lamp.NAME_state", "FECA", "CA", "0 (2 bits)", "(b0 >> shift) & 3",
     "", "verified",
     "NAME in {malfunction, red_stop, amber_warning, protect}; "
     "shift = 6,4,2,0 respectively; values 0=off, 1=on, 2=reserved, 3=n/a; "
     "emitted only when nonzero"),
    ("dm1.lamp.NAME_flash", "FECA", "CA", "1 (2 bits)", "(b1 >> shift) & 3",
     "", "verified",
     "same NAME / shift mapping as _state; values 0=no_flash, 1=slow_1Hz, "
     "2=fast_2Hz, 3=n/a"),
    ("dm1.dtc.spn", "FECA", "CA", "2-4",
     "b2 | (b3<<8) | ((b4>>5)&7)<<16", "", "verified",
     "SAE J1939-73 SPN (Suspect Parameter Number, 19 bits); CM=0 layout; "
     "emitted only when SPN!=0 or FMI!=0 (no active DTCs in any observed capture)"),
    ("dm1.dtc.fmi", "FECA", "CA", "4 (low 5 bits)", "b4 & 0x1F",
     "", "verified",
     "Failure Mode Indicator (5 bits); SAE J1939-73 Appendix A enumerates "
     "the 32 standard FMIs (0=above-range high, 1=below-range low, etc.)"),
    ("dm1.dtc.cm", "FECA", "CA", "5 (bit 7)", "(b5 >> 7) & 1",
     "", "verified",
     "SPN Conversion Method bit; 0 = modern (this decoder), 1 = legacy "
     "(re-decode SPN/FMI if observed nonzero)"),
    ("dm1.dtc.oc", "FECA", "CA", "5 (low 7 bits)", "b5 & 0x7F",
     "", "verified",
     "Occurrence Count: number of times this DTC has been activated since "
     "the last clear (saturates at 126; 127 = not available)"),
]

# can_ids.csv has its own writer because it's per-ID metadata, not timeseries.
IDS_SCHEMA = ["can_id", "ext", "count", "priority", "R", "DP",
              "PF", "PS", "SA", "PGN", "PDU", "PS_role", "name"]


def write_signals(rows: list, out_dir: Path):
    path = out_dir / "signals.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(SIGNALS_HEADER)
        w.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows)")


def write_frames(frames: list, out_dir: Path):
    path = out_dir / "frames.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FRAMES_HEADER)
        w.writerows(frames)
    print(f"wrote {path} ({len(frames)} frames)")


def write_decoders(out_dir: Path):
    path = out_dir / "decoders.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(DECODERS_HEADER)
        w.writerows(DECODERS)
    print(f"wrote {path} ({len(DECODERS)} decoders)")


def write_ids(id_counts: dict, out_dir: Path):
    """Emit the per-unique-ID J1939 decode table to can_ids.csv."""
    path = out_dir / "can_ids.csv"
    decoded = []
    for (can_id, is_ext), n in id_counts.items():
        d = decode_can_id(can_id, is_ext)
        decoded.append((d, n))
    # Sort: 29-bit before 11-bit, then by numeric ID value.
    decoded.sort(key=lambda dn: (not dn[0]["ext"], int(dn[0]["can_id"], 16)))

    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(IDS_SCHEMA)
        for d, n in decoded:
            w.writerow([d["can_id"], d["ext"], n, d["priority"], d["r"], d["dp"],
                        d["pf"], d["ps"], d["sa"], d["pgn"], d["pdu"],
                        d["ps_role"], d["name"]])
    print(f"wrote {path} ({len(decoded)} unique IDs)")


def values_for(rows: list, scenario: str, signal: str):
    """Pull all values for one scenario+signal pair."""
    return [r[4] for r in rows if r[0] == scenario and r[3] == signal]


def integrate_power(rows: list, scenario: str) -> Tuple[float, float, float]:
    """Trapezoidal integration of pack.power_w over the capture, returning
    (wh_drawn, wh_charged, capture_seconds). Drawn / charged are positive
    energies; sign is folded by clamping each sample to >=0 / <=0 before
    integrating each side. Skips samples whose dt is implausibly large
    (>5 s) so any gaps don't smear arbitrary power across the gap."""
    samples = [(r[1], r[4]) for r in rows
               if r[0] == scenario and r[3] == "pack.power_w"]
    samples.sort()
    wh_out = 0.0    # drawn
    wh_in = 0.0     # charged
    if len(samples) < 2:
        return 0.0, 0.0, 0.0
    span = samples[-1][0] - samples[0][0]
    for (t0, p0), (t1, p1) in zip(samples, samples[1:]):
        dt = t1 - t0
        if dt <= 0 or dt > 5.0:
            continue
        # trapezoidal rule, split by sign so a + . - mix doesn't cancel
        avg_pos = (max(p0, 0.0) + max(p1, 0.0)) / 2.0
        avg_neg = (min(p0, 0.0) + min(p1, 0.0)) / 2.0
        wh_out += avg_pos * dt / 3600.0
        wh_in += -avg_neg * dt / 3600.0
    return wh_out, wh_in, span


def summarize(counts: dict, rows: list):
    print()
    print(f"{'file':<28} {'frames':>7} {'cells':>6} {'temps':>6} "
          f"{'F100':>5} {'F102':>5} {'F108':>5} {'chgr':>5} {'vc':>5} "
          f"{'motor':>6}")
    for scenario, sc in counts.items():
        print(f"{scenario:<28} {sc['total']:>7} {sc['cells']:>6} "
              f"{sc['temps']:>6} {sc['f100']:>5} {sc['f102']:>5} "
              f"{sc['f108']:>5} {sc['charger']:>5} {sc['vc']:>5} "
              f"{sc['motor']:>6}")

    print("\nsummary:")
    for scenario in counts:
        print(f"\n  {scenario}")
        maxs = values_for(rows, scenario, "pack.cell_max_mv")
        mins = values_for(rows, scenario, "pack.cell_min_mv")
        spreads = values_for(rows, scenario, "pack.cell_spread_mv")
        est = values_for(rows, scenario, "pack.v_estimate")
        if maxs:
            print(f"    cell max  : {min(maxs)}..{max(maxs)} mV")
            print(f"    cell min  : {min(mins)}..{max(mins)} mV")
            print(f"    spread    : {min(spreads)}..{max(spreads)} mV")
            print(f"    pack est  : {min(est):.2f}..{max(est):.2f} V "
                  f"(20 cells * mean cell mV)")
        amps = values_for(rows, scenario, "pack.current_a")
        if amps:
            print(f"    I (F100)  : {min(amps):+.1f}..{max(amps):+.1f} A "
                  f"(0.1 A/bit, +draw / -charge)")
        powers = values_for(rows, scenario, "pack.power_w")
        if powers:
            wh_out, wh_in, span = integrate_power(rows, scenario)
            net = wh_out - wh_in
            avg_pos = (sum(p for p in powers if p > 0)
                       / max(1, sum(1 for p in powers if p > 0)))
            avg_neg = (sum(p for p in powers if p < 0)
                       / max(1, sum(1 for p in powers if p < 0)))
            print(f"    P (F100)  : {min(powers):+.0f}..{max(powers):+.0f} W "
                  f"(V*I, signed)")
            print(f"    P avg     : draw {avg_pos:+.0f} W  /  "
                  f"charge {avg_neg:+.0f} W  "
                  f"(over {span:.1f} s of samples)")
            print(f"    energy    : drawn {wh_out:.1f} Wh  /  "
                  f"charged {wh_in:.1f} Wh  /  net {net:+.1f} Wh "
                  f"(of {PACK_CAPACITY_WH/1000:.1f} kWh nominal)")
        active_codes = sorted({
            int(r[3].rsplit("_", 1)[1])
            for r in rows
            if r[0] == scenario and r[3].startswith("bms.fault.code_")
        })
        if active_codes:
            print(f"    BMS codes : {', '.join(str(c) for c in active_codes)} "
                  f"(union over capture; F108 bytes 0..7)")
        chgr_v = values_for(rows, scenario, "charger.voltage_v")
        chgr_i = values_for(rows, scenario, "charger.current_a")
        if chgr_v:
            print(f"    chgr V    : {min(chgr_v):.1f}..{max(chgr_v):.1f} V "
                  f"(output terminals; pack V only while flags=0x00)")
            print(f"    chgr I    : {min(chgr_i):.1f}..{max(chgr_i):.1f} A")
        # Per-channel module temps share the temp.NN.c naming.
        temps_c = [r[4] for r in rows
                   if r[0] == scenario
                   and r[3].startswith("temp.")
                   and r[3].endswith(".c")]
        if temps_c:
            t_min, t_max = min(temps_c), max(temps_c)
            print(f"    temps     : {t_min}..{t_max} C  "
                  f"({c_to_f(t_min)}..{c_to_f(t_max)} F)")
        rpms_signed = values_for(rows, scenario, "motor.rpm_signed")
        rpms_mag = values_for(rows, scenario, "motor.rpm_magnitude")
        dirs = values_for(rows, scenario, "motor.direction")
        tq = values_for(rows, scenario, "motor.torque_raw")
        if rpms_signed:
            n_fwd = sum(1 for d in dirs if d == 1)
            n_rev = sum(1 for d in dirs if d == -1)
            n_neu = sum(1 for d in dirs if d == 0)
            print(f"    motor RPM : {min(rpms_signed)}..{max(rpms_signed)} (signed)")
            print(f"    |RPM|     : {min(rpms_mag)}..{max(rpms_mag)}")
            print(f"    torque    : {min(tq)}..{max(tq)} (raw)")
            print(f"    F/N/R     : F={n_fwd}  R={n_rev}  N={n_neu}")
            ranges = values_for(rows, scenario, "motor.range")
            if ranges:
                seen = sorted(set(ranges))
                counts = "  ".join(f"R{g}={ranges.count(g)}" for g in seen)
                print(f"    range     : {counts}")


# --- main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Decode J1939-style CAN logs from a Solectrac tractor.",
        epilog="supported formats: any python-can LogReader format "
               "(.asc, .blf, .log, .trc, python-can .csv)",
    )
    parser.add_argument("inputs", nargs="+", metavar="FILE",
                        help="CAN log file(s) to decode")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path.cwd(),
                        metavar="DIR",
                        help="directory to write output CSVs into "
                             "(default: current working directory)")
    args = parser.parse_args()

    inputs = [Path(p) for p in args.inputs]
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    frames = []
    counts = {}
    id_counts = {}

    for path in inputs:
        print(f"reading {path.name}")
        decode_file(path, path.name, rows, frames, counts, id_counts)

    write_signals(rows, out_dir)
    write_frames(frames, out_dir)
    write_decoders(out_dir)
    write_ids(id_counts, out_dir)
    summarize(counts, rows)


if __name__ == "__main__":
    main()
