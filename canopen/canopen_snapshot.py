#!/usr/bin/env python3
"""Print one point-in-time reading of the decoded CANopen objects.

Takes the last full poller sweep before --at seconds from a capture (an SD
can_NN.asc, or a CSV with t,index,value columns such as s137.csv /
canopen_stages.csv) and renders every object we have a decode for: name, raw,
interpreted value, and the confidence marker from canopen/README.md. Objects
without a decode are not shown (see README for the tallies).

    python3 canopen/canopen_snapshot.py sdcard/s00137/can_00.asc --at 600
    python3 canopen/canopen_snapshot.py canopen/s137.csv --at 150
"""
import argparse, csv, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

def s16(v): return v - 65536 if 32767 < v < 65536 else v
def pct32(v): return f"{s16(v) / 32767 * 100:+.1f} %"
def bits(v, names):
    on = [n for b, n in names.items() if v >> b & 1]
    other = v & ~sum(1 << b for b in names)
    return ", ".join(on) + (f" (+0x{other:X} unnamed)" if other else "")

SW = {2: "Sw3 always-on", 3: "R1", 4: "R2", 5: "R3", 6: "FORWARD", 7: "REVERSE", 10: "Sw11 OPC-enable"}
RANGE = {0: "R1", 1: "R2", 2: "R3"}; DIR = {0: "N", 1: "F", 2: "R"}
NMT = {127: "pre-operational", 5: "operational", 4: "stopped"}

# index: (name, confidence, formatter)
DECODE = {
    0x1009: ("HW version",              "CONFIRMED", lambda v: v.to_bytes(4, "little").decode(errors="replace").strip("\0")),
    0x100A: ("SW version",              "CONFIRMED", lambda v: v.to_bytes(4, "little").decode(errors="replace").strip("\0")),
    0x1017: ("Producer heartbeat time", "CONFIRMED", lambda v: f"{v} ms"),
    0x3328: ("CAN NMT state",           "CONFIRMED", lambda v: NMT.get(v, str(v))),
    0x3223: ("Main_State",              "CONFIRMED", lambda v: f"{v} (main-contactor state machine)"),
    0x322B: ("System_Flags1",           "CONFIRMED", lambda v: f"0b{v:08b}  interlock {'CLOSED' if v >> 5 & 1 else 'OPEN'} (bit 5)"),
    0x3226: ("Switches",                "CONFIRMED", lambda v: bits(v, SW)),
    0x33E6: ("OEM packed range+dir",    "CONFIRMED", lambda v: f"{RANGE.get(v >> 12 & 3, '?')} {DIR.get(v >> 10 & 3, '?')}"),
    0x3207: ("Motor_RPM",               "CONFIRMED", lambda v: f"{s16(v)} rpm"),
    0x33FC: ("OEM rpm + 3200 (J1939 FF21CA bytes 2-3)", "CONFIRMED", lambda v: f"{v - 3200} rpm"),
    0x393A: ("|rpm| copy",              "CONFIRMED", lambda v: f"{v} rpm"),
    0x35D1: ("MotorspeedA",             "CONFIRMED", lambda v: f"{s16(v)} rpm"),
    0x35D2: ("MotorspeedB",             "CONFIRMED", lambda v: f"{s16(v)} rpm"),
    0x3206: ("Frequency (electrical)",  "CONFIRMED", lambda v: f"{s16(v)} (2 x rpm, 4-pole)"),
    0x3208: ("Modulation_Depth",        "CONFIRMED", lambda v: f"{v / 1182 * 100:.1f} %"),
    0x3209: ("Current_RMS",             "CONFIRMED", lambda v: f"{v / 10:.1f} A"),
    0x33EA: ("OEM motor current (J1939 FF21CA bytes 0-1)", "CONFIRMED", lambda v: f"{v} A"),
    0x359E: ("Battery_Current",         "CONFIRMED", lambda v: f"{s16(v) / 10:.1f} A"),
    0x324C: ("Capacitor_Voltage",       "CONFIRMED", lambda v: f"{v / 64:.1f} V"),
    0x324D: ("Keyswitch_Voltage",       "CONFIRMED", lambda v: f"{v / 100:.2f} V"),
    0x320B: ("Motor_Temperature",       "CONFIRMED", lambda v: f"{s16(v) / 10:.1f} C"),
    0x322A: ("Controller_Temperature",  "CONFIRMED", lambda v: f"{s16(v) / 10:.1f} C"),
    0x3308: ("BDI_Percentage (= BMS shown SOC via VCL)", "CONFIRMED", lambda v: f"{v} %"),
    0x3215: ("Throttle_Pot_Raw",        "CONFIRMED", lambda v: f"{v / 36044 * 5.5:.2f} V at pedal wiper"),
    0x3217: ("Pot2_Raw (unused input)", "CONFIRMED", lambda v: f"{v / 36044 * 5.5:.2f} V"),
    0x3211: ("Mapped_Throttle",         "CONFIRMED", pct32),
    0x3216: ("Throttle_Command",        "CONFIRMED", pct32),
    0x3218: ("OEM throttle copy (RPDO0 input)", "CONFIRMED", pct32),
    0x3213: ("Throttle_Multiplier",     "CONFIRMED", lambda v: f"{v}  ({'enabled' if v else 'held at 0: neutral-start'})"),
    0x3011: ("Max_Speed_SpdM (active cap)", "CONFIRMED", lambda v: f"{v} rpm"),
    0x33D1: ("OEM active speed limit", "CONFIRMED", lambda v: f"{v} rpm"),
    0x306E: ("ramped speed limit",      "CONFIRMED", lambda v: f"{v} rpm"),
    0x3593: ("ramped speed limit (copy)", "CONFIRMED", lambda v: f"{v} rpm"),
    0x3840: ("Max_Speed_SpdMx",         "CONFIRMED", lambda v: f"{v} rpm"),
    0x3103: ("table R3 forward cap",    "CONFIRMED", lambda v: f"{v} rpm"),
    0x3104: ("table R3 reverse cap",    "CONFIRMED", lambda v: f"{v} rpm"),
    0x3105: ("table R1 forward cap",    "CONFIRMED", lambda v: f"{v} rpm"),
    0x3106: ("table R1 reverse cap",    "CONFIRMED", lambda v: f"{v} rpm"),
    0x3107: ("table R2 forward cap",    "CONFIRMED", lambda v: f"{v} rpm"),
    0x3108: ("table R2 reverse cap",    "CONFIRMED", lambda v: f"{v} rpm"),
    0x3010: ("Control_Mode_Select",     "CONFIRMED", lambda v: {0: "speed-express", 1: "speed mode", 2: "torque mode"}.get(v, str(v))),
    0x305B: ("Drive_Current_Limit",     "CONFIRMED", lambda v: f"{v / 32767 * 100:.0f} % of rated"),
    0x305C: ("Regen_Current_Limit",     "CONFIRMED", lambda v: f"{v / 32767 * 100:.0f} % of rated"),
    0x305D: ("Brake_Current_Limit",     "CONFIRMED", lambda v: f"{v / 32767 * 100:.0f} % of rated"),
    0x3581: ("Motor-temp cutback",      "CONFIRMED", lambda v: f"{v / 4096 * 100:.0f} %"),
    0x35F3: ("Controller-temp cutback", "CONFIRMED", lambda v: f"{v / 4096 * 100:.0f} %"),
    0x3604: ("Over-voltage cutback",    "CONFIRMED", lambda v: f"{v / 4096 * 100:.0f} %"),
    0x3605: ("Under-voltage cutback",   "CONFIRMED", lambda v: f"{v / 4096 * 100:.0f} %"),
    0x3892: ("EMBrakeState",            "CONFIRMED", str),
    0x3149: ("CAN_PDO_Timeout_Period",  "CONFIRMED", lambda v: f"{v} ({'disabled' if v == 0 else 'enabled'})"),
    0x332F: ("CAN_EE_Writes_Enabled",   "CONFIRMED", lambda v: f"{v} ({'writes volatile' if v == 0 else 'WRITES HIT EEPROM'})"),
    0x3160: ("Master_Timer (key-on run time)", "CONFIRMED", lambda v: f"{v} ticks = {v / 9.57 / 3600:.1f} h powered"),
    0x320A: ("Vehicle_Speed (assumes H range)", "CONFIRMED", lambda v: f"{s16(v) / 10:.1f} mph (fixed H-range ratio)"),
    0x35BF: ("Time_to_Capture_Speed_1", "TENTATIVE", lambda v: f"{v / 100:.2f} s"),
    0x35B7: ("headroom ramp",           "TENTATIVE", lambda v: f"{v} (9192 rest -> ~3670 under throttle)"),
    0x35AA: ("headroom ramp (twin)",    "TENTATIVE", str),
    0x3228: ("OEM flag word",           "TENTATIVE", lambda v: f"0x{v:03X} (384 rest; latching bits 3-7)"),
    0x3602: ("OEM motion flag",         "TENTATIVE", lambda v: f"{v} ({'moving' if v == 640 else 'at rest' if v == 5 else '?'})"),
    0x3540: ("OEM stationary flag",     "TENTATIVE", lambda v: f"{v} ({'stationary' if v == 1 else 'moving' if v == 0 else '?'})"),
}
ORDER = [0x1009, 0x100A, 0x1017, 0x3328, 0x3149, 0x332F, 0x3160,
         0x3223, 0x322B, 0x3226, 0x33E6, 0x3892,
         0x3207, 0x33FC, 0x393A, 0x35D1, 0x35D2, 0x3206, 0x320A,
         0x3209, 0x33EA, 0x359E, 0x3208, 0x324C, 0x324D, 0x320B, 0x322A, 0x3308,
         0x3215, 0x3217, 0x3211, 0x3216, 0x3218, 0x3213,
         0x3011, 0x33D1, 0x306E, 0x3593, 0x3840, 0x3103, 0x3104, 0x3105, 0x3106, 0x3107, 0x3108,
         0x3010, 0x305B, 0x305C, 0x305D, 0x3581, 0x35F3, 0x3604, 0x3605,
         0x35BF, 0x35B7, 0x35AA, 0x3228, 0x3602, 0x3540]
assert set(ORDER) == set(DECODE)

def load(path):
    """[(t, index, value)] for sub-0 SDO replies."""
    if path.endswith(".asc"):
        from analyze_asc import parse
        return [(t, i, v) for t, i, sub, v in parse(path)[0] if sub == 0]
    out = []
    for r in csv.DictReader(open(path)):
        if r.get("sub", "0") not in ("0", "0x00"): continue
        out.append((float(r.get("t", r.get("t_mono", 0))), int(r["index"], 16), int(r["value"])))
    return out

def sweep_at(rows, at):
    """Last full sweep (0x1000 .. next 0x1000) ending before `at`; None -> last sweep."""
    b = [i for i, (t, idx, _) in enumerate(rows) if idx == 0x1000 and (at is None or t <= at)]
    if len(b) < 2: sys.exit("need at least two sweep boundaries before --at")
    chunk = rows[b[-2]:b[-1]]
    return {idx: v for _, idx, v in chunk}, chunk[0][0], chunk[-1][0]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture"); ap.add_argument("--at", type=float, default=None, help="capture time (s); default = last sweep")
    a = ap.parse_args()
    vals, t0, t1 = sweep_at(load(a.capture), a.at)
    print(f"{os.path.basename(a.capture)}  sweep {t0:.1f}-{t1:.1f} s  ({len(vals)} objects, {sum(i in vals for i in ORDER)} decoded shown)\n")
    print(f"{'index':7} {'name':40} {'raw':>10}  {'reading':44} conf")
    for idx in ORDER:
        if idx not in vals: continue
        name, conf, fmt = DECODE[idx]
        try: reading = fmt(vals[idx])
        except Exception as e: reading = f"? ({e})"
        print(f"0x{idx:04X}  {name:40} {vals[idx]:>10}  {reading:44} {conf}")

if __name__ == "__main__":
    main()
