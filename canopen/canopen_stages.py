#!/usr/bin/env python3
"""Guided, PASSIVE stage capture against the firmware's CANopen poller.

The RejsaCAN (built with -DCANOPEN_POLL) sweeps the whole dictionary every
~8 s on its own. This script just listens over USB SLCAN, tags every SDO reply
with the current stage label, and ends a stage as soon as --sweeps complete
sweeps have landed inside it (a sweep boundary = the reply for the first table
entry, 0x1000). So a stage lasts 8-16 s, not a fixed timer. Sends nothing.

Run it YOURSELF in your terminal (it's interactive):

    python3 canopen/canopen_stages.py                  # -> canopen/canopen_stages.csv
    python3 canopen/canopen_stages.py --sweeps 2       # two full sweeps per stage
    python3 canopen/canopen_stages.py --stages fast --sweeps 60   # -DCANOPEN_FAST firmware
    python3 canopen/canopen_stages.py --stages seat --secs 10     # seat timing, 10 s per stage

Tractor keyed on, motor STOPPED for every stage: with the motor still, the
~150 RPM/current-driven objects stay quiet, so whatever moves belongs to the
one input you changed. After each stage it prints the movers vs baseline.
Ctrl-C saves and exits. The same replies also land on the SD card.
"""
import argparse, csv, os, statistics, sys, time
import can
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from canopen_dump import switch_to_slcan, NODE, PORT
from canopen_session import summarize

FIRST_INDEX = 0x1000   # kCanopenObjects[0]; its reply marks a sweep boundary

STAGES = [
    ("baseline",       "Key on, NEUTRAL, foot off everything, seated, PTO off, hydraulics as-is. Sit still."),
    ("brake_held",     "Press and HOLD the brake pedal."),
    ("brake_released", "Release the brake."),
    ("pbrake_on",      "Set the PARKING brake."),
    ("pbrake_off",     "Release the parking brake."),
    ("seat_empty",     "Stand up off the seat (keep key on)."),
    ("seat_back",      "Sit back down."),
    ("hyd_off",        "Hydraulics switch OFF (if it was on)."),
    ("hyd_on",         "Hydraulics switch ON."),
    ("pto_on",         "Engage PTO."),
    ("pto_off",        "Disengage PTO."),
    ("lights_on",      "Work lights ON."),
    ("lights_off",     "Work lights OFF."),
    ("lever_f",        "Lever to FORWARD, no throttle."),
    ("lever_r",        "Lever to REVERSE, no throttle."),
    ("lever_n",        "Lever back to NEUTRAL."),
    ("range_r1",       "Range switch to R1."),
    ("range_r2",       "Range switch to R2."),
    ("range_r3",       "Range switch to R3."),
]

# For the -DCANOPEN_FAST firmware (~15 sweeps/s): timing-order experiments.
# Use --sweeps 60 or so (~4 s per stage) and CHANGE THE INPUT AFTER PRESSING
# ENTER, so the transition itself is inside the stage.
FAST_STAGES = [
    ("baseline",      "Key on, NEUTRAL, R3, seated, foot off. Sit still. (Enter, then hold)"),
    ("seat_empty",    "Press Enter, THEN stand up off the seat within 2 s."),
    ("seat_back",     "Press Enter, THEN sit back down."),
    ("lever_f",       "Press Enter, THEN move the lever to FORWARD (no throttle)."),
    ("lever_n",       "Press Enter, THEN lever back to NEUTRAL."),
    ("throttle_r3",   "FORWARD, R3. Press Enter, THEN press the pedal 1/4 for 2 s and release."),
    ("throttle_r1",   "Lever N, switch to R1, lever F. Press Enter, THEN pedal 1/4 for 2 s, release."),
    ("throttle_r2",   "Same in R2. Press Enter, THEN pedal 1/4 for 2 s, release."),
    ("roll_slow",     "R1, FORWARD. Press Enter, THEN creep forward as slowly as possible ~3 s."),
    ("stop",          "Release, lever N. Sit still."),
]

# Seat / operator-presence timing, for -DCANOPEN_FAST with --secs 10: the
# fast run showed the Curtis RESETTING a few seconds after leaving the seat,
# and Max_Speed staying at its 1200 rpm power-up value until the lever moves.
SEAT_STAGES = [
    ("baseline",      "Key on, NEUTRAL, R3, seated, foot off. Sit still."),
    ("seat_empty",    "Press Enter, THEN stand up within 2 s. Stay up."),
    ("seat_empty_2",  "Still standing. (Enter, keep standing)"),
    ("seat_back",     "Press Enter, THEN sit down within 2 s."),
    ("seat_back_2",   "Still seated, lever untouched. (Enter, hold)"),
    ("lever_f",       "Press Enter, THEN lever to FORWARD (no throttle)."),
    ("seat_empty_f",  "Lever stays in F. Press Enter, THEN stand up within 2 s. Stay up."),
    ("seat_back_f",   "Press Enter, THEN sit down. Lever still F."),
    ("lever_n",       "Press Enter, THEN lever to NEUTRAL."),
    ("lever_f_2",     "Press Enter, THEN lever to FORWARD again."),
    ("lever_n_2",     "Press Enter, THEN lever to NEUTRAL. Done."),
]

def parse_reply(msg):
    """(index, sub, value) for an expedited SDO upload reply from our node, else None."""
    if msg.is_extended_id or msg.arbitration_id != 0x580 + NODE or len(msg.data) < 8:
        return None
    d = msg.data
    if d[0] >> 5 != 2 or not d[0] & 0x02:
        return None
    n = (d[0] >> 2) & 3 if d[0] & 1 else 0
    return d[1] | d[2] << 8, d[3], int.from_bytes(d[4:8 - n], "little")

class Stage:
    """Collects replies until `sweeps` complete sweeps have been seen, or —
    with `secs` — until the first sweep boundary after `secs` seconds."""
    def __init__(self, sweeps, secs=None, t0=0.0):
        self.need = sweeps
        self.secs, self.t0, self.now = secs, t0, t0
        self.boundaries = 0
        self.samples = {}
        self.rows = []
    def feed(self, t, reply):
        index, sub, value = reply
        if index == FIRST_INDEX and sub == 0:
            self.boundaries += 1
        if self.boundaries >= 1:                       # only count samples inside a full sweep
            self.samples.setdefault((index, sub), []).append(value)
        self.rows.append((t, index, sub, value))
        self.now = t
    @property
    def done(self):
        if self.secs is not None:
            return self.boundaries >= 2 and self.now - self.t0 >= self.secs \
                and self.rows[-1][1] == FIRST_INDEX
        return self.boundaries > self.need

def record_stage(bus, label, sweeps, writer, timeout=60.0, secs=None):
    # Drain what queued in the serial buffer while the operator read the
    # prompt — otherwise a stage starts with up to ~4000 pre-change replies.
    # Time-bounded: on a live bus recv() never returns None, so "read until
    # empty" would spin forever. 0.3 s is enough to swallow the backlog at
    # USB speed, and any live frames it eats are pre-transition anyway.
    t_drain = time.monotonic() + 0.3
    while time.monotonic() < t_drain:
        bus.recv(timeout=0.02)
    t0 = time.monotonic()
    st = Stage(sweeps, secs, t0)
    while not st.done:
        msg = bus.recv(timeout=1.0)
        if time.monotonic() - t0 > timeout:
            print("  (timeout: is the tractor keyed on and the poller running?)")
            break
        if msg is None:
            continue
        r = parse_reply(msg)
        if r is None:
            continue
        st.feed(time.monotonic(), r)
        if r[0] == FIRST_INDEX and r[1] == 0:
            sys.stdout.write(f" sweep{st.boundaries}"); sys.stdout.flush()
    for t, index, sub, value in st.rows:
        writer.writerow([label, f"{t:.3f}", f"0x{index:04X}", f"0x{sub:02X}", value])
    print(f"  [{len(st.rows)} replies, {time.monotonic() - t0:.0f} s]")
    return st.samples

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweeps", type=int, default=1, help="complete sweeps per stage")
    ap.add_argument("--out", default=os.path.join(HERE, "canopen_stages.csv"))
    ap.add_argument("--port", default=PORT)
    ap.add_argument("--secs", type=float, default=None,
                    help="record each stage for this long (ends at the next sweep boundary) instead of --sweeps")
    ap.add_argument("--stages", choices=("full", "fast", "seat"), default="full",
                    help="fast / seat = stage lists for the -DCANOPEN_FAST firmware")
    args = ap.parse_args()
    stages = {"full": STAGES, "fast": FAST_STAGES, "seat": SEAT_STAGES}[args.stages]

    switch_to_slcan(args.port)
    base_med, base_spread = {}, {}
    f = open(args.out, "w", newline="")
    writer = csv.writer(f)
    writer.writerow(["stage", "t_mono", "index", "sub", "value"])
    try:
        with can.Bus(interface="slcan", channel=args.port, bitrate=250000) as bus:
            for label, instr in stages:
                print(f"\n=== {label} ===\n  SET: {instr}")
                ans = input("  Press Enter when set  (s=skip, q=quit): ").strip().lower()
                if ans == "q":
                    break
                if ans == "s":
                    continue
                samples = record_stage(bus, label, args.sweeps, writer, secs=args.secs)
                f.flush()
                if label == "baseline":
                    for obj, vals in samples.items():
                        base_med[obj] = statistics.median(vals)
                        base_spread[obj] = (max(vals) - min(vals)) if len(vals) > 1 else 0
                    print(f"  baseline set ({len(base_med)} objects).")
                else:
                    summarize(label, samples, base_med, base_spread)
    except KeyboardInterrupt:
        print("\n[interrupted]")
    finally:
        f.close()
        print(f"\nSaved {args.out}")

def _selftest():
    st = Stage(1, secs=1.0, t0=0.0)
    for t, r in [(0.1, (FIRST_INDEX, 0, 0)), (0.5, (0x2000, 0, 1)), (0.9, (FIRST_INDEX, 0, 0)), (1.2, (0x2000, 0, 1))]:
        st.feed(t, r); assert not st.done
    st.feed(1.3, (FIRST_INDEX, 0, 0)); assert st.done            # first boundary after secs
    st = Stage(1)
    seq = [(0x2000, 0, 1), (FIRST_INDEX, 0, 0), (0x2000, 0, 2), (FIRST_INDEX, 0, 0)]
    for r in seq:
        assert not st.done
        st.feed(0.0, r)
    assert st.done and st.samples[(0x2000, 0)] == [2], st.samples   # pre-boundary sample excluded
    assert parse_reply(can.Message(arbitration_id=0x5A8, is_extended_id=False,
                                   data=[0x4B, 0x07, 0x32, 0, 0x10, 0x27, 0, 0])) == (0x3207, 0, 10000)
    print("selftest ok")

if __name__ == "__main__":
    _selftest() if "--selftest" in sys.argv else main()
