#!/usr/bin/env python3
"""Poll the Curtis CANopen objects in a loop and log a time-series to stdout.

Targets are read from a prior dump (canopen.txt): every object with a value,
minus the bogus sub 0x05. Sweeps repeatedly, writes one CSV row per reading to
stdout, and prints only the objects that CHANGED since the last sweep to stderr
so you can watch which values move as you operate the machine.

    python canopen/canopen_timeseries.py > canopen/canopen_ts.csv       # CSV to file, deltas on screen
    python canopen/canopen_timeseries.py --all-subs --targets canopen.txt > canopen/canopen_ts.csv

Read-only (SDO upload only). Ctrl-C to stop. Sweep rate depends on object count
(~4 ms/read); hold each machine state ~15 s so a sweep lands inside it.
"""
import argparse, os, re, sys, time
import serial, can
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from canopen_dump import sdo_upload, switch_to_slcan, NODE, PORT  # reuse SDO helper

LINE_RE = re.compile(r"^0x([0-9A-Fa-f]{4}):0x([0-9A-Fa-f]{2})\s+value=")

def load_targets(path, all_subs):
    """Parse (index, sub) pairs from a canopen.txt dump."""
    targets = []
    with open(path) as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue
            index, sub = int(m.group(1), 16), int(m.group(2), 16)
            if sub == 0x05:                 # constant-0 quirk, skip
                continue
            if not all_subs and sub != 0x00:  # default: telemetry lives at sub0
                continue
            targets.append((index, sub))
    return targets

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default=os.path.join(HERE, "canopen.txt"))
    ap.add_argument("--all-subs", action="store_true", help="poll every sub, not just sub0")
    ap.add_argument("--port", default=PORT)
    args = ap.parse_args()

    targets = load_targets(args.targets, args.all_subs)
    print(f"[targets] {len(targets)} objects from {args.targets}", file=sys.stderr)

    switch_to_slcan(args.port)
    print("iso_time,t_rel,index,sub,value,raw")   # CSV header
    last = {}
    t0 = time.monotonic()
    sweep = 0
    with can.Bus(interface="slcan", channel=args.port, bitrate=250000) as bus:
        while True:
            sweep += 1
            changed = []
            for index, sub in targets:
                res = sdo_upload(bus, index, sub)
                if not res or "value" not in res:
                    continue
                v, raw = res["value"], res["raw"]
                t = time.monotonic() - t0
                print(f"{time.strftime('%H:%M:%S')},{t:.2f},0x{index:04X},0x{sub:02X},{v},{raw}",
                      flush=True)
                key = (index, sub)
                if key in last and last[key] != v:
                    changed.append((index, sub, last[key], v))
                last[key] = v
            # delta view: what moved this sweep
            if sweep > 1:
                if changed:
                    print(f"--- sweep {sweep} ({time.strftime('%H:%M:%S')}): "
                          f"{len(changed)} changed ---", file=sys.stderr, flush=True)
                    for index, sub, old, new in changed:
                        print(f"    0x{index:04X}:0x{sub:02X}  {old} -> {new}",
                              file=sys.stderr, flush=True)
                else:
                    print(f"--- sweep {sweep}: no change ---", file=sys.stderr, flush=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[stopped]", file=sys.stderr)
