#!/usr/bin/env python3
"""Walk the non-zero sub-indices of canopen_full.txt.

Curtis layout (found 2026-09-24): a PARAMETER (EEPROM-writable) exposes
  sub 0 = value, sub 3 = minimum, sub 4 = maximum   (sub 5 is the always-0 quirk)
while a MONITOR variable exposes sub 0 only. Records/arrays (0x1xxx comm
profile, 0x2000/0x2001, 0x57xx) use subs 1..N.

Writes canopen_params.csv (index, vcl_name, value, min, max) and prints the
parameter / monitor split plus the unnamed parameters with a non-trivial value.

    python3 canopen/subindex_walk.py
"""
import collections, csv, os, re
HERE = os.path.dirname(os.path.abspath(__file__))

def load(path=os.path.join(HERE, "canopen_full.txt")):
    ent = collections.defaultdict(dict)
    for l in open(path):
        m = re.match(r"^0x([0-9A-F]{4}):0x([0-9A-F]{2})\s+value=(\d+)", l)
        if m: ent[int(m.group(1), 16)][int(m.group(2), 16)] = int(m.group(3))
    return ent

def main():
    ent = load()
    named = {int(r["index"], 16): r["vcl_name"] for r in csv.DictReader(open(os.path.join(HERE, "canopen_named.csv")))}
    params = {i: s for i, s in ent.items() if 3 in s and 4 in s}
    mons = [i for i, s in ent.items() if i >= 0x3000 and 3 not in s]
    print(f"parameters (min/max present): {len(params)}, unnamed {sum(1 for i in params if not named.get(i))}")
    print(f"monitor variables (sub 0 only): {len(mons)}, unnamed {sum(1 for i in mons if not named.get(i))}")
    out = os.path.join(HERE, "canopen_params.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["index", "vcl_name", "value", "min", "max"])
        for i in sorted(params): w.writerow([f"0x{i:04X}", named.get(i, ""), params[i].get(0), params[i][3], params[i][4]])
    print("wrote", os.path.relpath(out))
    print("\nunnamed parameters with value not at 0/min/max:")
    for i in sorted(params):
        v, lo, hi = params[i].get(0), params[i][3], params[i][4]
        if not named.get(i) and v not in (0, lo, hi): print(f"  0x{i:04X} = {v:>6}   range {lo}..{hi}")
    print("\nrecords/arrays (subs beyond 5):", [f"0x{i:04X}(0..{max(s)})" for i, s in sorted(ent.items()) if max(s) > 5])

if __name__ == "__main__":
    main()
