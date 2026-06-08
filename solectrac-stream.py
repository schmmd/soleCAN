#!/usr/bin/env python3
"""
solectrac-stream.py — Live (or replayed) CAN UI for the
Solectrac CAN bus.

Decodes the same J1939-style frames as solectrac-analyze.py, but
streams from a live CAN interface (or a python-can log file) and
displays a real-time dashboard:

    * Pack voltage estimate, current magnitude, DC and estimated AC power.
    * State-of-charge (SOC) from the BMS-published F100F3 byte.
    * Charger output V / A / power, status flag.
    * Per-cell voltages with min/max/spread (1-based BMS numbering).
    * Per-channel module temperatures (with the +40 C offset removed).
    * Vehicle-controller heartbeat state.
    * Live alerts (low/high cell, spread, temp, AC budget, stale BMS).

Data sources:
    --interface socketcan --channel can0    live SocketCAN bus
    --replay path/to/raw.log                python-can log file replay

Examples:
    # Live capture from SocketCAN
    solectrac-stream.py --interface socketcan --channel can0 --bitrate 250000

    # Replay an existing capture
    solectrac-stream.py --replay session.log

    # Live + raw logging + AC-budget alerts for a 120V/20A circuit
    solectrac-stream.py --interface socketcan --channel can0 \\
        --raw-log out.log --mains-v 120 --breaker-a 20
"""

import argparse
import asyncio
import json
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Deque, List, Optional, Tuple

try:
    import can
except ImportError:
    print("python-can is required: pip install python-can", file=sys.stderr)
    sys.exit(1)

try:
    from rich.console import Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("rich is required: pip install rich", file=sys.stderr)
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
    PACK_VOLTAGE_LSB_V, PACK_VOLTAGE_OFFSET_HI_V, PACK_VOLTAGE_OFFSET_LO_V,
    CHARGER_V_LSB_V, CHARGER_V_OFFSET_HI_V, CHARGER_I_LSB_A,
    RPM_BIAS, LIMIT_CURRENT_LSB_A,
    BMS_FAULT_CODES_BYTE7, BMS_FAULT_CODES_BYTES_0_TO_6,
    parse_id, be16, le16, c_to_f,
    decode as proto_decode,
)


# --- script-local protocol tables -----------------------------------------

DM1_LAMP_STATE = {0: "off", 1: "on", 2: "rsv", 3: "n/a"}
DM1_FLASH_STATE = {0: "-", 1: "1Hz", 2: "2Hz", 3: "n/a"}
# Standard J1939-73 Appendix A FMIs (most common; full list is 0..31).
DM1_FMI_NAMES = {
    0: "above max", 1: "below min", 2: "erratic",
    3: "shorted high", 4: "shorted low", 5: "open circuit",
    6: "shorted ground", 7: "wrong response", 8: "abnormal frequency",
    9: "abnormal update rate", 10: "abnormal change rate", 11: "unknown",
    12: "bad device", 13: "out of cal", 14: "special", 15: "info high (least)",
    16: "info high (mod)", 17: "info low (least)", 18: "info low (mod)",
    19: "data error", 20: "data drift high", 21: "data drift low",
    31: "condition exists",
}

# Vendor BMS error-code table from the CET / Farmtrac 25 G operator
# manual, p.44 ("Error Codes for Controller and Battery"). Authoritative
# source for the human-readable description of each numeric code shown on
# the dashboard's BMS error display.
BMS_FAULT_DESCRIPTIONS: dict = {
    100: "SOC is too high",
    101: "SOC is too low",
    102: "Total voltage is too high",
    103: "Total voltage is too low",
    104: "Charge current fault",
    105: "Discharge current fault",
    106: "Battery temperature is too low",
    107: "Battery temperature is too high",
    108: "Battery under voltage",
    109: "Battery over voltage",
    110: "Battery temperature unbalance",
    111: "Battery voltage unbalance",
    112: "The battery does not match",
    113: "Output pole temperature too high",
    116: "Memory parameter fault",
    117: "Data memory fault",
    118: "Cell voltage detection fault",
    119: "Temperature detection fault",
    120: "Current detection fault",
    121: "Internal total voltage detection fault",
    122: "External total voltage detection fault",
    123: "Insulation monitoring fault",
    124: "Pre-charging fault",
    125: "Internal CAN communication fault",
    126: "Serious insulation fault",
    127: "Slight insulation fault",
    140: "System fault: kvst",
    141: "BMS fault need maintenance",
    142: "BMS fault (manual omits 142)",  # tentative; bit-3 capture observation
    143: "Battery fault need maintenance",
    144: "Battery system fault needs maintenance",
    145: "Needs full charge/discharge maintenance",
    146: "Maintenance mode status",
}

# Motor controller fault codes from the same operator-manual table. Some
# numbers list two distinct conditions; both are kept because the manual
# gives no way to disambiguate them from the code alone.
MC_FAULT_DESCRIPTIONS: dict = {
    12: ["Controller Over Current"],
    13: ["Current Sensor Fault"],
    15: ["Controller Severe Undertemp"],
    16: ["Controller Severe Overtemp"],
    17: ["Severe B+ Undervoltage"],
    18: ["Severe B+ Overvoltage"],
    22: ["Controller Over Temp Cutback"],
    23: ["B+ Undervoltage Cutback"],
    24: ["B+ Overvoltage Cutback"],
    25: ["+5V Supply Failure"],
    26: ["Motor Temp Hot Cutback"],
    29: ["Motor Temp Sensor Fault"],
    31: ["Coil1 Driver Open/Short", "Main Open/Short"],
    32: ["Coil2 Driver Open/Short", "EM Brake Open/Short"],
    36: ["Encoder Fault", "Sin/Cos Sensor Fault"],
    37: ["Motor Open"],
    38: ["Main Contactor Welded"],
    39: ["Main Contactor Did Not Close"],
    41: ["Throttle Wiper High"],
    42: ["Throttle Wiper Low"],
    43: ["Pot2 Wiper High"],
    44: ["Pot2 Wiper Low"],
    45: ["Pot Low Over Current"],
    47: ["HPD/Sequencing Fault"],
    49: ["Parameter Change Fault", "PDO Timeout"],
    71: ["Stall Detected", "Vehicle lock without applying hand brake"],
    83: ["Driver Supply"],
    87: ["Motor Characterization Fault"],
    89: ["Encoder Pulse Count Fault", "Motor Type Fault"],
    92: ["EM Brake failed to set"],
    99: ["Parameter Mismatch"],
}


def describe_bms_code(code: int) -> str:
    """Return the operator-manual description for a BMS code, or 'unknown'."""
    return BMS_FAULT_DESCRIPTIONS.get(int(code), f"unknown code {int(code)}")


def describe_mc_code(code: int) -> str:
    """Return a slash-joined description for a motor-controller code."""
    entries = MC_FAULT_DESCRIPTIONS.get(int(code))
    if not entries:
        return f"unknown code {int(code)}"
    return " / ".join(entries)


# F108 byte 7: bit -> code, 1 bit per code with gaps. Mapping established
# by per-bit injection on 2026-05-10 (see solectrac-inject-f108.py and
# f108-byte7.csv): bit 0 = 140, bits 1,2 silent (dashboard shows nothing),
# bit 3 = 142, bit 4 = 143, bit 5 = 144, bit 6 = 144 (genuine duplicate
# of bit 5, re-verified), bit 7 = 145. Code 146 does NOT appear anywhere
# in F108; the operator's "146" in bms-124-140-142-143-144-146.asc was
# almost certainly a 145 transcription.
#
# Descriptions are looked up from BMS_FAULT_DESCRIPTIONS at render time
# so the operator-manual text remains the single source of truth.
# Throttle pedal scaling for FF21CA byte 0. Raw 0..0xFF; the byte is a
# raw ADC-style reading, not the J1939 SPN 91 0.4 %/bit encoding the
# earlier hypothesis assumed. Maxima observed in the corpus:
#   - asc/full-throttle-*.asc (neutral, no load): raw 0x69 = 105
#   - real-world-on-driving-mowing-off.asc (forward, real load): 0xCC = 204
#   - real-world-on-driving-mowing-off.asc (reverse, real load): 0x96 = 150
#   - driving-2800rpm-highgear-loader.asc (forward, top speed in high
#     gear, real load): 0xFF = 255  (full-scale ground truth)
# The forward/reverse asymmetry on the same pedal hardware (0xFF vs
# 0x96) points to a controller-side reverse-speed limiter applied
# before the byte goes on the wire. Idle offset ~3 (sensor noise with
# foot off); controller dead-low ~14 (below this, motor RPM stays at 0;
# matches the Kelly TPS_dead_low concept from the hydraulic pump doc).
THROTTLE_DEAD_LOW = 3                          # idle resting offset (subtracted from raw)
THROTTLE_PCT_PER_BIT = 100.0 / (0xFF - THROTTLE_DEAD_LOW)  # raw 0xFF = 100%
# Motor RPM -> ground speed coefficients per range, calibrated for the
# Turf/Industrial tire option (23x8.5-12 front, 33x13.5-16.5 rear).
# Source: CET Operator Manual page 34 travel-speed table
# (https://solectracsupport.com/FT25GUSAOPM.pdf), at the max-RPM column:
# Low 5.7 km/h @ 2800 RPM, Medium 8.6 km/h @ 2800 RPM, High 17.0 km/h @
# 2800 RPM. Relationship is linear in motor RPM within each range. The
# S/N/F switch is a motor-RPM cap, not a gear stage, and does not affect
# this calibration. The Agri tire option uses different coefficients
# (5x12 / 8.0x18) — swap in 4.6/8.8/17.5 if those tires are fitted.
KMH_PER_RPM_HIGH_TURF = {
    1: 5.7 / 2800,    # Low (range gear "L")
    2: 8.6 / 2800,    # Medium (range gear "M")
    3: 17.0 / 2800,   # High (range gear "H")
}
KMH_TO_MPH = 0.6213712

# Charger status byte (FF50CA byte 0). It serves a dual purpose:
# it indicates that the OBC is actively delivering charge AND selects the
# pack-V encoding offset for FF50 bytes 1-2 (same low/high variant scheme
# as F100F3 byte 0).
#   0x00 = idle (charger module powered, not charging)
#   0x01 = transient handshake (only seen briefly during wake-up)
#   0x02 = active charging, LO offset: pack V = raw × 0.1 + 51.2 V
#          (covers 51.2..76.7 V — most of an L1 / L2 charge sits here)
#   0x03 = active charging, HI offset: pack V = raw × 0.1 + 76.8 V
#          (covers 76.8..102.3 V — the top end of charge)
CHGR_STATUS_IDLE = 0x00
CHGR_STATUS_HANDSHAKE = 0x01
CHGR_STATUS_LO = 0x02
CHGR_STATUS_HI = 0x03
CHGR_ACTIVE_STATES = {CHGR_STATUS_LO, CHGR_STATUS_HI}

STALE_S = 2.0  # mark a channel stale if no update for this long

# Current threshold above which the pack is considered actively drawn (gates
# the runtime-to-empty estimate so a parked tractor doesn't show an ETA).
PACK_DRAW_CURRENT_A = 2.0

# Time-to-full estimator: retain (ts, soc%) samples for SOC_ETA_HISTORY_S,
# slope over the most recent SOC_ETA_WINDOW_S when SOC is rising in that
# window, and fall back to the slope across all retained history when
# the window lands entirely inside a plateau (the BMS publishes SOC in
# 0.4%/count steps that can hold for >1000 s in CV taper). Until the
# deque has seen SOC_ETA_STABLE_TRANSITIONS distinct values, the ETA is
# tagged "(rough)" because a one- or two-transition slope can be off by
# ~100%. Linear extrapolation is also optimistic near 100% SOC.
SOC_ETA_HISTORY_S = 7200.0          # retain up to 2 h of SOC samples
SOC_ETA_WINDOW_S = 1800.0           # preferred slope window (30 min)
SOC_ETA_MIN_SPAN_S = 30.0           # need at least this much data first
SOC_ETA_STABLE_TRANSITIONS = 3      # transitions before dropping "(rough)"

# Pack-power sparkline: keep the last POWER_HISTORY_S of (ts, W) samples
# and bucket them into POWER_SPARK_WIDTH columns at render time. F100F3
# arrives at ~10 Hz so 60 s gives ~600 samples; bucketing averages them.
POWER_HISTORY_S = 60.0
POWER_SPARK_WIDTH = 30


# --- state store ------------------------------------------------------------

@dataclass
class Channel:
    """A single decoded value with the time it was last updated."""
    value: Optional[float] = None
    ts: Optional[float] = None  # time.monotonic() of last update

    def update(self, value: float, now: float) -> None:
        self.value = value
        self.ts = now

    def clear(self) -> None:
        self.value = None
        self.ts = None

    def is_stale(self, now: float) -> bool:
        return self.ts is None or (now - self.ts) > STALE_S


@dataclass
class State:
    # pack-level
    # pack_v_terminal: from F100F3 byte 1 (the BMS-published terminal
    # voltage; load-sensitive, anchored against 24-capture regression per
    # NOTES). Authoritative when present.
    # pack_v_est: from F102 cell-min/max mean (20 * (max+min)/2). Cheap
    # cross-check / fallback before any F100 frame has been seen.
    pack_v_terminal: Channel = field(default_factory=Channel)
    pack_v_est: Channel = field(default_factory=Channel)
    pack_i_a: Channel = field(default_factory=Channel)
    # Derived: instantaneous pack power (V*I, signed). + draw / - charge.
    # Session-cumulative energy in Wh, integrated trapezoidally between
    # successive F100F3 frames; gated against gaps > 5 s (likely bus
    # dropouts) so we don't smear arbitrary power across the gap. The
    # last_energy_ts tracks the timestamp / power of the previous F100F3
    # frame for the trapezoidal step.
    pack_power_w: Channel = field(default_factory=Channel)
    # Recent (ts, pack_power_w) samples for the sparkline. Pruned to
    # POWER_HISTORY_S so the panel always shows the last minute.
    power_history: Deque[Tuple[float, float]] = field(default_factory=deque)
    energy_wh_drawn: float = 0.0
    energy_wh_charged: float = 0.0
    last_energy_ts: Optional[float] = None
    last_energy_p: Optional[float] = None
    # charger
    chgr_v: Channel = field(default_factory=Channel)
    chgr_i: Channel = field(default_factory=Channel)
    chgr_status: Channel = field(default_factory=Channel)
    # vehicle controller
    vc_state_raw: Channel = field(default_factory=Channel)
    # motor controller (FF21CA)
    motor_rpm: Channel = field(default_factory=Channel)        # signed (dir * |rpm|)
    motor_rpm_mag: Channel = field(default_factory=Channel)    # |rpm| magnitude
    motor_throttle: Channel = field(default_factory=Channel)
    motor_direction: Channel = field(default_factory=Channel)  # -1 R / 0 N / +1 F (byte 7 low nibble)
    motor_range_gear: Channel = field(default_factory=Channel) # 1..3 (byte 7 high nibble)
    # FF21CA bytes 4 and 5 are both J1939 +40 C-offset temps; byte 4 is
    # the main controller (consistently warmer, ramps from cold-start) and
    # byte 5 is the motor housing.
    controller_temp_c: Channel = field(default_factory=Channel)
    motor_temp_c: Channel = field(default_factory=Channel)
    # F102 cell summary
    max_cell_mv: Channel = field(default_factory=Channel)
    min_cell_mv: Channel = field(default_factory=Channel)
    spread_mv: Channel = field(default_factory=Channel)
    max_cell_n: Channel = field(default_factory=Channel)  # 1-based per BMS
    min_cell_n: Channel = field(default_factory=Channel)  # 1-based per BMS
    # F104 temp summary (symmetric with F102)
    temp_max_c: Channel = field(default_factory=Channel)
    temp_min_c: Channel = field(default_factory=Channel)
    temp_spread_c: Channel = field(default_factory=Channel)
    temp_max_n: Channel = field(default_factory=Channel)  # 1-based per BMS
    temp_min_n: Channel = field(default_factory=Channel)  # 1-based per BMS
    # F106 BMS state bitmap
    bms_state_byte0: Channel = field(default_factory=Channel)
    bms_state_byte1: Channel = field(default_factory=Channel)
    bms_output_enable: Channel = field(default_factory=Channel)    # b0 bit 0
    bms_main_contactor: Channel = field(default_factory=Channel)   # b0 bit 2
    bms_operating: Channel = field(default_factory=Channel)        # b0 bit 6
    bms_standby: Channel = field(default_factory=Channel)          # b0 bit 7
    bms_charging: Channel = field(default_factory=Channel)         # b1 bit 3
    bms_charger_present: Channel = field(default_factory=Channel)  # b1 bit 2
    bms_drive_mode: Channel = field(default_factory=Channel)       # b1 bit 5
    bms_contactors: Channel = field(default_factory=Channel)       # b1 bit 6
    # F107 BMS current limits
    limit_discharge_a: Channel = field(default_factory=Channel)
    limit_charge_a: Channel = field(default_factory=Channel)
    limit_mode: Channel = field(default_factory=Channel)           # 0 chg / 1 drv
    # BMS-published SOC (F100F3 byte 4): % = raw × 0.4 − 0.8.
    bms_soc_pct: Channel = field(default_factory=Channel)
    # Recent (ts, bms_soc%) samples used to estimate time-to-full during
    # charging. Pruned to SOC_ETA_HISTORY_S; the estimator prefers slope
    # over the SOC_ETA_WINDOW_S window and falls back to the full
    # retained history when the window lands inside a SOC plateau.
    soc_history: Deque[Tuple[float, float]] = field(default_factory=deque)
    # F108 fault bitmap bytes. Bytes 0..6 carry vendor codes 100..127 at
    # 2 bits per code (4 codes per byte; code = 100 + 4*byte + pair_index
    # over bit pairs (0,1)(2,3)(4,5)(6,7)); see active_bms_faults. Byte 7
    # is the system/maintenance code bitmap (1 bit per code, decoded
    # against BMS_FAULT_CODES_BYTE7). All 8 bytes are tracked so the TUI
    # can show raw bitmap state alongside decoded codes.
    fault_bytes: List[Channel] = field(
        default_factory=lambda: [Channel() for _ in range(8)]
    )
    # DM1 (J1939-73 Active DTCs) from motor ECU 0xCA. We track raw
    # lamp/flash bytes and the most recent active DTC fields. Cleared
    # back to None when an idle frame (00 00 00 00 00 00 FF FF) arrives,
    # so the panel reflects "presently inactive" instead of "last fault
    # ever observed".
    dm1_lamp_byte: Channel = field(default_factory=Channel)
    dm1_flash_byte: Channel = field(default_factory=Channel)
    dm1_spn: Channel = field(default_factory=Channel)
    dm1_fmi: Channel = field(default_factory=Channel)
    dm1_cm: Channel = field(default_factory=Channel)
    dm1_oc: Channel = field(default_factory=Channel)
    # 1806E5F4: BMS-to-charger command. The BMS-side address 0xF4
    # publishes voltage and current setpoints (and an enable flag) to
    # the charger at 0xE5. Idle frames clear voltage/current to None
    # while keeping enable visible, so the panel mirrors charger state
    # rather than freezing on the last active setpoint.
    chgr_cmd_v_v: Channel = field(default_factory=Channel)
    chgr_cmd_i_a: Channel = field(default_factory=Channel)
    chgr_cmd_enable: Channel = field(default_factory=Channel)
    # 0x18FF2112: dashboard / instrument-cluster heartbeat at 10 Hz.
    # byte 0 = alive flag (0 during ~700 ms boot, 1 thereafter); other
    # bytes always zero. Useful as a liveness check: if this Channel
    # goes stale the dashboard ECU has likely dropped off the bus.
    dash_alive: Channel = field(default_factory=Channel)
    # per-cell / per-temp arrays (indexed 0-based; display is 1-based)
    cells: List[Channel] = field(
        default_factory=lambda: [Channel() for _ in range(NUM_CELLS)]
    )
    temps: List[Channel] = field(
        default_factory=lambda: [Channel() for _ in range(NUM_TEMPS)]
    )
    # session counters
    frames: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.monotonic)
    # time.monotonic() of the most recent frame seen by decode(). Powers
    # the dashboard's "last frame age" footer in --ui web.
    last_frame_ts: Optional[float] = None


# --- helpers ----------------------------------------------------------------

def primary_pack_v(state: State) -> Channel:
    """Return the more authoritative pack-voltage Channel: the F100F3
    BMS-published terminal voltage when present, else the F102-derived
    cell-mean estimate. Both are kept in state so a fallback exists
    before the first F100 frame arrives.
    """
    if state.pack_v_terminal.value is not None:
        return state.pack_v_terminal
    return state.pack_v_est


# --- decoder ----------------------------------------------------------------

import re

# --- decoder routing -------------------------------------------------------

# Canonical signal name (as emitted by solectrac_proto.decode) → State
# attribute that owns the Channel. Unknown names are silently ignored so
# stream is forward-compatible with signals analyze.py emits but stream
# doesn't yet track (e.g. pack.current_raw).
_NAME_TO_ATTR = {
    # F100 / F102 pack-level scalars
    "pack.voltage_v": "pack_v_terminal",
    "pack.current_a": "pack_i_a",
    "pack.power_w": "pack_power_w",
    "pack.soc_pct": "bms_soc_pct",
    "pack.v_estimate": "pack_v_est",
    "pack.cell_max_mv": "max_cell_mv",
    "pack.cell_min_mv": "min_cell_mv",
    "pack.cell_spread_mv": "spread_mv",
    "pack.cell_max_n": "max_cell_n",
    "pack.cell_min_n": "min_cell_n",
    # F104 temp summary
    "pack.temp_max_c": "temp_max_c",
    "pack.temp_min_c": "temp_min_c",
    "pack.temp_max_n": "temp_max_n",
    "pack.temp_min_n": "temp_min_n",
    "pack.temp_spread_c": "temp_spread_c",
    # F106 BMS state
    "bms.state.byte0": "bms_state_byte0",
    "bms.state.byte1": "bms_state_byte1",
    "bms.state.output_enable": "bms_output_enable",
    "bms.state.main_contactor": "bms_main_contactor",
    "bms.state.operating": "bms_operating",
    "bms.state.standby": "bms_standby",
    "bms.state.charging": "bms_charging",
    "bms.state.charger_present": "bms_charger_present",
    "bms.state.drive_mode": "bms_drive_mode",
    "bms.state.contactors": "bms_contactors",
    # F107 BMS limits
    "bms.limit.discharge_a": "limit_discharge_a",
    "bms.limit.charge_a": "limit_charge_a",
    "bms.limit.mode": "limit_mode",
    # FF50 charger
    "charger.status": "chgr_status",
    "charger.voltage_v": "chgr_v",
    "charger.current_a": "chgr_i",
    # 0600 BMS→Charger command
    "chgr_cmd.enable": "chgr_cmd_enable",
    "chgr_cmd.voltage_v": "chgr_cmd_v_v",
    "chgr_cmd.current_a": "chgr_cmd_i_a",
    # Vehicle controller heartbeat
    "vc.state": "vc_state_raw",
    # FF21 motor telemetry
    "motor.rpm_signed": "motor_rpm",
    "motor.rpm_magnitude": "motor_rpm_mag",
    "motor.direction": "motor_direction",
    "motor.range_gear": "motor_range_gear",
    "motor.throttle_raw": "motor_throttle",
    "motor.controller_temp_c": "controller_temp_c",
    "motor.motor_temp_c": "motor_temp_c",
    # FF21 dashboard heartbeat
    "dash.alive": "dash_alive",
    # FECA DM1
    "dm1.lamp.byte0": "dm1_lamp_byte",
    "dm1.lamp.byte1": "dm1_flash_byte",
    "dm1.dtc.spn": "dm1_spn",
    "dm1.dtc.fmi": "dm1_fmi",
    "dm1.dtc.cm": "dm1_cm",
    "dm1.dtc.oc": "dm1_oc",
}

_CELL_NAME = re.compile(r"^cell\.(\d{2})\.voltage_v$")
_TEMP_NAME = re.compile(r"^temp\.(\d{2})\.c$")
_FAULT_BYTE_NAME = re.compile(r"^bms\.fault\.byte(\d)$")


def decode(msg: "can.Message", state: State, now: float) -> None:
    """Update state from a single CAN frame.

    The byte-level decode lives in solectrac_proto.decode(); this wrapper
    routes emit(name, value, unit) calls to the matching State Channel
    (with volts→mV conversion for cells), tracks derived state
    (power_history + trapezoidal Wh integration on pack.power_w,
    soc_history on pack.soc_pct), and dispatches clear(name) for the
    state-machine signals (charger V/I outside the active window, DM1
    idle, chgr_cmd idle).
    """
    state.frames += 1
    state.last_frame_ts = now

    def emit(name, value, unit):
        # Hot-path indexed names (per-cell, per-temp, per-fault-byte).
        m = _CELL_NAME.match(name)
        if m:
            idx = int(m.group(1))
            if idx < NUM_CELLS:
                # Shared decoder emits volts; stream stores mV ints so the
                # existing alert thresholds and display formatting work.
                state.cells[idx].update(int(round(value * 1000)), now)
            return
        m = _TEMP_NAME.match(name)
        if m:
            idx = int(m.group(1))
            if idx < NUM_TEMPS:
                state.temps[idx].update(int(value), now)
            return
        m = _FAULT_BYTE_NAME.match(name)
        if m:
            state.fault_bytes[int(m.group(1))].update(int(value), now)
            return
        # Static mapping covers everything else stream tracks. Unknown
        # names (analyze-only signals like pack.current_raw,
        # bms.fault.code_NNN) are silently ignored.
        attr = _NAME_TO_ATTR.get(name)
        if attr is None:
            return
        getattr(state, attr).update(value, now)
        # Derived state needs the just-updated value.
        if name == "pack.power_w":
            state.power_history.append((now, value))
            p_cutoff = now - POWER_HISTORY_S
            while (state.power_history
                   and state.power_history[0][0] < p_cutoff):
                state.power_history.popleft()
            # Trapezoidal Wh integration over the gap to the previous
            # F100F3 frame. Gap > 5 s is treated as a bus dropout and
            # skipped (we don't know what was happening in between).
            if (state.last_energy_ts is not None
                    and state.last_energy_p is not None):
                dt = now - state.last_energy_ts
                if 0.0 < dt <= 5.0:
                    p0, p1 = state.last_energy_p, value
                    avg_pos = (max(p0, 0.0) + max(p1, 0.0)) / 2.0
                    avg_neg = (min(p0, 0.0) + min(p1, 0.0)) / 2.0
                    state.energy_wh_drawn += avg_pos * dt / 3600.0
                    state.energy_wh_charged += -avg_neg * dt / 3600.0
            state.last_energy_ts = now
            state.last_energy_p = value
        elif name == "pack.soc_pct":
            state.soc_history.append((now, value))
            cutoff = now - SOC_ETA_HISTORY_S
            while (state.soc_history
                   and state.soc_history[0][0] < cutoff):
                state.soc_history.popleft()

    def clear_chan(name):
        attr = _NAME_TO_ATTR.get(name)
        if attr is None:
            return
        getattr(state, attr).clear()

    category = proto_decode(msg, emit, clear_chan)
    if category == "parse_error":
        state.errors += 1


# --- BMS faults -------------------------------------------------------------

def active_bms_faults(state: State) -> List[Tuple[int, str]]:
    """Return [(code_number, description), ...] for currently active codes
    in F108. Bytes 0..6 are decoded per BMS_FAULT_CODES_BYTES_0_TO_6
    (mixed 2-bit/1-bit encoding by byte); byte 7 is decoded per
    BMS_FAULT_CODES_BYTE7 (1 bit per code with gaps; bits 5 and 6 both
    = 144).

    Descriptions come from the operator-manual BMS_FAULT_DESCRIPTIONS
    table.
    """
    active: set = set()
    for byte_idx, codes in BMS_FAULT_CODES_BYTES_0_TO_6.items():
        b = state.fault_bytes[byte_idx].value
        if b is None:
            continue
        b = int(b)
        for bit_idx, code in enumerate(codes):
            if code is None:
                continue
            if (b >> bit_idx) & 1:
                active.add(code)
    b7 = state.fault_bytes[7].value
    if b7 is not None:
        b7 = int(b7)
        for bit, code in BMS_FAULT_CODES_BYTE7:
            if (b7 >> bit) & 1:
                active.add(code)
    return [(code, describe_bms_code(code)) for code in sorted(active)]


# --- alerts -----------------------------------------------------------------

def evaluate_alerts(state: State, mains_v: float, breaker_a: float,
                    efficiency: float, now: float) -> List[Tuple[str, str]]:
    alerts: List[Tuple[str, str]] = []

    for i, c in enumerate(state.cells):
        if c.value is None:
            continue
        mv = c.value
        if mv < 3000:
            alerts.append(("CRIT", f"cell #{i + 1} below 3.00 V "
                                   f"({mv / 1000:.3f} V)"))
        elif mv < 3300:
            alerts.append(("WARN", f"cell #{i + 1} below 3.30 V "
                                   f"({mv / 1000:.3f} V)"))
        if mv > 4200:
            alerts.append(("CRIT", f"cell #{i + 1} above 4.20 V "
                                   f"({mv / 1000:.3f} V)"))

    if state.spread_mv.value is not None and state.spread_mv.value > 100:
        alerts.append(("WARN", f"cell spread {int(state.spread_mv.value)} mV "
                               f"> 100 mV"))

    for i, t in enumerate(state.temps):
        if t.value is None:
            continue
        if t.value > 55:
            alerts.append(("CRIT",
                           f"T{i} = {t.value} °C ({c_to_f(t.value):.0f} °F)"
                           f" > 55 °C"))

    temp_vals = [t.value for t in state.temps if t.value is not None]
    if len(temp_vals) >= 2:
        delta = max(temp_vals) - min(temp_vals)
        if delta > 10:
            alerts.append(("WARN",
                           f"temp delta {delta} °C ({delta * 9 / 5:.0f} °F)"
                           f" > 10 °C"))

    # AC-supply budget (only meaningful while actively charging — both CC
    # (0x02) and CV (0x03); 0x01 is a transient handshake that doesn't draw
    # breaker power).
    chgr_active = (state.chgr_status.value in CHGR_ACTIVE_STATES
                   and not state.chgr_status.is_stale(now))
    pack_v = primary_pack_v(state)
    if (chgr_active and pack_v.value
            and state.pack_i_a.value is not None
            and state.pack_i_a.value < 0):
        dc_w = pack_v.value * -state.pack_i_a.value
        ac_w = dc_w / max(efficiency, 0.01)
        ac_a = ac_w / max(mains_v, 1.0)
        if ac_a > 0.8 * breaker_a:
            alerts.append(("WARN",
                           f"est AC draw {ac_a:.1f} A > 80% of "
                           f"{breaker_a:.0f} A breaker"))

    # Stale BMS heartbeat while VC says we're awake.
    if (state.frames > 100
            and state.vc_state_raw.value == 0x0C
            and state.pack_i_a.is_stale(now)):
        alerts.append(("CRIT", "no F100 frame from BMS in > 2 s"))

    # Active BMS warning codes (F108 byte 7).
    faults = active_bms_faults(state)
    if faults:
        items = ", ".join(f"{c} ({d})" for c, d in faults)
        alerts.append(("WARN", f"BMS reports active fault codes: {items}"))

    return alerts


# --- TUI rendering ----------------------------------------------------------

# Sparkline glyphs all grow from the bottom; sign is communicated by
# colour (green = charging, default = drawing) since unicode block
# elements don't have a clean symmetric set that grows above and below
# a baseline.
_SPARK_LEVELS = " ▁▂▃▄▅▆▇█"   # 0 = baseline tick, 8 = max magnitude


def power_sparkline(state: State, width: int = POWER_SPARK_WIDTH) -> Text:
    """Return a coloured unicode sparkline of recent pack power.

    Buckets samples into `width` columns by timestamp. Bar height encodes
    |power| scaled to the window's peak; colour encodes sign (green =
    charging, default = drawing). Empty buckets render as a dim tick.
    """
    samples = list(state.power_history)
    if not samples:
        return Text("─" * width, style="dim")

    t0 = samples[0][0]
    t1 = samples[-1][0]
    span = max(t1 - t0, 1e-6)
    buckets: List[List[float]] = [[] for _ in range(width)]
    for ts, p in samples:
        idx = int((ts - t0) / span * width)
        if idx >= width:
            idx = width - 1
        buckets[idx].append(p)

    means = [sum(b) / len(b) if b else None for b in buckets]
    abs_max = max((abs(m) for m in means if m is not None), default=0.0)
    if abs_max < 1.0:
        abs_max = 1.0  # avoid divide-by-zero / amplifying noise

    out = Text()
    last: Optional[float] = None
    for m in means:
        if m is None:
            # Carry the previous bucket value across short gaps so the
            # line doesn't look chopped up; render a dim tick when there
            # is no prior sample either.
            if last is None:
                out.append("─", style="dim")
                continue
            m = last
        last = m
        level = min(8, int(round(abs(m) / abs_max * 8)))
        ch = _SPARK_LEVELS[level]
        if level == 0:
            out.append(ch, style="dim")
        elif m >= 0:
            # m comes from pack.power_w (J1939 convention, positive = drawing).
            out.append(ch)
        else:
            out.append(ch, style="green")
    return out


# F106 flag display order. Tuples of (channel attribute, short label,
# style-when-active). Mode-style flags (operating/standby/charging/drive)
# get bold so they read as "what the BMS is currently doing"; the rest
# get plain green for "yes, this is on". Ordered to keep mutually
# exclusive primary-mode pills (op / stby / chg) adjacent.
_BMS_FLAGS: List[Tuple[str, str, str]] = [
    ("bms_main_contactor", "MC",     "bold green"),
    ("bms_contactors",     "ctct",   "green"),
    ("bms_output_enable",  "out",    "green"),
    ("bms_operating",      "OPER",   "bold green"),
    ("bms_standby",        "STBY",   "bold cyan"),
    ("bms_charging",       "CHG",    "bold green"),
    ("bms_drive_mode",     "DRIVE",  "bold green"),
    ("bms_charger_present", "chgr",  "green"),
]


def bms_flags_pills(state: State) -> Text:
    """Compact pill row for the eight F106 BMS state flags.

    Each pill is the abbreviated flag name; bright when the flag is set,
    dim when clear. Pills are separated by a thin '·' so the row reads
    as one line of state at a glance.
    """
    out = Text()
    sep = Text(" ")  # single space; pill colours give the visual break
    for i, (attr, label, style) in enumerate(_BMS_FLAGS):
        if i > 0:
            out.append_text(sep)
        ch: Channel = getattr(state, attr)
        v = ch.value
        if v is None:
            out.append(label, style="dim")
        elif int(v):
            out.append(label, style=style)
        else:
            out.append(label, style="dim")
    return out


def fmt(c: Channel, fmt_spec: str = "{:.2f}", unit: str = "",
        now: Optional[float] = None) -> Text:
    if c.value is None:
        return Text("---", style="dim")
    text = fmt_spec.format(c.value)
    if unit:
        text += f" {unit}"
    if now is not None and c.is_stale(now):
        return Text(text, style="yellow dim")
    return Text(text)


def render_header(state: State, now: float, mode: str = "live") -> Panel:
    uptime = now - state.started_at
    h = int(uptime // 3600)
    m = int((uptime % 3600) // 60)
    s = int(uptime % 60)
    rate = state.frames / uptime if uptime > 0 else 0.0
    line = (f"Up: {h:02d}:{m:02d}:{s:02d}    "
            f"Frames: {state.frames:,}    "
            f"Rate: {rate:.0f} fps    "
            f"Errors: {state.errors}")
    label = "LIVE" if mode == "live" else "REPLAY"
    return Panel(Text(line), title=f"Solectrac — {label}",
                 border_style="cyan")


def render_pack(state: State, mains_v: float, efficiency: float,
                now: float) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(justify="left")
    t.add_column(justify="left")

    pack_v_ch = primary_pack_v(state)
    t.add_row("voltage", fmt(pack_v_ch, "{:.2f}", "V", now))

    pi = state.pack_i_a.value
    chgr_active = (state.chgr_status.value in CHGR_ACTIVE_STATES
                   and not state.chgr_status.is_stale(now))
    if pi is None:
        i_text = Text("---", style="dim")
    else:
        # Underlying state.pack_i_a uses the J1939 convention (positive =
        # drawing from pack). Flip for display so positive = charging.
        # Green when charging in (positive), default style when drawing.
        i_disp = -pi
        if i_disp > 0.05:
            i_text = Text(f"+{i_disp:.1f} A (charging)", style="green")
        elif i_disp < -0.05:
            i_text = Text(f"{i_disp:.1f} A (drawing)")
        else:
            i_text = Text(f"{i_disp:+.1f} A")
    t.add_row("current", i_text)

    # F107 BMS current limit headroom. Pick the relevant limit from the
    # sign of pack current: discharge limit when drawing, charge limit
    # when charging. ideas.txt: "you're at 18 A of a 200 A budget".
    if pi is not None:
        if pi >= 0 and state.limit_discharge_a.value is not None:
            limit_ch = state.limit_discharge_a
            kind = "discharge"
            used = pi
        elif pi < 0 and state.limit_charge_a.value is not None:
            limit_ch = state.limit_charge_a
            kind = "charge"
            used = -pi
        else:
            limit_ch = None
            kind = None
            used = 0.0
        limit = limit_ch.value if limit_ch is not None else None
        if limit is not None and limit > 0:
            frac = max(0.0, used / limit)
            bar_w = 14
            filled = min(bar_w, int(round(frac * bar_w)))
            bar = Text("█" * filled + "░" * (bar_w - filled))
            if frac >= 0.9:
                style = "bold red"
            elif frac >= 0.7:
                style = "yellow"
            else:
                style = None
            tag = " (stale)" if limit_ch.is_stale(now) else ""
            short = "dis" if kind == "discharge" else "chg"
            t.add_row(
                "limit",
                Text.assemble(
                    bar,
                    Text(f"  {used:>5.1f}/{limit:.0f}A {short}"
                         f"  {frac * 100:>3.0f}%{tag}",
                         style=style),
                ),
            )

    if pack_v_ch.value is not None and pi is not None:
        # Display convention: positive = power into the pack (charging),
        # negative = power out of the pack (drawing). Underlying pi is
        # J1939 (positive = drawing), so flip for the display.
        dc_w = pack_v_ch.value * -pi
        p_style = "green" if dc_w > 0 else None
        t.add_row("power", Text(f"{dc_w:+.0f} W", style=p_style))
        if chgr_active and dc_w > 0:
            ac_w = dc_w / max(efficiency, 0.01)
            ac_a = ac_w / max(mains_v, 1.0)
            t.add_row("AC est",
                      Text(f"+{ac_w:.0f} W  ({ac_a:.1f} A @ {mains_v:.0f} V, "
                           f"{efficiency * 100:.0f}% eff)",
                           style="green"))

    # 60 s sparkline of pack power. Glyphs above the baseline (red) =
    # drawing, below (green) = charging. Empty until the first F100F3.
    if state.power_history:
        spark = power_sparkline(state)
        t.add_row(
            "trend",
            Text.assemble(
                spark,
                Text(f"  last {int(POWER_HISTORY_S)}s", style="dim"),
            ),
        )

    # F106 BMS state flags. Eight booleans decoded from byte 0/1; we
    # render them as pills so the operator can see at a glance whether
    # the contactor is closed, what mode the BMS is in, and whether the
    # charger is present. Bright = set, dim = clear, "?" = no F106 yet.
    if state.bms_state_byte0.value is not None:
        flags_row = bms_flags_pills(state)
        t.add_row("flags", flags_row)

    # Session-cumulative energy. Integrated across F100F3 frames since
    # stream start. Net is positive when the session has charged more
    # than it drew (energy into pack).
    wh_out = state.energy_wh_drawn
    wh_in = state.energy_wh_charged
    if wh_out > 0.5 or wh_in > 0.5:
        t.add_row("", "")
        t.add_row("session draw", Text(f"{wh_out:.0f} Wh"))
        t.add_row("session charge", Text(f"{wh_in:.0f} Wh", style="green"))
        net = wh_in - wh_out
        net_style = "green" if net > 0 else None
        t.add_row("session net",
                  Text(f"{net:+.0f} Wh  "
                       f"({net / PACK_CAPACITY_WH * 100:+.1f}% of pack)",
                       style=net_style))

    if state.bms_soc_pct.value is not None:
        soc = state.bms_soc_pct.value
        bar_w = 20
        filled = int(round(soc * bar_w / 100))
        bar = Text("█" * filled + "░" * (bar_w - filled))
        if soc < 15:
            soc_style = "bold red"
        elif soc < 30:
            soc_style = "yellow"
        else:
            soc_style = "green"
        tag = " stale" if state.bms_soc_pct.is_stale(now) else ""
        t.add_row("SOC", Text.assemble(
            bar,
            Text(f"  {soc:>3.0f}%", style=soc_style),
            Text(tag, style="dim"),
        ))
        kwh_remaining = soc / 100.0 * PACK_CAPACITY_WH / 1000.0
        kwh_total = PACK_CAPACITY_WH / 1000.0
        t.add_row("remaining", Text(f"{kwh_remaining:.1f} / {kwh_total:.1f} kWh"))

    # Runtime-to-empty estimate. Symmetric with the charger panel's
    # ETA-to-100%: same soc_history slope, opposite sign. Only shown
    # when the pack is actually being drawn from -- a parked tractor
    # with zero load would otherwise show a "(rough)" never-ending ETA.
    if (state.bms_soc_pct.value is not None
            and pi is not None and pi > PACK_DRAW_CURRENT_A):
        eta = estimate_drain_eta_s(state)
        if eta is None:
            t.add_row("ETA to 0%", Text("estimating...", style="dim"))
        elif count_soc_transitions(state) < SOC_ETA_STABLE_TRANSITIONS:
            t.add_row("ETA to 0%",
                      Text(f"{format_eta(eta)} (rough)", style="yellow"))
        else:
            t.add_row("ETA to 0%", Text(format_eta(eta)))

    return Panel(t, title="Pack", border_style="green")


def count_soc_transitions(state: State) -> int:
    """Number of times the BMS SOC value changed in the retained history.

    Used to gate the "(rough)" tag on the ETA: with only one or two
    transitions a single quantization step dominates the slope, so the
    estimate can be off by ~100%.
    """
    n = 0
    last: Optional[float] = None
    for _ts, sc in state.soc_history:
        if last is not None and sc != last:
            n += 1
        last = sc
    return n


def _estimate_soc_eta_s(state: State, target: float,
                        rising: bool) -> Optional[float]:
    """Generic SOC slope-extrapolation ETA used by the charge-to-100% and
    drain-to-0% estimators.

    Prefers the slope across the most recent SOC_ETA_WINDOW_S so the ETA
    tracks the current phase. Falls back to the full retained history
    when the window lands entirely inside a SOC plateau (the BMS holds
    each 0.385%/count step for >1000 s in CV taper). Returns None when
    SOC isn't moving in the requested direction.
    """
    samples = state.soc_history
    if len(samples) < 2:
        return None
    t1, s1 = samples[-1]
    if rising and s1 >= target:
        return 0.0
    if (not rising) and s1 <= target:
        return 0.0

    def _slope_eta(t0: float, s0: float) -> Optional[float]:
        span = t1 - t0
        if span < SOC_ETA_MIN_SPAN_S:
            return None
        rate = (s1 - s0) / span  # %/s, signed
        if rising and rate <= 0:
            return None
        if (not rising) and rate >= 0:
            return None
        return (target - s1) / rate

    cutoff = t1 - SOC_ETA_WINDOW_S
    for ts, sc in samples:
        if ts >= cutoff:
            eta = _slope_eta(ts, sc)
            if eta is not None:
                return eta
            break
    return _slope_eta(samples[0][0], samples[0][1])


def estimate_charge_eta_s(state: State) -> Optional[float]:
    """Seconds until BMS SOC reaches 100%, or None if not rising.

    Linear extrapolation is optimistic in the last ~10% because charge
    current tapers in CV.
    """
    return _estimate_soc_eta_s(state, target=100.0, rising=True)


def estimate_drain_eta_s(state: State) -> Optional[float]:
    """Seconds until BMS SOC reaches 0%, or None if not falling.

    Linear extrapolation; real packs hit a BMS cutoff above 0% and the
    cutback regions distort the slope, so this is "remaining at current
    pace" rather than a hard runtime.
    """
    return _estimate_soc_eta_s(state, target=0.0, rising=False)


def format_eta(secs: float) -> str:
    if secs <= 0:
        return "complete"
    if secs < 60:
        return "<1 min"
    hours = int(secs // 3600)
    minutes = int((secs % 3600) // 60)
    if hours > 0:
        return f"~{hours}h {minutes:02d}m"
    return f"~{minutes} min"


def render_charger(state: State, now: float) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(justify="left")
    t.add_column(justify="left")

    cs = state.chgr_status.value
    stale = state.chgr_status.is_stale(now)
    if cs is None:
        st = Text("---", style="dim")
    elif stale:
        st = Text(f"stale  (last 0x{int(cs):02X})", style="yellow dim")
    elif cs == CHGR_STATUS_IDLE:
        st = Text("idle")
    elif cs == CHGR_STATUS_HANDSHAKE:
        st = Text("handshake (status=0x01)", style="yellow")
    elif cs == CHGR_STATUS_LO:
        st = Text("CHARGING — pack < 76.8 V (status=0x02)", style="bold green")
    elif cs == CHGR_STATUS_HI:
        st = Text("CHARGING — pack ≥ 76.8 V (status=0x03)", style="bold green")
    else:
        st = Text(f"unknown (status=0x{int(cs):02X})", style="magenta")
    t.add_row("State", st)
    t.add_row("current", fmt(state.chgr_i, "{:.1f}", "A", now))
    # DC power = pack V (from F100F3, same source as the pack pane) × charger I.
    pack_v = primary_pack_v(state).value
    if pack_v is not None and state.chgr_i.value is not None:
        t.add_row("DC power",
                  Text(f"{pack_v * state.chgr_i.value:.0f} W"))

    # BMS->Charger setpoints from 1806E5F4. Show V/I requested by the
    # BMS alongside what the charger reports delivering, and surface the
    # enable flag (0=active request, 1=idle).
    en = state.chgr_cmd_enable.value
    if (state.chgr_cmd_v_v.value is not None
            or state.chgr_cmd_i_a.value is not None
            or en is not None):
        t.add_row("", "")
        if en is None:
            en_text = Text("---", style="dim")
        elif int(en) == 0:
            en_text = Text("active", style="green")
        elif int(en) == 1:
            en_text = Text("idle")
        else:
            en_text = Text(f"0x{int(en):02X}", style="magenta")
        t.add_row("BMS request", en_text)
        t.add_row("  V setpoint", fmt(state.chgr_cmd_v_v, "{:.1f}", "V", now))
        t.add_row("  I setpoint", fmt(state.chgr_cmd_i_a, "{:.1f}", "A", now))

    # Time-to-full estimate, shown only while actively charging. Based
    # on the slope of recent BMS SOC samples; CV taper near full will
    # make the linear extrapolation read low in the last ~10%.
    if cs in CHGR_ACTIVE_STATES and not stale:
        t.add_row("", "")
        soc_now = state.bms_soc_pct.value
        if soc_now is not None and soc_now >= 99.5:
            t.add_row("ETA to 100%", Text("complete", style="green"))
        else:
            eta = estimate_charge_eta_s(state)
            if eta is None:
                t.add_row("ETA to 100%",
                          Text("estimating...", style="dim"))
            elif count_soc_transitions(state) < SOC_ETA_STABLE_TRANSITIONS:
                t.add_row("ETA to 100%",
                          Text(f"{format_eta(eta)} (rough)",
                               style="yellow"))
            else:
                t.add_row("ETA to 100%", Text(format_eta(eta)))

    return Panel(t, title="Charger", border_style="green")


def render_cells(state: State, now: float) -> Panel:
    cells = state.cells
    vals = [c.value for c in cells if c.value is not None]
    if not vals:
        return Panel(Text("(no cell data yet)", style="dim"),
                     title="Cell voltages", border_style="blue")

    lo, hi = min(vals), max(vals)
    mean = sum(vals) / len(vals)
    cols = 4  # cells per row (row-major: 5 rows × 4 cols for 20 cells)

    t = Table.grid(padding=(0, 1))
    for _ in range(cols):
        t.add_column(justify="right")
        t.add_column(justify="right")
        t.add_column(justify="right")

    row: list = []
    for i, c in enumerate(cells):
        n = i + 1  # BMS-style 1-based display
        if c.value is None:
            mv_text = Text("---", style="dim")
            delta_text = Text("", style="dim")
        else:
            delta = int(round(c.value - mean))
            if c.value == hi:
                style = "bold green"
            elif c.value == lo:
                style = "bold red"
            elif c.is_stale(now):
                style = "yellow dim"
            else:
                style = None
            mv_text = Text(f"{int(c.value)} mV", style=style)
            delta_text = Text(f"({delta:+d})", style=style)
        label = f"#{n:>2}" if (i % cols == 0) else f"  #{n:>2}"
        row.extend([label, mv_text, delta_text])
        if len(row) == cols * 3:
            t.add_row(*row)
            row = []
    if row:
        # pad final row so add_row gets the right column count
        while len(row) < cols * 3:
            row.extend(["", Text(""), ""])
        t.add_row(*row)

    summary = Text()
    if state.max_cell_n.value is not None:
        summary.append(
            f"  BMS reports max #{int(state.max_cell_n.value)}, "
            f"min #{int(state.min_cell_n.value)}, "
            f"spread {int(state.spread_mv.value or 0)} mV "
            f"({int(state.spread_mv.value or 0) / mean * 100:.2f}%)"
        )

    return Panel(Group(t, summary),
                 title=f"Cell voltages  ({lo}–{hi} mV)",
                 border_style="blue")


def render_temps(state: State, now: float) -> Panel:
    t = Table.grid(padding=(0, 1))
    t.add_column(justify="right")
    t.add_column(justify="left")
    for i, ch in enumerate(state.temps):
        label = Text(f"T{i+1}", style="dim")
        if ch.value is None:
            val = Text("---", style="dim")
        else:
            style = "yellow dim" if ch.is_stale(now) else None
            val = Text(f"{int(ch.value)}°C ({int(c_to_f(ch.value))}°F)",
                       style=style)
        t.add_row(label, val)

    vals = [c.value for c in state.temps if c.value is not None]
    if vals:
        lo, hi = min(vals), max(vals)
        delta = hi - lo
        sub = Text(f"Δ {delta}°C  {lo}–{hi}°C", style="dim")
    else:
        sub = Text("")
    return Panel(Group(t, sub), title="Temps", border_style="blue")


def render_motor(state: State, now: float) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(justify="left")
    t.add_column(justify="left")

    rpm_mag = state.motor_rpm_mag.value
    if rpm_mag is None:
        rpm_text = Text("---", style="dim")
    else:
        mag = abs(int(rpm_mag))
        style = ("bold red" if mag > 2800
                else "green" if mag > 100
                else None)
        di = state.motor_direction.value
        sign = "-" if di == -1 else " "
        rpm_text = Text(f"{sign}{mag:>5d}", style=style)
        if state.motor_rpm_mag.is_stale(now):
            rpm_text = Text(f"{sign}{mag:>5d}  (stale)", style="yellow dim")
    t.add_row("RPM", rpm_text)

    thr = state.motor_throttle.value
    if thr is None:
        t.add_row("throttle", Text("---", style="dim"))
    else:
        # Raw 0..0xFF mapped to 0..100% with a 3-unit idle dead-low
        # subtracted (sensor noise with foot off). No upper clamp: a raw
        # value above 0xFF (shouldn't happen) would render as >100%.
        pct = max(0.0, (int(round(thr)) - THROTTLE_DEAD_LOW) * THROTTLE_PCT_PER_BIT)
        bar_w = 20
        filled = max(0, min(bar_w, int(round(pct * bar_w / 100))))
        bar = Text("█" * filled + "░" * (bar_w - filled))
        t.add_row("throttle",
                  Text.assemble(bar, Text(f"  {pct:>5.1f}%  (raw {int(thr)})")))

    di = state.motor_direction.value
    if di is None:
        di_text = Text("---", style="dim")
    elif di == 1:
        di_text = Text("FORWARD", style="bold green")
    elif di == -1:
        di_text = Text("REVERSE", style="bold yellow")
    else:
        di_text = Text("NEUTRAL", style="dim")
    t.add_row("F/N/R", di_text)

    rg = state.motor_range_gear.value
    if rg is None:
        rg_text = Text("---", style="dim")
    else:
        rg_text = Text(f"R{int(rg)}")
    t.add_row("range", rg_text)

    rpm_signed = state.motor_rpm.value
    if rpm_signed is None or rg is None:
        gs_text = Text("---", style="dim")
    else:
        coef = KMH_PER_RPM_HIGH_TURF.get(int(rg))
        if coef is None:
            gs_text = Text("---", style="dim")
        else:
            kmh = rpm_signed * coef
            mph = kmh * KMH_TO_MPH
            gs_text = Text(f"{kmh:+5.1f} km/h  ({mph:+5.1f} mph)")
            if state.motor_rpm.is_stale(now):
                gs_text = Text(f"{kmh:+5.1f} km/h  ({mph:+5.1f} mph)  (stale)",
                               style="yellow dim")
    t.add_row("ground speed", gs_text)

    def _temp_text(ch: Channel) -> Text:
        if ch.value is None:
            return Text("---", style="dim")
        text = f"{ch.value:.0f} °C ({c_to_f(ch.value):.0f} °F)"
        if ch.is_stale(now):
            return Text(text, style="yellow dim")
        return Text(text)

    t.add_row("ctrl temp", _temp_text(state.controller_temp_c))
    t.add_row("motor temp", _temp_text(state.motor_temp_c))

    return Panel(t, title="Motor controller", border_style="magenta")


def render_vc(state: State, now: float) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(justify="left")
    t.add_column(justify="left")
    raw = state.vc_state_raw.value
    if raw is None:
        s = Text("---", style="dim")
    else:
        name = VC_STATE_NAMES.get(int(raw), "unknown")
        s = Text(f"{name}  (0x{int(raw):02X})")
    t.add_row("Heartbeat", s)
    if state.vc_state_raw.ts is not None:
        ago = now - state.vc_state_raw.ts
        t.add_row("Last F100D0", Text(f"{ago:.1f} s ago"))
    return Panel(t, title="Vehicle controller", border_style="magenta")


def render_faults(state: State, now: float) -> Panel:
    """Display BMS fault info from F108 plus MC fault info from DM1
    (PGN FECA, SA 0xCA). The two subsystems use different fault
    transports: BMS broadcasts F108 as a continuous bitmap (byte 7
    decoded against the vendor BMS error-code table; other bytes
    shown raw when nonzero), while the MC speaks standard J1939 DM1
    where the SPN value equals the dashboard-displayed MC code
    (e.g. SPN 47 -> "MC47" = HPD/Sequencing Fault).
    """
    bytes_seen = any(c.value is not None for c in state.fault_bytes)
    faults = active_bms_faults(state) if bytes_seen else []

    t = Table.grid(padding=(0, 2))
    t.add_column(justify="right")
    t.add_column(justify="left")

    if not bytes_seen:
        t.add_row(Text("F108", style="dim"),
                  Text("(no F108 frame seen yet)", style="dim"))
        nonzero_other = []
        raw = Text("", style="dim")
    else:
        # Stale if the most recent F108 byte update is older than STALE_S.
        stamps = [c.ts for c in state.fault_bytes if c.ts is not None]
        stale = (not stamps) or ((now - max(stamps)) > STALE_S)

        vals = [int(c.value) if c.value is not None else 0
                for c in state.fault_bytes]
        nonzero_other = [(i, v) for i, v in enumerate(vals)
                         if v != 0 and i != 7]

        if faults:
            for code, desc in faults:
                t.add_row(Text(f"{code}", style="bold red"), Text(desc))
        else:
            t.add_row(Text("byte 7", style="dim"),
                      Text("no codes from byte-7 group", style="green"))

        if nonzero_other:
            # Bytes 0..6 (excluding 7) with bit-position breakdown. Useful
            # while the bit-to-code mapping for these bytes is still open.
            for i, v in nonzero_other:
                bits = ", ".join(str(b) for b in range(8) if (v >> b) & 1)
                t.add_row(
                    Text(f"byte {i}", style="yellow"),
                    Text(f"0x{v:02X}  bits {{{bits}}}  (undecoded)",
                         style="yellow"),
                )

        raw_style = "yellow dim" if stale else "dim"
        raw_hex = " ".join(f"{v:02X}" for v in vals)
        raw = Text(
            f"raw  {raw_hex}" + ("  (stale)" if stale else ""),
            style=raw_style,
        )

    # DM1 section: always rendered. When healthy it's a single
    # "no active DTCs" line; when active, lamp + SPN/FMI rows.
    dm1 = _render_dm1(state, now)

    dm1_active = (state.dm1_lamp_byte.value or
                  state.dm1_flash_byte.value or
                  state.dm1_spn.value or state.dm1_fmi.value)
    if faults or nonzero_other or dm1_active:
        border = "red" if (faults or dm1_active) else "yellow"
    elif bytes_seen:
        border = "green"
    else:
        border = "blue"

    return Panel(Group(t, raw, dm1), title="Faults & DTCs",
                 border_style=border)


def _render_dm1(state: State, now: float):
    """Compact DM1 (motor-ECU Active DTC) summary, embedded in the
    Faults & DTCs panel. Shows a single 'no active DTCs' line when
    healthy, and lamp/SPN/FMI breakdowns when a fault is active."""
    t = Table.grid(padding=(0, 2))
    t.add_column(justify="right")
    t.add_column(justify="left")

    lamp = state.dm1_lamp_byte
    flash = state.dm1_flash_byte
    spn = state.dm1_spn
    fmi = state.dm1_fmi
    cm = state.dm1_cm
    oc = state.dm1_oc

    # If we've never seen a DM1 frame, lamp.ts is None and value is None.
    # If the last seen frame was idle, the decoder cleared all channels so
    # ts goes back to None. Either way we render a single status line.
    has_lamp = lamp.value is not None or flash.value is not None
    has_dtc = spn.value is not None or fmi.value is not None

    if not has_lamp and not has_dtc:
        # No active fault. Distinguish "never seen" from "actively idle"
        # using state.frames as a proxy -- after any traffic at all on
        # the bus, the motor ECU's 1 Hz DM1 broadcast will have arrived.
        msg = Text("DM1 (motor ECU): no active DTCs",
                   style="green")
        t.add_row("", msg)
        return t

    if has_lamp:
        lb = int(lamp.value or 0)
        fb = int(flash.value or 0)
        for i, name in enumerate(DM1_LAMP_NAMES):
            shift = 6 - 2 * i
            s = (lb >> shift) & 0x03
            f = (fb >> shift) & 0x03
            if s == 0 and f == 0:
                continue
            state_txt = DM1_LAMP_STATE.get(s, "?")
            flash_txt = DM1_FLASH_STATE.get(f, "?")
            style = "bold red" if s == 1 else "yellow"
            t.add_row(
                Text(f"DM1 {name}", style=style),
                Text(f"{state_txt}  flash={flash_txt}", style=style),
            )

    if has_dtc:
        spn_v = int(spn.value or 0)
        fmi_v = int(fmi.value or 0)
        oc_v = int(oc.value or 0)
        cm_v = int(cm.value or 0)
        fmi_name = DM1_FMI_NAMES.get(fmi_v, "?")
        # SPN value = the MC error code shown on the dashboard. Confirmed
        # 2026-05-13 via DM1 injection: SPN 12/47/99 rendered as
        # "MC12"/"MC47"/"MC99". The cluster prepends "MC" because the
        # DM1 source address is 0xCA.
        t.add_row(
            Text(f"MC{spn_v}", style="bold red"),
            Text(describe_mc_code(spn_v), style="bold red"),
        )
        t.add_row(
            Text("DM1 DTC", style="dim red"),
            Text(
                f"SPN={spn_v}  FMI={fmi_v} ({fmi_name})  "
                f"OC={oc_v}  CM={cm_v}",
                style="dim red",
            ),
        )

    return t


def render_alerts(alerts: List[Tuple[str, str]]) -> Panel:
    if not alerts:
        return Panel(Text("(none)", style="green"),
                     title="Alerts", border_style="green")
    t = Table.grid(padding=(0, 2))
    t.add_column()
    t.add_column()
    for sev, msg in alerts:
        style = "bold red" if sev == "CRIT" else "yellow"
        t.add_row(Text(sev, style=style), Text(msg))
    border = "red" if any(s == "CRIT" for s, _ in alerts) else "yellow"
    return Panel(t, title="Alerts", border_style=border)


def build_layout(state: State, args, now: float, mode: str = "live") -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="row1", size=17),
        Layout(name="cells", size=11),
        Layout(name="row4", size=9),
        Layout(name="faults", size=15),
        Layout(name="alerts", size=8),
    )
    layout["header"].update(render_header(state, now, mode))
    layout["row1"].split_row(
        Layout(render_pack(state, args.mains_v, args.efficiency, now)),
        Layout(render_charger(state, now)),
    )
    layout["cells"].split_row(
        Layout(render_cells(state, now), ratio=1),
        Layout(render_temps(state, now), ratio=1),
    )
    layout["row4"].split_row(
        Layout(render_motor(state, now)),
        Layout(render_vc(state, now)),
    )
    layout["faults"].update(render_faults(state, now))
    alerts = evaluate_alerts(state, args.mains_v, args.breaker_a,
                             args.efficiency, now)
    layout["alerts"].update(render_alerts(alerts))
    return layout


# --- frame source -----------------------------------------------------------

def iter_asc_messages_from(path: str, byte_offset: int):
    """Yield can.Message from a Vector .asc file starting at byte_offset
    (advanced past any partial first line). Backs --start P%, which seeks
    by file position rather than scanning timestamps — line lengths are
    roughly uniform so byte-% closely tracks time-%. Header lines and the
    'Start of measurement' marker before the first data line are skipped
    by the per-line shape check."""
    f = open(path, "r", errors="replace")
    try:
        f.seek(byte_offset)
        if byte_offset > 0:
            f.readline()  # discard the partial line at the seek point
        for line in f:
            parts = line.split()
            # Data line: "<ts> <bus> <hex_id[x]> Rx|Tx d <dlc> <byte>..."
            if len(parts) < 7 or parts[4].lower() != "d":
                continue
            try:
                ts = float(parts[0])
                dlc = int(parts[5])
            except ValueError:
                continue
            id_str = parts[2]
            ext = id_str.endswith(("x", "X"))
            if ext:
                id_str = id_str[:-1]
            try:
                arb_id = int(id_str, 16)
            except ValueError:
                continue
            data_tokens = parts[6:6 + dlc]
            if len(data_tokens) < dlc:
                continue
            try:
                data = bytes(int(b, 16) for b in data_tokens)
            except ValueError:
                continue
            yield can.Message(
                timestamp=ts,
                arbitration_id=arb_id,
                is_extended_id=ext,
                data=data,
            )
    finally:
        f.close()


def open_source(args):
    """Return either a python-can Bus (live) or LogReader (replay)."""
    if args.replay:
        return can.LogReader(args.replay), "replay"
    kwargs = dict(interface=args.interface, channel=args.channel,
                  bitrate=args.bitrate)
    if args.host:
        kwargs["host"] = args.host
    if args.port:
        kwargs["port"] = args.port
    return can.Bus(**kwargs), "live"


# --- web UI -----------------------------------------------------------------
#
# When run with --ui web, we expose the same `/` and `/json` endpoints the
# firmware does, so the existing dashboard.html (the single source of truth,
# in android/app/src/main/assets/) can render against replayed or live data.
# state_to_json mirrors the firmware's buildJson(minimal=false) shape closely
# enough that the dashboard reads the same fields.

KMH_PER_RPM = (5.7 / 2800.0, 8.6 / 2800.0, 17.0 / 2800.0)
MPH_PER_KMH = 0.6213712


def _ch(c: Channel, ndigits: Optional[int] = None):
    if c.value is None:
        return None
    return round(c.value, ndigits) if ndigits is not None else c.value


def state_to_json(state: State, now: float, mode: str) -> dict:
    """Snapshot in the same shape the firmware serves at /json."""
    out: dict = {"uptime": now - state.started_at}

    can_obj: dict = {
        "state": "running" if state.frames > 0 else "stopped",
        "frames_rx": state.frames,
    }
    if state.last_frame_ts is not None:
        can_obj["last_frame_age_s"] = round(now - state.last_frame_ts, 2)
    out["can"] = can_obj

    pack: dict = {}
    pv = primary_pack_v(state).value
    if pv is not None:
        pack["voltage_v"] = round(pv, 2)
    # JSON convention: positive = into the pack (charging), negative = out
    # (drawing). Underlying state values use the J1939 wire convention
    # (positive = drawing), so flip on the way out.
    if state.pack_i_a.value is not None:
        pack["current_a"] = round(-state.pack_i_a.value, 1)
    if state.pack_power_w.value is not None:
        pack["power_w"] = round(-state.pack_power_w.value, 1)
    if state.bms_soc_pct.value is not None:
        pack["soc_pct"] = round(state.bms_soc_pct.value, 1)

    cells: dict = {}
    if state.max_cell_mv.value is not None:
        cells["max_mv"] = int(state.max_cell_mv.value)
    if state.min_cell_mv.value is not None:
        cells["min_mv"] = int(state.min_cell_mv.value)
    if state.spread_mv.value is not None:
        cells["spread_mv"] = int(state.spread_mv.value)
    if state.max_cell_n.value is not None:
        cells["max_n"] = int(state.max_cell_n.value)
    if state.min_cell_n.value is not None:
        cells["min_n"] = int(state.min_cell_n.value)
    temp: dict = {}
    if state.temp_max_c.value is not None:
        temp["max_c"] = int(state.temp_max_c.value)
    if state.temp_min_c.value is not None:
        temp["min_c"] = int(state.temp_min_c.value)
    if temp:
        cells["temp_summary"] = temp
    if cells:
        pack["cells"] = cells
    if pack:
        out["pack"] = pack

    # Session-cumulative energy + derived ETAs (mirrors buildJson session block).
    sess: dict = {
        "wh_drawn": round(state.energy_wh_drawn, 1),
        "wh_charged": round(state.energy_wh_charged, 1),
        # Net: positive = into pack (net charge), negative = out (net draw).
        "wh_net": round(state.energy_wh_charged - state.energy_wh_drawn, 1),
        "wh_capacity": PACK_CAPACITY_WH,
    }
    if state.bms_soc_pct.value is not None:
        remaining = state.bms_soc_pct.value * PACK_CAPACITY_WH / 100.0
        sess["wh_remaining"] = round(remaining, 1)
        eta_full = estimate_charge_eta_s(state)
        eta_zero = estimate_drain_eta_s(state)
        if eta_full and eta_full > 0:
            sess["eta_to_full_s"] = int(eta_full)
        if eta_zero and eta_zero > 0:
            sess["eta_to_zero_s"] = int(eta_zero)
    out["session"] = sess

    # BMS state pills. Firmware names the b1 bit 6 channel "awake" (matches the
    # dashboard label); stream.py's internal field is bms_contactors but the
    # bit decoded is the same one (see decode() PGN_F106).
    bms_block: dict = {}
    if state.bms_state_byte0.value is not None:
        bms_block["state"] = {
            "output_enable":  int(bool(state.bms_output_enable.value)),
            "main_contactor": int(bool(state.bms_main_contactor.value)),
            "operating":      int(bool(state.bms_operating.value)),
            "standby":        int(bool(state.bms_standby.value)),
            "charging":       int(bool(state.bms_charging.value)),
            "charger_present": int(bool(state.bms_charger_present.value)),
            "drive_mode":     int(bool(state.bms_drive_mode.value)),
            "awake":          int(bool(state.bms_contactors.value)),
        }
    if (state.limit_discharge_a.value is not None
            or state.limit_charge_a.value is not None):
        lim: dict = {}
        if state.limit_discharge_a.value is not None:
            lim["discharge_a"] = round(state.limit_discharge_a.value, 1)
        if state.limit_charge_a.value is not None:
            lim["charge_a"] = round(state.limit_charge_a.value, 1)
        bms_block["limit"] = lim
    if bms_block:
        out["bms"] = bms_block

    bms_codes = [code for code, _ in active_bms_faults(state)]
    mc_codes: list = []
    if state.dm1_spn.value is not None and int(state.dm1_spn.value) != 0:
        mc_codes.append(int(state.dm1_spn.value))
    out["faults"] = {"bms": bms_codes, "mc": mc_codes}

    if state.motor_rpm_mag.value is not None:
        mot: dict = {"rpm_magnitude": int(state.motor_rpm_mag.value)}
        if state.motor_direction.value is not None:
            mot["direction"] = int(state.motor_direction.value)
        if state.motor_range_gear.value is not None:
            rg = int(state.motor_range_gear.value)
            mot["range_gear"] = rg
            if 1 <= rg <= 3:
                kmh = state.motor_rpm_mag.value * KMH_PER_RPM[rg - 1]
                mot["speed_kmh"] = round(kmh, 2)
                mot["speed_mph"] = round(kmh * MPH_PER_KMH, 2)
        if state.controller_temp_c.value is not None:
            mot["controller_temp_c"] = int(state.controller_temp_c.value)
        if state.motor_temp_c.value is not None:
            mot["motor_temp_c"] = int(state.motor_temp_c.value)
        out["motor"] = mot

    if state.chgr_status.value is not None:
        chg: dict = {"status": int(state.chgr_status.value)}
        if state.chgr_v.value is not None:
            chg["voltage_v"] = round(state.chgr_v.value, 2)
        if state.chgr_i.value is not None:
            chg["current_a"] = round(state.chgr_i.value, 1)
        out["charger"] = chg

    if (state.chgr_cmd_v_v.value is not None
            or state.chgr_cmd_i_a.value is not None):
        cmd: dict = {}
        if state.chgr_cmd_v_v.value is not None:
            cmd["voltage_v"] = round(state.chgr_cmd_v_v.value, 1)
        if state.chgr_cmd_i_a.value is not None:
            cmd["current_a"] = round(state.chgr_cmd_i_a.value, 1)
        out["chgr_cmd"] = cmd

    return out


# Located by walking up from this script to the repo root. Single source of
# truth: the Android app's WebView assets directory. The firmware symlinks its
# embedded copy at embedded/esp32-s3/src/dashboard.html to this same file.
DASHBOARD_HTML_PATH = (
    Path(__file__).resolve().parent
    / "android" / "app" / "src" / "main" / "assets" / "dashboard.html"
)


def serve_web(state: State, mode: str, host: str, port: int,
              reader: threading.Thread, stop_evt: threading.Event) -> None:
    """Block while serving /  and  /json until the reader exits or Ctrl-C."""
    try:
        html_bytes = DASHBOARD_HTML_PATH.read_bytes()
    except OSError as e:
        sys.stderr.write(f"failed to read {DASHBOARD_HTML_PATH}: {e}\n")
        return

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silence access log
            pass

        def _send(self, code: int, ctype: str, body: bytes) -> None:
            # Browser refreshes / polling cancels race with response writes;
            # the resulting disconnect errors are benign — swallow them so
            # the access log stays clean.
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", html_bytes)
            elif self.path == "/json":
                body = json.dumps(
                    state_to_json(state, time.monotonic(), mode)
                ).encode("utf-8")
                self._send(200, "application/json", body)
            else:
                self.send_error(404)

    httpd = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(f"Web UI listening on http://{host}:{port}/\n")
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()
    try:
        while reader.is_alive() and not stop_evt.is_set():
            reader.join(timeout=1.0)
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- BLE peripheral ---------------------------------------------------------
#
# When run with --ui ble, we impersonate the firmware's Nordic UART Service
# peripheral so the Android app (which scans by service UUID, not name) can
# connect to the laptop instead of the tractor. Same UUIDs, same on-wire
# framing as embedded/esp32-s3/src/main.cpp:925-1010:
#
#     [u16 big-endian length] [length bytes of JSON]
#
# split across N notifications of up to BLE_CHUNK_BYTES each. The Android
# client reassembles by counting bytes against the length prefix
# (BleClient.kt:226). Reuses state_to_json() — Android renders the dashboard
# from the same shape it receives over HTTP, so a single JSON producer
# serves both transports.

NUS_SVC_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_TX_UUID  = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_RX_UUID  = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"

BLE_CHUNK_BYTES = 180
BLE_PUSH_INTERVAL_S = 0.2          # diff cadence (matches firmware's 200 ms)
BLE_INTER_CHUNK_DELAY_S = 0.005    # 5 ms between chunks


async def _ble_run(state: State, mode: str,
                   reader: threading.Thread,
                   stop_evt: threading.Event) -> None:
    try:
        from bless import (
            BlessServer,
            GATTAttributePermissions,
            GATTCharacteristicProperties,
        )
    except ImportError as e:
        sys.stderr.write(
            f"failed to import `bless` for --ui ble: {e}\n"
            "If bless is installed but its bleak dependency is too new "
            "(>=1.0), pin `bleak<1` and re-sync.\n")
        return

    server = BlessServer(name="solectrac")
    # Writes to RX are accepted and silently discarded (firmware does the same).
    server.read_request_func = lambda ch, **_: ch.value
    server.write_request_func = lambda ch, value, **_: None

    await server.add_new_service(NUS_SVC_UUID)
    # CoreBluetooth rejects non-nil initial values on anything that isn't
    # strictly read-only, so pass None for both notify and write chars.
    await server.add_new_characteristic(
        NUS_SVC_UUID, NUS_TX_UUID,
        GATTCharacteristicProperties.notify,
        None,
        GATTAttributePermissions.readable,
    )
    await server.add_new_characteristic(
        NUS_SVC_UUID, NUS_RX_UUID,
        (GATTCharacteristicProperties.write
         | GATTCharacteristicProperties.write_without_response),
        None,
        GATTAttributePermissions.writeable,
    )
    await server.start()
    sys.stderr.write(
        f"BLE peripheral advertising NUS {NUS_SVC_UUID}; "
        "open the Android app and Scan.\n")

    last_payload = b""
    try:
        while reader.is_alive() and not stop_evt.is_set():
            payload = json.dumps(
                state_to_json(state, time.monotonic(), mode)
            ).encode("utf-8")
            if payload and payload != last_payload and len(payload) <= 0xFFFF:
                frame = struct.pack(">H", len(payload)) + payload
                tx_char = server.get_characteristic(NUS_TX_UUID)
                for off in range(0, len(frame), BLE_CHUNK_BYTES):
                    tx_char.value = bytearray(frame[off:off + BLE_CHUNK_BYTES])
                    server.update_value(NUS_SVC_UUID, NUS_TX_UUID)
                    await asyncio.sleep(BLE_INTER_CHUNK_DELAY_S)
                last_payload = payload
            await asyncio.sleep(BLE_PUSH_INTERVAL_S)
    finally:
        await server.stop()


def serve_ble(state: State, mode: str,
              reader: threading.Thread, stop_evt: threading.Event) -> None:
    try:
        asyncio.run(_ble_run(state, mode, reader, stop_evt))
    except KeyboardInterrupt:
        pass


# --- main -------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Live BMS / charger TUI for the Solectrac CAN bus.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--interface",
                     help="python-can interface (e.g. socketcan, slcan, pcan)")
    src.add_argument("--replay",
                     help="replay a python-can log file (.log/.asc/.blf)")
    p.add_argument("--channel",
                   help="bus channel for live capture (e.g. can0)")
    p.add_argument("--bitrate", type=int, default=250000,
                   help="bus bitrate for live capture (default 250000)")
    p.add_argument("--host",
                   help="remote host for network interfaces (e.g. socketcand)")
    p.add_argument("--port", type=int,
                   help="remote port for network interfaces (e.g. socketcand)")
    p.add_argument("--raw-log",
                   help="write a python-can log of all received frames")
    p.add_argument("--mains-v", type=float, default=120.0,
                   help="AC supply voltage for AC-draw estimate (default 120)")
    p.add_argument("--breaker-a", type=float, default=20.0,
                   help="AC breaker rating for alerting (default 20)")
    p.add_argument("--efficiency", type=float, default=0.85,
                   help="assumed AC->DC charger efficiency (default 0.85)")
    p.add_argument("--refresh-hz", type=float, default=5.0,
                   help="TUI refresh rate (default 5)")
    p.add_argument("--start", type=float, default=0.0,
                   help="for --replay (.asc only), seek to this percentage "
                        "of the file size and start realtime playback from "
                        "there (0..100, default 0). Cheap O(1) seek; the "
                        "TUI starts cold and populates as frames arrive.")
    p.add_argument("--timescale", type=float, default=1.0,
                   help="for --replay, multiplier on realtime playback "
                        "(1.0 = recorded speed, 2.0 = 2x faster, "
                        "0.5 = half speed)")
    p.add_argument("--ui", choices=("tui", "web", "ble", "none"), default="tui",
                   help="UI mode: 'tui' (default) renders the rich terminal "
                        "dashboard; 'web' serves the firmware's /  and  /json "
                        "endpoints so the same dashboard.html can render against "
                        "replayed or live data; 'ble' impersonates the firmware's "
                        "Nordic UART peripheral so the Android app can connect to "
                        "the laptop; 'none' is headless (decode only)")
    p.add_argument("--web-host", default="0.0.0.0",
                   help="bind address for --ui web (default 0.0.0.0)")
    p.add_argument("--web-port", type=int, default=8080,
                   help="bind port for --ui web (default 8080)")
    args = p.parse_args()

    if args.interface and not args.channel and args.interface != "virtual":
        p.error("--channel is required with --interface")

    state = State()
    source, mode = open_source(args)

    raw_logger: Optional["can.Listener"] = None
    if args.raw_log:
        raw_logger = can.Logger(args.raw_log)

    stop_evt = threading.Event()

    def reader_loop():
        try:
            if mode == "live":
                while not stop_evt.is_set():
                    msg = source.recv(timeout=0.1)
                    if msg is None:
                        continue
                    if raw_logger is not None:
                        raw_logger(msg)
                    decode(msg, state, time.monotonic())
            else:
                start_pct = max(0.0, min(100.0, args.start))
                replay_iter = source
                if start_pct > 0 and args.replay:
                    if args.replay.lower().endswith(".asc"):
                        import os
                        size = os.path.getsize(args.replay)
                        offset = int(size * start_pct / 100.0)
                        replay_iter = iter_asc_messages_from(
                            args.replay, offset)
                    else:
                        sys.stderr.write(
                            "--start: only supported for .asc replays; "
                            "ignoring\n")

                first_msg_ts: Optional[float] = None
                replay_start: Optional[float] = None
                timescale = args.timescale if args.timescale > 0 else 1.0
                for msg in replay_iter:
                    if stop_evt.is_set():
                        break
                    if raw_logger is not None:
                        raw_logger(msg)
                    if getattr(msg, "timestamp", None):
                        if first_msg_ts is None:
                            first_msg_ts = msg.timestamp
                            replay_start = time.monotonic()
                        else:
                            elapsed_log = (msg.timestamp - first_msg_ts) / timescale
                            target = replay_start + elapsed_log
                            delay = target - time.monotonic()
                            if delay > 0:
                                # break sleep into chunks so stop_evt is responsive
                                end = time.monotonic() + delay
                                while not stop_evt.is_set():
                                    remaining = end - time.monotonic()
                                    if remaining <= 0:
                                        break
                                    time.sleep(min(0.1, remaining))
                    decode(msg, state, time.monotonic())
                # signal that replay finished
                stop_evt.set()
        except Exception as e:
            state.errors += 1
            sys.stderr.write(f"reader error: {e}\n")

    reader = threading.Thread(target=reader_loop, daemon=True)
    reader.start()

    try:
        if args.ui == "web":
            serve_web(state, mode, args.web_host, args.web_port,
                      reader, stop_evt)
        elif args.ui == "ble":
            serve_ble(state, mode, reader, stop_evt)
        elif args.ui == "none":
            while reader.is_alive() and not stop_evt.is_set():
                reader.join(timeout=1.0)
        else:
            with Live(build_layout(state, args, time.monotonic(), mode),
                      refresh_per_second=args.refresh_hz,
                      screen=True) as live:
                tick = 1.0 / max(args.refresh_hz, 1.0)
                while reader.is_alive() and not stop_evt.is_set():
                    live.update(build_layout(state, args, time.monotonic(), mode))
                    time.sleep(tick)
                # Keep the final frame visible briefly when replay ends.
                if args.replay:
                    live.update(build_layout(state, args, time.monotonic(), mode))
                    time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        try:
            if hasattr(source, "shutdown"):
                source.shutdown()
        except Exception:
            pass
        if raw_logger is not None:
            try:
                raw_logger.stop()
            except Exception:
                pass
        reader.join(timeout=2.0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
