#!/usr/bin/env python3
"""Guided CANopen capture: walks you through machine states, labels each in the
data, and shows which objects moved vs. baseline (idle noise auto-filtered).

Run it YOURSELF in your terminal (it's interactive):

    python canopen/canopen_session.py                 # -> canopen/canopen_session.csv
    python canopen/canopen_session.py --secs 20       # longer window per stage

For each stage: set the machine as instructed, press Enter, and it records for
--secs seconds while polling every object. After each stage it prints the top
movers vs. baseline. Read-only (SDO upload only). Ctrl-C saves and exits.
"""
import argparse, csv, os, re, statistics, sys, time
import serial, can
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from canopen_dump import sdo_upload, switch_to_slcan, NODE, PORT

LINE_RE = re.compile(r"^0x([0-9A-Fa-f]{4}):0x([0-9A-Fa-f]{2})\s+value=")

# (label, what to set before pressing Enter)
STAGES = [
    ("baseline",        "Neutral, NO throttle. Hydraulics ON (leave as-is). Sit still."),
    ("hydraulics_off",  "Turn HYDRAULICS OFF. Still Neutral, no throttle."),
    ("forward",         "Shift F/N/R lever to FORWARD (via Neutral). No throttle."),
    ("reverse",         "Shift lever to REVERSE. No throttle."),
    ("neutral",         "Back to NEUTRAL. No throttle."),
    ("range_r1",        "Range switch to R1 (turtle)."),
    ("range_r2",        "Range switch to R2."),
    ("range_r3",        "Range switch to R3 (rabbit)."),
    ("throttle_quarter","FORWARD, hold ~1/4 throttle (motor will spin)."),
    ("throttle_full",   "FORWARD, hold FULL throttle."),
    ("throttle_off",    "Release throttle, back to Neutral."),
    ("pto_on",          "Engage PTO."),
    ("pto_off",         "Disengage PTO."),
]

def load_targets(path):
    targets = []
    with open(path) as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue
            index, sub = int(m.group(1), 16), int(m.group(2), 16)
            if sub == 0x05 or sub != 0x00:   # sub0 telemetry only; skip 0x05 quirk
                continue
            targets.append((index, sub))
    return targets

def record_stage(bus, targets, secs, writer, label):
    """Poll every target repeatedly for `secs`. Returns {obj: [samples]}."""
    samples = {}
    end = time.monotonic() + secs
    sweeps = 0
    while time.monotonic() < end:
        sweeps += 1
        for index, sub in targets:
            res = sdo_upload(bus, index, sub)
            if not res or "value" not in res:
                continue
            v = res["value"]
            writer.writerow([label, time.strftime("%H:%M:%S"), f"{time.monotonic():.2f}",
                             f"0x{index:04X}", f"0x{sub:02X}", v, res["raw"]])
            samples.setdefault((index, sub), []).append(v)
        sys.stdout.write("."); sys.stdout.flush()
    print(f" [{sweeps} sweeps]")
    return samples

def summarize(label, samples, baseline_med, baseline_spread):
    """Print objects whose median moved beyond baseline's own idle spread."""
    movers = []
    for obj, vals in samples.items():
        med = statistics.median(vals)
        b_med = baseline_med.get(obj)
        if b_med is None:
            continue
        delta = med - b_med
        # significant only if it exceeds this object's idle wiggle
        if abs(delta) > max(baseline_spread.get(obj, 0), 0):
            movers.append((abs(delta), obj, b_med, med, delta))
    movers.sort(reverse=True)
    if not movers:
        print(f"  ({label}: nothing moved beyond idle noise)")
        return
    print(f"  {label}: top movers vs baseline")
    for _, (index, sub), b_med, med, delta in movers[:20]:
        print(f"    0x{index:04X}:0x{sub:02X}  {b_med} -> {med}  (Δ{delta:+g})")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=15.0, help="record seconds per stage")
    ap.add_argument("--targets", default=os.path.join(HERE, "canopen.txt"))
    ap.add_argument("--out", default=os.path.join(HERE, "canopen_session.csv"))
    ap.add_argument("--port", default=PORT)
    args = ap.parse_args()

    targets = load_targets(args.targets)
    print(f"{len(targets)} objects; {args.secs:g}s per stage; writing {args.out}")
    switch_to_slcan(args.port)

    baseline_med, baseline_spread = {}, {}
    f = open(args.out, "w", newline="")
    writer = csv.writer(f)
    writer.writerow(["stage", "iso_time", "t_mono", "index", "sub", "value", "raw"])

    try:
        with can.Bus(interface="slcan", channel=args.port, bitrate=250000) as bus:
            for label, instr in STAGES:
                print(f"\n=== {label} ===\n  SET: {instr}")
                ans = input("  Press Enter to record  (s=skip, q=quit): ").strip().lower()
                if ans == "q":
                    break
                if ans == "s":
                    continue
                samples = record_stage(bus, targets, args.secs, writer, label)
                f.flush()
                if label == "baseline":
                    for obj, vals in samples.items():
                        baseline_med[obj] = statistics.median(vals)
                        baseline_spread[obj] = (max(vals) - min(vals)) if len(vals) > 1 else 0
                    print(f"  baseline set ({len(baseline_med)} objects).")
                else:
                    summarize(label, samples, baseline_med, baseline_spread)
    except KeyboardInterrupt:
        print("\n[interrupted]")
    finally:
        f.close()
        print(f"\nSaved {args.out}")

if __name__ == "__main__":
    main()
