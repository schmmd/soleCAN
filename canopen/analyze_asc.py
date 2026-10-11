#!/usr/bin/env python3
"""Analyze the CANopen SDO replies the firmware poller leaves in an SD can_NN.asc.

Extracts every 0x5A8 expedited-upload reply as (t, index, value), lines each
sample up with the J1939 FF21CA motor state nearest in time (RPM, torque_raw,
direction, range), and reports which objects move and what they track.

    python3 canopen/analyze_asc.py sdcard/s00137/can_00.asc [-o canopen/s137.csv]
"""
import argparse, csv, math, re, statistics, sys
from collections import defaultdict
from bisect import bisect_left

NODE = 0x28
LINE = re.compile(r"^\s*([\d.]+)\s+1\s+([0-9A-F]+)(x?)\s+Rx\s+d\s+(\d+)((?:\s+[0-9A-F]{2})*)")
RPM_BIAS = 0x0C80

def parse(path):
    sdo, mot = [], []          # (t, idx, val, raw) / (t, rpm, torque, dir, range)
    with open(path) as f:
        for line in f:
            m = LINE.match(line)
            if not m: continue
            t, ident, ext, dlc, hexs = m.groups()
            d = bytes.fromhex(hexs.replace(" ", ""))
            if len(d) < 8: continue
            t = float(t)
            if not ext and int(ident, 16) == 0x580 + NODE:
                if d[0] & 0x02 and d[0] >> 5 == 2:      # expedited upload response
                    n = (d[0] >> 2) & 3 if d[0] & 1 else 0
                    val = int.from_bytes(d[4:8 - n], "little")
                    sdo.append((t, d[1] | d[2] << 8, d[3], val))
            elif ext and int(ident, 16) & 0xFFFF == 0x21CA and int(ident, 16) >> 16 == 0x0CFF:
                rpm = (d[2] | d[3] << 8) - RPM_BIAS
                fnr = d[7] & 0xF
                sign = 1 if fnr == 4 else -1 if fnr == 8 else 0
                mot.append((t, sign * rpm, d[0] | d[1] << 8, sign, (d[7] >> 4) + 1))
    return sdo, mot

def nearest(mot_t, mot, t):
    i = bisect_left(mot_t, t)
    if i == 0: return mot[0]
    if i == len(mot): return mot[-1]
    return mot[i] if mot_t[i] - t < t - mot_t[i - 1] else mot[i - 1]

def corr(xs, ys):
    if len(xs) < 3: return float("nan")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs)); sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0: return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)

def s16(v): return v - 65536 if v > 32767 else v

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asc"); ap.add_argument("-o", "--out")
    ap.add_argument("--top", type=int, default=60)
    a = ap.parse_args()
    sdo, mot = parse(a.asc)
    mot_t = [m[0] for m in mot]
    print(f"{len(sdo)} SDO replies, {len(mot)} FF21CA frames, span {sdo[0][0]:.0f}-{sdo[-1][0]:.0f} s")

    # activity timeline: 30 s bins of |rpm|
    print("\nRPM timeline (30 s bins: max |rpm| / mean torque_raw / share of samples with rpm>0):")
    bins = defaultdict(list)
    for t, rpm, tq, *_ in mot: bins[int(t // 30)].append((abs(rpm), tq))
    for b in sorted(bins):
        v = bins[b]
        print(f"  {b*30:5d}s  rpm_max={max(x[0] for x in v):5d}  tq={statistics.fmean(x[1] for x in v):6.0f}  moving={sum(1 for x in v if x[0] > 0)/len(v):.0%}")

    if a.out:
        with open(a.out, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["t", "index", "sub", "value", "rpm", "torque_raw"])
            for t, idx, sub, val in sdo:
                m = nearest(mot_t, mot, t); w.writerow([f"{t:.3f}", f"0x{idx:04X}", sub, val, m[1], m[2]])

    # per-object series with aligned motor state
    per = defaultdict(list)
    for t, idx, sub, val in sdo:
        if sub == 0: per[idx].append((t, val, nearest(mot_t, mot, t)))
    ref = {n: {t: v for t, v, _ in per.get(i, [])} for n, i in
           (("rpm_co", 0x3207), ("ibat", 0x359E))}

    rows = []
    for idx, ser in per.items():
        vals = [s16(v) for _, v, _ in ser]
        if len(set(vals)) < 2: continue
        rpm = [abs(m[1]) for _, _, m in ser]; tq = [m[2] for _, _, m in ser]
        srpm = [m[1] for _, _, m in ser]
        rows.append((idx, len(vals), len(set(vals)), min(vals), max(vals),
                     corr(vals, rpm), corr(vals, srpm), corr(vals, tq)))
    rows.sort(key=lambda r: -max(abs(r[5]) if r[5] == r[5] else 0, abs(r[7]) if r[7] == r[7] else 0))
    const = len(per) - len(rows)
    print(f"\n{len(per)} objects polled, {const} constant, {len(rows)} varying. Top {a.top} by |corr| (values read as s16):")
    print("  index   n    distinct     min     max   r(|rpm|) r(rpm±)  r(torque)")
    for r in rows[:a.top]:
        print(f"  0x{r[0]:04X} {r[1]:4d} {r[2]:8d} {r[3]:7d} {r[4]:7d}   {r[5]:+.2f}   {r[6]:+.2f}   {r[7]:+.2f}")
    print("\nVarying objects with weak motor correlation (|r|<0.3 for all): candidates for other inputs")
    weak = [r for r in rows if all((x != x) or abs(x) < 0.3 for x in r[5:8])]
    for r in weak:
        print(f"  0x{r[0]:04X} n={r[1]} distinct={r[2]} range={r[3]}..{r[4]}")

if __name__ == "__main__":
    main()
