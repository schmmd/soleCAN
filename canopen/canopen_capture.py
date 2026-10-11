#!/usr/bin/env python3
"""Capture one labeled window of CANopen values and append to a CSV.

    python canopen/canopen_capture.py --label hydraulics_on --secs 10 --out canopen/hyd_test.csv

Run it twice (different --label) around a state change, then diff the CSV.
Read-only. Reuses the shared SDO helper and the sub0 target list from canopen.txt.
"""
import argparse, csv, os, re, sys, time
import serial, can
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from canopen_dump import sdo_upload, switch_to_slcan, NODE, PORT

LINE_RE = re.compile(r"^0x([0-9A-Fa-f]{4}):0x([0-9A-Fa-f]{2})\s+value=")

def load_targets(path):
    out = []
    with open(path) as f:
        for line in f:
            m = LINE_RE.match(line)
            if m and int(m.group(2), 16) == 0x00:   # sub0 telemetry
                out.append((int(m.group(1), 16), 0x00))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--secs", type=float, default=10.0)
    ap.add_argument("--out", default=os.path.join(HERE, "hyd_test.csv"))
    ap.add_argument("--targets", default=os.path.join(HERE, "canopen.txt"))
    ap.add_argument("--port", default=PORT)
    args = ap.parse_args()

    targets = load_targets(args.targets)
    switch_to_slcan(args.port)
    new_file = not os.path.exists(args.out)
    f = open(args.out, "a", newline="")
    w = csv.writer(f)
    if new_file:
        w.writerow(["label", "t_mono", "index", "sub", "value", "raw"])
    sweeps = 0
    with can.Bus(interface="slcan", channel=args.port, bitrate=250000) as bus:
        end = time.monotonic() + args.secs
        while time.monotonic() < end:
            sweeps += 1
            for index, sub in targets:
                res = sdo_upload(bus, index, sub)
                if res and "value" in res:
                    w.writerow([args.label, f"{time.monotonic():.2f}",
                                f"0x{index:04X}", f"0x{sub:02X}", res["value"], res["raw"]])
            sys.stderr.write("."); sys.stderr.flush()
    f.close()
    print(f"\n[{args.label}] {sweeps} sweeps appended to {args.out}", file=sys.stderr)

if __name__ == "__main__":
    main()
