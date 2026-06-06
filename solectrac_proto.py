"""
solectrac_proto — shared protocol constants and helpers for the Solectrac
CAN bus decoders (solectrac-analyze.py and solectrac-stream.py).

This module is the single source of truth for J1939 SA / PGN identifiers,
BMS fault-bit tables, voltage / current / temperature scalings, and the
trivial bit-twiddling helpers both scripts use. Display-only tables
(human-readable lamp / flash text, BMS / MC code descriptions) live in
the script that renders them, not here.
"""

from typing import List, Tuple


# --- bus map ---------------------------------------------------------------

SRC_BMS = 0xF3
SRC_BMS_CHGR_IF = 0xF4   # BMS in its charger-interface role; only sender of
                         # PGN 0x000600 to 0xE5 (proprietary charger commands)
SRC_CHARGER = 0xE5
SRC_VEHICLE = 0xD0       # vehicle controller; minimal F100 heartbeat
SRC_MOTOR = 0xCA         # motor controller / drive ECU; FF21, DM1 source
SRC_DASH = 0x12          # dashboard / instrument-cluster heartbeat; only FF21


# --- PGNs ------------------------------------------------------------------

PGN_CELL_FIRST, PGN_CELL_LAST = 0xF113, 0xF13C
PGN_TEMP_FIRST, PGN_TEMP_LAST = 0xF155, 0xF15E

PGN_F100 = 0xF100   # pack status (V, signed pack current, SoC)
PGN_F102 = 0xF102   # cell min/max summary
PGN_F104 = 0xF104   # temp min/max summary (symmetric with F102)
PGN_F106 = 0xF106   # BMS state / mode (bytes 0,1 = bitmap)
PGN_F107 = 0xF107   # BMS current limits (charge/discharge)
PGN_F108 = 0xF108   # BMS active fault bitmap (byte 7 = dashboard codes)

PGN_FF50 = 0xFF50   # charger telemetry (V, A, status)
PGN_FF21 = 0xFF21   # motor telemetry (RPM, throttle, state) / dash heartbeat
PGN_FECA = 0xFECA   # SAE J1939-73 DM1 (Active Diagnostic Trouble Codes)
PGN_PROP_0600 = 0x0600   # PDU1, src 0xF4 -> dest 0xE5: BMS charger setpoint


# --- DM1 (lamp names only; display-text tables live in each script) --------

DM1_LAMP_NAMES = ("malfunction", "red_stop", "amber_warning", "protect")


# --- vehicle-controller heartbeat ------------------------------------------

# F100D0 byte 0 takes only 0x00 (init/transition, 19 frames across 22,338)
# or 0x0C (ready, the other 22,319). Bytes 1..7 are 0xFF padding.
VC_STATE_NAMES = {0x00: "init", 0x0C: "ready"}


# --- pack topology ---------------------------------------------------------

# Vendor BMS GUI nameplate. Cell / temp PGN ranges have room for many more
# channels but only NUM_CELLS / NUM_TEMPS slots are real on this pack.
NUM_CELLS = 20
NUM_TEMPS = 7

# Pack ratings: vendor BMS GUI reports 300 Ah at 72.0 V nominal (21.6 kWh).
# The FT 25G service manual nameplates the pack at 350 Ah / 73 V / 25.5 kWh
# (20S4P NMC modules). GUI value is likely a derated/usable capacity; kept
# for back-compat with prior SOC->Wh calculations. Not used for any decoding.
PACK_CAPACITY_AH = 300.0
PACK_NOMINAL_V = 72.0
PACK_CAPACITY_WH = PACK_CAPACITY_AH * PACK_NOMINAL_V    # 21,600 Wh


# --- encoding scales -------------------------------------------------------

TEMP_OFFSET_C = 40                        # J1939 +40 C offset on temp bytes

PACK_CURRENT_LSB_A = 0.1                  # F100F3 bytes 2-3 BE, 0.1 A/bit
PACK_CURRENT_BIAS_RAW = 0x7D00            # raw value at 0 A (positive = discharge)
PACK_VOLTAGE_LSB_V = 0.1                  # F100F3 byte 1 and FF50E5 bytes 1-2 LE
PACK_VOLTAGE_OFFSET_HI_V = 76.8           # F100F3 variant 0x03 / FF50 (76.8–102.3 V)
PACK_VOLTAGE_OFFSET_LO_V = 51.2           # F100F3 variant 0x02 (51.2–76.7 V)

# Charger and BMS share the same on-the-wire voltage encoding today, but
# keep these as their own named bindings so a future divergence has one
# place to plug in. See feedback-keep-encoding-constants.
CHARGER_V_LSB_V = PACK_VOLTAGE_LSB_V
CHARGER_V_OFFSET_V = PACK_VOLTAGE_OFFSET_HI_V
CHARGER_I_LSB_A = 0.1

RPM_BIAS = 0x0C80                         # FF21CA bytes 2-3 LE zero-RPM offset
LIMIT_CURRENT_LSB_A = 0.01                # F107F3 bytes 0-1 / 2-3 BE, 0.01 A/bit


# --- F108 fault bit tables -------------------------------------------------

# All per-bit assignments confirmed by injection sweep on 2026-05-10
# (solectrac-inject-f108.py). Per-source descriptions live in each script's
# BMS_FAULT_DESCRIPTIONS table (display-only, operator-manual text).

# Byte 7: 1 bit per dashboard system/maintenance code, with gaps and a
# duplicate. bits 1,2 silent; bits 5 AND 6 both display 144; code 146 is
# NOT encoded in F108 (the operator's "146" was a 145 transcription).
BMS_FAULT_CODES_BYTE7: List[Tuple[int, int]] = [
    (0, 140),
    (3, 142),
    (4, 143),
    (5, 144),
    (6, 144),  # duplicate of bit 5 (re-verified by injection)
    (7, 145),
]

# Bytes 0..6: mixed 2-bit-per-code / 1-bit-per-code encoding by byte.
# bytes 0..3 are 2 bits per code (each adjacent pair displays the same
# code); bytes 4..5 are 1 bit per code; byte 6 is fully silent. Reserved
# codes 114, 115 take zero bits (bits 4..7 of byte 3 silent).
BMS_FAULT_CODES_BYTES_0_TO_6: dict = {
    0: (100, 100, 101, 101, 102, 102, 103, 103),
    1: (104, 104, 105, 105, 106, 106, 107, 107),
    2: (108, 108, 109, 109, 110, 110, 111, 111),
    3: (112, 112, 113, 113, None, None, None, None),
    4: (116, 117, 118, 119, 120, 121, 122, 123),
    5: (124, 125, 126, 127, None, None, None, None),
    # byte 6: all 8 bits silent
}


# --- helpers ---------------------------------------------------------------

def parse_id(can_id: int) -> Tuple[int, int, int]:
    """Decode a 29-bit J1939 ID into (priority, pgn, source)."""
    src = can_id & 0xFF
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    pgn = (pf << 8) | (ps if pf >= 0xF0 else 0)   # PDU2 vs PDU1
    priority = (can_id >> 26) & 0x7
    return priority, pgn, src


def be16(hi: int, lo: int) -> int:
    return (hi << 8) | lo


def le16(lo: int, hi: int) -> int:
    return (hi << 8) | lo


def data_bytes(msg_data) -> list:
    """Return 8 ints, padding with 0 for short payloads, truncated to 8."""
    out = list(msg_data)
    while len(out) < 8:
        out.append(0)
    return out[:8]


def c_to_f(c):
    """Celsius -> Fahrenheit, rounded to 1 decimal. Returns None for None input."""
    return None if c is None else round(c * 9 / 5 + 32, 1)


# --- F108 derivation -------------------------------------------------------

def derive_bms_fault_codes(data: list) -> List[int]:
    """Return the sorted set of active BMS fault codes implied by an F108
    payload's 8 data bytes. Bytes 0..6 use the mixed-encoding table; byte 7
    uses the 1-bit-per-code-with-gaps table. The two sets are deduplicated
    so each code appears once even when both bytes 0..6 and byte 7 assert it.
    """
    active: set = set()
    for byte_idx, codes in BMS_FAULT_CODES_BYTES_0_TO_6.items():
        b = data[byte_idx]
        for bit_idx, code in enumerate(codes):
            if code is None:
                continue
            if (b >> bit_idx) & 1:
                active.add(code)
    b7 = data[7]
    for bit, code in BMS_FAULT_CODES_BYTE7:
        if (b7 >> bit) & 1:
            active.add(code)
    return sorted(active)


# --- shared decoder --------------------------------------------------------

# F100F3 BMS-published SoC: byte 4 raw → percent. Calibrated from two
# direct screen readings (raw=202 at 80%, raw=227 at 90%); slope 0.4,
# intercept -0.8. Saturates at 250 in soc-100-idle.asc.
_SOC_SLOPE = 0.4
_SOC_INTERCEPT = -0.8

# F108 idle frames have no bits set; analyze treats that as skipped_zero
# and stream needs nothing to clear, so the shared decoder handles it as
# a normal "f108" frame with 8 zero-valued byte emissions. Each consumer's
# emit callback decides how to react to a zero byte (analyze suppresses,
# stream stores so the channel goes back to 0).


def _noop_clear(_name: str) -> None:
    pass


def decode(msg, emit, clear=_noop_clear):
    """Walk one CAN frame and dispatch every decoded signal through emit().

    emit(name, value, unit) is called once per signal. Values are raw
    (no rounding) so each caller can round at its own emit. Names follow
    analyze.py's dotted convention (e.g. "pack.voltage_v", "cell.07.voltage_v",
    "bms.state.byte0"). The unit string is informational ("v", "a", "mv",
    "c", "w", "%", "rpm", "") -- the canonical unit of the value matches
    the unit string for that name.

    clear(name) is called when a frame's state machine says a previously-
    valid value is no longer meaningful (charger V/I outside the active
    window, DM1 reverting to idle, BMS->charger cmd going to idle). Each
    caller decides what "clear" means (stream calls Channel.clear(), analyze
    treats it as a no-op since absence-of-row already conveys the same).

    Returns a category string used for per-PGN counters:
      "non_extended", "parse_error",
      "skipped_zero" (all-zero / idle frame in a known PGN),
      "cells", "temps", "f100", "f102", "f104", "f106", "f107", "f108",
      "vc", "motor", "dash", "dm1", "charger", "chgr_cmd",
      or None (extended ID, unrecognized PGN).
    """
    if not getattr(msg, "is_extended_id", True):
        return "non_extended"
    try:
        _, pgn, src = parse_id(msg.arbitration_id)
    except Exception:
        return "parse_error"

    data = data_bytes(msg.data)

    if src == SRC_BMS:
        if PGN_CELL_FIRST <= pgn <= PGN_CELL_LAST:
            if all(b == 0 for b in data):
                return "skipped_zero"
            base = (pgn - PGN_CELL_FIRST) * 4
            for slot in range(4):
                idx = base + slot
                if idx >= NUM_CELLS:
                    continue
                mv = be16(data[2 * slot], data[2 * slot + 1])
                if mv == 0 or mv == 0xFFFF:
                    continue
                emit(f"cell.{idx:02d}.voltage_v", mv / 1000.0, "v")
            return "cells"

        if PGN_TEMP_FIRST <= pgn <= PGN_TEMP_LAST:
            if all(b == 0 for b in data):
                return "skipped_zero"
            base = (pgn - PGN_TEMP_FIRST) * 8
            for slot, b in enumerate(data):
                idx = base + slot
                if idx >= NUM_TEMPS:
                    continue
                if b == 0 or b == 0xFF:
                    continue
                emit(f"temp.{idx:02d}.c", b - TEMP_OFFSET_C, "c")
            return "temps"

        if pgn == PGN_F100:
            if all(b == 0 for b in data):
                return "skipped_zero"
            offset = (PACK_VOLTAGE_OFFSET_LO_V if data[0] == 0x02
                      else PACK_VOLTAGE_OFFSET_HI_V)
            volts = data[1] * PACK_VOLTAGE_LSB_V + offset
            raw_i = be16(data[2], data[3])
            amps = (raw_i - PACK_CURRENT_BIAS_RAW) * PACK_CURRENT_LSB_A
            emit("pack.voltage_v", volts, "v")
            emit("pack.current_raw", raw_i, "")
            emit("pack.current_a", amps, "a")
            emit("pack.power_w", volts * amps, "w")
            soc_pct = data[4] * _SOC_SLOPE + _SOC_INTERCEPT
            emit("pack.soc_raw", data[4], "")
            emit("pack.soc_pct", soc_pct, "%")
            return "f100"

        if pgn == PGN_F102:
            if all(b == 0 for b in data):
                return "skipped_zero"
            max_mv = be16(data[0], data[1])
            min_mv = be16(data[2], data[3])
            if max_mv == 0 or min_mv == 0:
                return None
            emit("pack.cell_max_mv", max_mv, "mv")
            emit("pack.cell_min_mv", min_mv, "mv")
            emit("pack.cell_spread_mv", max_mv - min_mv, "mv")
            emit("pack.cell_max_n", data[4], "")
            emit("pack.cell_min_n", data[5], "")
            emit("pack.cell_spread_mv_reported", data[7], "mv")
            emit("pack.v_estimate",
                 NUM_CELLS * (max_mv + min_mv) / 2.0 / 1000.0, "v")
            return "f102"

        if pgn == PGN_F104:
            if all(b == 0 for b in data):
                return "skipped_zero"
            if data[0] == 0xFF or data[1] == 0xFF:
                return None
            emit("pack.temp_max_c", data[0] - TEMP_OFFSET_C, "c")
            emit("pack.temp_min_c", data[1] - TEMP_OFFSET_C, "c")
            emit("pack.temp_max_n", data[2], "")
            emit("pack.temp_min_n", data[3], "")
            emit("pack.temp_spread_c", data[4], "c")
            return "f104"

        if pgn == PGN_F106:
            if all(b == 0 for b in data):
                return "skipped_zero"
            b0, b1 = data[0], data[1]
            emit("bms.state.byte0", b0, "")
            emit("bms.state.byte1", b1, "")
            emit("bms.state.output_enable", 1 if b0 & 0x01 else 0, "")
            emit("bms.state.main_contactor", 1 if b0 & 0x04 else 0, "")
            emit("bms.state.operating", 1 if b0 & 0x40 else 0, "")
            emit("bms.state.standby", 1 if b0 & 0x80 else 0, "")
            emit("bms.state.charging", 1 if b1 & 0x08 else 0, "")
            emit("bms.state.charger_present", 1 if b1 & 0x04 else 0, "")
            emit("bms.state.drive_mode", 1 if b1 & 0x20 else 0, "")
            emit("bms.state.contactors", 1 if b1 & 0x40 else 0, "")
            return "f106"

        if pgn == PGN_F107:
            if all(b == 0 for b in data):
                return "skipped_zero"
            i_dis = be16(data[0], data[1]) * LIMIT_CURRENT_LSB_A
            i_chg = be16(data[2], data[3]) * LIMIT_CURRENT_LSB_A
            emit("bms.limit.discharge_a", i_dis, "a")
            emit("bms.limit.charge_a", i_chg, "a")
            emit("bms.limit.mode", data[4], "")
            emit("bms.limit.byte5", data[5], "")
            return "f107"

        if pgn == PGN_F108:
            if all(b == 0 for b in data):
                # Idle: still emit zero-valued byte signals so stream's
                # fault_bytes channels follow the bus back to zero.
                for i in range(8):
                    emit(f"bms.fault.byte{i}", 0, "")
                return "skipped_zero"
            for i, b in enumerate(data):
                emit(f"bms.fault.byte{i}", b, "")
            for code in derive_bms_fault_codes(data):
                emit(f"bms.fault.code_{code}", 1, "")
            return "f108"

        return None

    if src == SRC_VEHICLE and pgn == PGN_F100:
        emit("vc.state", data[0], "")
        return "vc"

    if src == SRC_MOTOR and pgn == PGN_FF21:
        rpm_mag = ((data[3] << 8) | data[2]) - RPM_BIAS
        fnr = data[7] & 0x0F
        if fnr == 0x4:
            direction = 1
        elif fnr == 0x8:
            direction = -1
        else:
            direction = 0
        range_gear = ((data[7] >> 4) & 0x0F) + 1
        emit("motor.rpm_signed", direction * rpm_mag, "rpm")
        emit("motor.rpm_magnitude", rpm_mag, "rpm")
        emit("motor.direction", direction, "")
        emit("motor.range_gear", range_gear, "")
        emit("motor.throttle_raw", data[0], "")
        if data[4]:
            emit("motor.controller_temp_c", data[4] - TEMP_OFFSET_C, "c")
        if data[5]:
            emit("motor.motor_temp_c", data[5] - TEMP_OFFSET_C, "c")
        return "motor"

    if src == SRC_DASH and pgn == PGN_FF21:
        emit("dash.alive", data[0], "")
        return "dash"

    if src == SRC_MOTOR and pgn == PGN_FECA:
        # DM1 (Active DTCs). Idle convention: 00 00 00 00 00 00 FF FF.
        lamp_byte = data[0]
        flash_byte = data[1]
        spn = (data[2]
               | (data[3] << 8)
               | (((data[4] >> 5) & 0x07) << 16))
        fmi = data[4] & 0x1F
        cm = (data[5] >> 7) & 0x01
        oc = data[5] & 0x7F
        dtc_active = (spn != 0) or (fmi != 0)
        if lamp_byte == 0 and flash_byte == 0 and not dtc_active:
            # Idle: clear everything so stream's panel reverts to
            # "no active fault" and analyze drops the row.
            clear("dm1.lamp.byte0")
            clear("dm1.lamp.byte1")
            clear("dm1.dtc.spn")
            clear("dm1.dtc.fmi")
            clear("dm1.dtc.cm")
            clear("dm1.dtc.oc")
            return "skipped_zero"
        # Order matches the original analyze emission sequence so the
        # CSV columns appear in the same row-order: byte0, all 4 lamp
        # states, byte1, all 4 lamp flashes, then DTC fields.
        emit("dm1.lamp.byte0", lamp_byte, "")
        for i, lname in enumerate(DM1_LAMP_NAMES):
            shift = 6 - 2 * i
            emit(f"dm1.lamp.{lname}_state", (lamp_byte >> shift) & 0x03, "")
        emit("dm1.lamp.byte1", flash_byte, "")
        for i, lname in enumerate(DM1_LAMP_NAMES):
            shift = 6 - 2 * i
            emit(f"dm1.lamp.{lname}_flash", (flash_byte >> shift) & 0x03, "")
        if dtc_active:
            emit("dm1.dtc.spn", spn, "")
            emit("dm1.dtc.fmi", fmi, "")
            emit("dm1.dtc.cm", cm, "")
            emit("dm1.dtc.oc", oc, "")
        else:
            clear("dm1.dtc.spn")
            clear("dm1.dtc.fmi")
            clear("dm1.dtc.cm")
            clear("dm1.dtc.oc")
        return "dm1"

    if src == SRC_CHARGER and pgn == PGN_FF50:
        if all(b == 0 for b in data):
            return "skipped_zero"
        status = data[0]
        v_raw = le16(data[1], data[2])
        i_raw = le16(data[3], data[4])  # legacy combined u16 (kept for CSV)
        flags = data[4]
        emit("charger.status", status, "")
        emit("charger.v_raw", v_raw, "")
        emit("charger.i_raw", i_raw, "")
        emit("charger.flags", flags, "")
        emit("charger.flag.output_disabled", 1 if flags & 0x04 else 0, "")
        emit("charger.flag.line_ok", 1 if flags & 0x08 else 0, "")
        emit("charger.flag.no_line", 1 if flags & 0x10 else 0, "")
        # V/I are only physically meaningful in the clean active state.
        if status in (0x02, 0x03) and flags == 0x00:
            offset = (PACK_VOLTAGE_OFFSET_LO_V if status == 0x02
                      else CHARGER_V_OFFSET_V)
            emit("charger.voltage_v",
                 v_raw * CHARGER_V_LSB_V + offset, "v")
            emit("charger.current_a", data[3] * CHARGER_I_LSB_A, "a")
        else:
            clear("charger.voltage_v")
            clear("charger.current_a")
        return "charger"

    if src == SRC_BMS_CHGR_IF and pgn == PGN_PROP_0600:
        v_set_raw = be16(data[0], data[1])
        i_set_raw = be16(data[2], data[3])
        enable = data[4]
        idle_pattern = (v_set_raw == 0 and i_set_raw == 0
                        and enable in (0, 1)
                        and all(b == 0xFF for b in data[5:]))
        if idle_pattern and enable == 0:
            # All-zero with enable=0 hasn't been observed; treat as malformed.
            return "skipped_zero"
        # Emission order matches the original analyze sequence: V/I first,
        # then enable, then raw fields. Idle frames clear V/I (stream)
        # and skip them in emit (analyze suppresses the row).
        if idle_pattern:
            clear("chgr_cmd.voltage_v")
            clear("chgr_cmd.current_a")
            emit("chgr_cmd.enable", enable, "")
        else:
            emit("chgr_cmd.voltage_v", v_set_raw * 0.1, "v")
            emit("chgr_cmd.current_a", i_set_raw * 0.1, "a")
            emit("chgr_cmd.enable", enable, "")
            emit("chgr_cmd.v_raw", v_set_raw, "")
            emit("chgr_cmd.i_raw", i_set_raw, "")
        return "chgr_cmd"

    return None
