#!/usr/bin/env python3
"""Analyze a guided canopen_session.csv: find which objects respond to which
machine action (direction, range, throttle, PTO) by comparing per-stage medians
against baseline, with within-stage noise as the significance floor.

    python canopen/analyze_canopen_session.py [canopen/canopen_session.csv]
"""
import csv, os, statistics, sys
from collections import defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "canopen_session.csv")

by_obj = defaultdict(lambda: defaultdict(list))   # obj -> stage -> [values]
stage_order = []
with open(path) as f:
    for r in csv.DictReader(f):
        by_obj[r["index"]][r["stage"]].append(int(r["value"]))
        if r["stage"] not in stage_order:
            stage_order.append(r["stage"])

def med(vals): return statistics.median(vals) if vals else None
def spread(vals): return (max(vals) - min(vals)) if len(vals) > 1 else 0

# per object: stage medians, and a noise floor = max within-stage spread
meds, noise = {}, {}
for obj, byst in by_obj.items():
    meds[obj] = {s: med(byst.get(s, [])) for s in stage_order}
    noise[obj] = max((spread(byst.get(s, [])) for s in stage_order), default=0)

def g(obj, s): return meds[obj].get(s)
def sig(obj, a, b):
    """Is stage a's median different from stage b's beyond this object's noise?"""
    va, vb = g(obj, a), g(obj, b)
    if va is None or vb is None: return False
    return abs(va - vb) > max(noise[obj], 1)

def table(obj, stages):
    return "  ".join(f"{s}={g(obj,s):g}" for s in stages if g(obj,s) is not None)

def drift(obj):  # baseline vs neutral: two rest states, should match for a clean signal
    return abs((g(obj,'baseline') or 0) - (g(obj,'neutral') or 0))

# ---- DIRECTION: forward/reverse differ from neutral, and from each other ----
print("### DIRECTION (F/N/R) candidates — neutral≈baseline, forward≠reverse")
dir_c = []
for obj in by_obj:
    if not ('forward' in meds[obj] and 'reverse' in meds[obj] and 'neutral' in meds[obj]):
        continue
    if drift(obj) > max(noise[obj],1):    # skip drifters (neutral!=baseline)
        continue
    fwd, rev, neu = g(obj,'forward'), g(obj,'reverse'), g(obj,'neutral')
    if sig(obj,'forward','neutral') and sig(obj,'reverse','neutral') and abs(fwd-rev) > max(noise[obj],1):
        score = min(abs(fwd-neu), abs(rev-neu), abs(fwd-rev)) / max(noise[obj],1)
        dir_c.append((score, obj))
for score, obj in sorted(dir_c, reverse=True)[:10]:
    print(f"  {obj}  [score {score:.0f}, noise {noise[obj]}]  "
          f"N={g(obj,'neutral'):g} F={g(obj,'forward'):g} R={g(obj,'reverse'):g}")
if not dir_c: print("  (none)")

# ---- RANGE: r1/r2/r3 mutually distinct (ideally monotonic) ----
print("\n### RANGE (R1/R2/R3) candidates — three distinct values")
rng_c = []
for obj in by_obj:
    r1,r2,r3 = g(obj,'range_r1'), g(obj,'range_r2'), g(obj,'range_r3')
    if None in (r1,r2,r3): continue
    n = max(noise[obj],1)
    if abs(r1-r2)>n and abs(r2-r3)>n and abs(r1-r3)>n:
        mono = (r1<r2<r3) or (r1>r2>r3)
        score = min(abs(r1-r2),abs(r2-r3),abs(r1-r3))/n
        rng_c.append((score, mono, obj))
for score, mono, obj in sorted(rng_c, reverse=True)[:10]:
    print(f"  {obj}  [score {score:.0f}{' MONO' if mono else ''}]  "
          f"R1={g(obj,'range_r1'):g} R2={g(obj,'range_r2'):g} R3={g(obj,'range_r3'):g}")
if not rng_c: print("  (none)")

# ---- THROTTLE: rises quarter->full, returns near baseline at throttle_off ----
print("\n### THROTTLE/RPM/CURRENT candidates — full>quarter>baseline, off≈baseline")
thr_c = []
for obj in by_obj:
    b,tq,tf,toff = g(obj,'baseline'),g(obj,'throttle_quarter'),g(obj,'throttle_full'),g(obj,'throttle_off')
    if None in (b,tq,tf,toff): continue
    n = max(noise[obj],1)
    if abs(tf-b) > n and abs(tf-b) > abs(tq-b) and (tf-b)*(tq-b) >= 0 and abs(toff-b) <= abs(tf-b)/2:
        thr_c.append((abs(tf-b)/n, obj))
for score, obj in sorted(thr_c, reverse=True)[:15]:
    print(f"  {obj}  [score {score:.0f}]  base={g(obj,'baseline'):g} "
          f"1/4={g(obj,'throttle_quarter'):g} full={g(obj,'throttle_full'):g} off={g(obj,'throttle_off'):g}")
if not thr_c: print("  (none)")

# ---- PTO: pto_on != pto_off ----
print("\n### PTO candidates — pto_on ≠ pto_off")
pto_c = []
for obj in by_obj:
    on,off = g(obj,'pto_on'), g(obj,'pto_off')
    if None in (on,off): continue
    n = max(noise[obj],1)
    if abs(on-off) > n:
        pto_c.append((abs(on-off)/n, obj))
for score, obj in sorted(pto_c, reverse=True)[:10]:
    print(f"  {obj}  [score {score:.0f}]  on={g(obj,'pto_on'):g} off={g(obj,'pto_off'):g}")
if not pto_c: print("  (none)")
