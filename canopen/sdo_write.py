#!/usr/bin/env python3
"""Guarded expedited SDO WRITE (download) of one Curtis parameter, RAM only.

    python3 canopen/sdo_write.py 0x3104               # read: value, min, max, EE-write flag
    python3 canopen/sdo_write.py 0x3104 2000 --apply    # write 2000, read back
    python3 canopen/sdo_write.py 0x3104 2240 --apply    # put it back (or just key-cycle)

Guards, all must pass before a single write frame goes out:
  1. 0x332F CAN_EE_Writes_Enabled == 0  -> the write is VOLATILE (RAM), gone at
     the next key cycle. Refuses otherwise; this script never touches 0x332F.
  2. Motor stopped (0x3207 == 0) and lever in neutral (0x33E6 dir bits == 0).
  3. The object is a PARAMETER (sub 3/4 present) and value is within [min, max].
  4. --apply on the command line.
Writes 16-bit values (every parameter in canopen_params.csv is <= 16 bit).
Expect the Curtis may log fault 49/99 "Parameter Change Fault" for some
parameters; that is a key-cycle-to-clear safety fault, not damage.
The firmware poller keeps running; replies are matched by index/sub so its
traffic can't be mistaken for ours.

DEFAULT TARGET IN THE EXAMPLES: 0x3104, the R3 reverse speed cap (2240 rpm,
range 0..6000). The VCL copies the table entry into 0x3011 Max_Speed when
R3 + reverse is selected, so the effect shows there — not in 0x3104 alone.
"""
import argparse, os, sys, time
import can
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from canopen_dump import switch_to_slcan, NODE, PORT

REQ, RESP = 0x600 + NODE, 0x580 + NODE

def xfer(bus, data, timeout=0.3, tries=5):
    """Send one SDO request; return the matching reply's data (same index/sub).

    Retries: the firmware poller shares the controller (~200 req/s) and the
    Curtis services one SDO at a time, so a request can be dropped silently.
    """
    for attempt in range(tries):
        bus.send(can.Message(arbitration_id=REQ, is_extended_id=False, data=data))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            m = bus.recv(timeout=max(0.0, end - time.monotonic()))
            if m and not m.is_extended_id and m.arbitration_id == RESP and bytes(m.data[1:4]) == bytes(data[1:4]):
                if attempt: print(f"  (reply after {attempt + 1} tries)")
                return bytes(m.data)
    return None

def read(bus, index, sub=0):
    d = xfer(bus, [0x40, index & 0xFF, index >> 8, sub, 0, 0, 0, 0])
    if d is None: return None
    if d[0] == 0x80: return ("abort", int.from_bytes(d[4:8], "little"))
    n = (d[0] >> 2) & 3 if d[0] & 1 else 0
    return int.from_bytes(d[4:8 - n], "little")

def write16(bus, index, value, sub=0):
    # A write is retried too: the Curtis acks with 0x60 only once it has taken
    # the value, and repeating an identical write is idempotent.
    d = xfer(bus, [0x2B, index & 0xFF, index >> 8, sub, value & 0xFF, (value >> 8) & 0xFF, 0, 0])
    if d is None: return "no reply (5 tries)"
    if d[0] == 0x80: return f"abort 0x{int.from_bytes(d[4:8], 'little'):08X}"
    return "ok" if d[0] == 0x60 else f"unexpected scs 0x{d[0]:02X}"

def motor_state(bus, secs=1.0):
    """(rpm, dir) from the J1939 FF21CA broadcast, or None if not seen."""
    end = time.monotonic() + secs
    while time.monotonic() < end:
        m = bus.recv(timeout=0.2)
        if m and m.is_extended_id and (m.arbitration_id & 0x00FFFFFF) == 0xFF21CA:
            return (m.data[2] | m.data[3] << 8) - 0x0C80, m.data[7] & 0x0F
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("index", type=lambda x: int(x, 0))
    ap.add_argument("value", type=int, nargs="?")
    ap.add_argument("--apply", action="store_true", help="actually write")
    ap.add_argument("--port", default=PORT)
    a = ap.parse_args()

    switch_to_slcan(a.port)
    with can.Bus(interface="slcan", channel=a.port, bitrate=250000) as bus:
        ee = read(bus, 0x332F)
        cur, lo, hi = read(bus, a.index), read(bus, a.index, 3), read(bus, a.index, 4)
        print(f"0x{a.index:04X}: value={cur}  min={lo}  max={hi}   CAN_EE_Writes_Enabled={ee}")
        if a.value is None:
            return
        problems = []
        if ee != 0: problems.append(f"0x332F = {ee}: EEPROM writes are ENABLED; refusing (never write with this set)")
        if not isinstance(lo, int) or not isinstance(hi, int): problems.append("no min/max sub-indices: not a parameter")
        elif not lo <= a.value <= hi: problems.append(f"value {a.value} outside [{lo}, {hi}]")
        ms = motor_state(bus)
        if ms is None: problems.append("no FF21CA seen: tractor keyed off? refusing")
        else:
            rpm, fnr = ms
            if rpm != 0: problems.append(f"motor turning ({rpm} rpm)")
            if fnr != 0: problems.append(f"lever not in neutral (data[7] low nibble 0x{fnr:X})")
        if problems:
            print("REFUSED:\n  " + "\n  ".join(problems)); sys.exit(2)
        if not a.apply:
            print(f"dry run: would write {a.value} to 0x{a.index:04X} (RAM only). Add --apply."); return
        print(f"writing {a.value} -> 0x{a.index:04X} ... ", end="", flush=True)
        print(write16(bus, a.index, a.value))
        time.sleep(0.2)
        print(f"read back: 0x{a.index:04X} = {read(bus, a.index)}   0x3011 Max_Speed = {read(bus, 0x3011)}   UserFault1 0x3238 = {read(bus, 0x3238)}")
        print("Volatile: a key cycle restores the EEPROM value. Watch the cluster for a fault code.")

if __name__ == "__main__":
    main()
