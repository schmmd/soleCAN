#!/usr/bin/env python3
"""Per-stage movers for a canopen_stages.csv (firmware-poller stage capture).

Uses only the LAST complete sweep of each stage (rows between the last two
0x1000 boundaries), so serial backlog at stage start can't leak in. Prints, per
stage, the objects whose value differs from baseline, then the "clean flags":
objects that hold one value in every stage but one.

    python3 canopen/analyze_stages.py [canopen/canopen_stages.csv] [--skip seat_empty,lever_f]
"""
import argparse, csv, os, sys
from collections import defaultdict

COUNTERS = {0x3160, 0x3332, 0x3338, 0x3339, 0x3330, 0x3336, 0x3510, 0x33EF,
            0x350E, 0x3554, 0x3555, 0x38CC, 0x35C1, 0x35C6}   # timers / dither (see README)

def s16(v): return v - 65536 if 32767 < v < 65536 else v

def last_sweep(rows):
    b = [i for i, r in enumerate(rows) if r[0] == 0x1000]
    if len(b) < 2: return {}
    return {idx: s16(val) for idx, val in rows[b[-2]:b[-1]]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "canopen_stages.csv"))
    ap.add_argument("--skip", default="", help="comma-separated stages to ignore")
    a = ap.parse_args()
    skip = set(filter(None, a.skip.split(",")))
    by = defaultdict(list)
    order = []
    for r in csv.DictReader(open(a.csv)):
        if r["stage"] in skip or int(r["sub"], 16) != 0: continue
        if r["stage"] not in order: order.append(r["stage"])
        by[r["stage"]].append((int(r["index"], 16), int(r["value"])))
    sweep = {st: last_sweep(by[st]) for st in order}
    base = sweep[order[0]]
    print(f"stages: {order}\nbaseline = {order[0]} ({len(base)} objects in last sweep)\n")
    for st in order[1:]:
        cur = sweep[st]
        d = [(i, base[i], cur[i]) for i in cur if i in base and i not in COUNTERS and cur[i] != base[i]]
        print(f"=== {st}: {len(d)} objects differ from baseline")
        for i, b, c in sorted(d, key=lambda x: -abs(x[2]-x[1]))[:25]:
            print(f"   0x{i:04X}  {b:>7} -> {c:<7}  (delta {c-b:+})")
    # clean flags: same value in all stages but exactly one
    print("\n=== clean flags: one value everywhere except in exactly one stage")
    allidx = set(base)
    for i in sorted(allidx):
        if i in COUNTERS: continue
        vals = {st: sweep[st].get(i) for st in order}
        c = defaultdict(list)
        for st, v in vals.items(): c[v].append(st)
        if len(c) == 2:
            (v1, s1), (v2, s2) = sorted(c.items(), key=lambda kv: len(kv[1]))
            if len(s1) == 1:
                print(f"   0x{i:04X}: {v2} everywhere, {v1} only in {s1[0]}")

if __name__ == "__main__":
    main()
