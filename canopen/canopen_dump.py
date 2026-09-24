#!/usr/bin/env python3
"""Dump the Curtis motor controller's CANopen object dictionary to stdout.

Scans SDO-readable objects at node 0x28 (confirmed Curtis, vendor "CI") over the
250k bus via the TX-armed RejsaCAN in slcan mode, and prints every value it can
read, one line per subindex, as it finds it. Read-only (SDO upload only).

    python canopen/canopen_dump.py > canopen/canopen/canopen.txt  # watch it stream; Ctrl-C anytime
    python canopen/canopen_dump.py --start 0x2000 --end 0x2FFF > canopen/canopen.txt

ponytail: expedited SDO only (values <=4 bytes). Segmented objects (strings,
long records) are printed as "segmented(size)" but not fetched.
"""
import argparse, sys, time
import serial
import can

PORT = "/dev/cu.usbmodem14301"
NODE = 0x28

ABORT_NO_OBJECT = 0x06020000  # object does not exist
ABORT_NO_SUB    = 0x06090011  # sub-index does not exist
MAX_SUB = 64                  # cap array/record enumeration

def switch_to_slcan(port):
    with serial.Serial(port, 115200, timeout=0.5) as s:
        s.write(b"mode slcan\r\n"); time.sleep(0.3); s.read(200)

def sdo_upload(bus, index, sub, timeout=0.06):
    """One expedited SDO upload. Returns a result dict, or None on no reply."""
    bus.send(can.Message(arbitration_id=0x600 + NODE, is_extended_id=False,
                         data=[0x40, index & 0xFF, index >> 8, sub, 0, 0, 0, 0]))
    # Wait up to `timeout` total for OUR reply, discarding the busy J1939 traffic.
    # (Deadline-bounded: recv rarely returns None on a live bus, so we must not
    # loop on "m is not None" or a missing reply spins forever.)
    deadline = time.monotonic() + timeout
    m = None
    while time.monotonic() < deadline:
        rx = bus.recv(timeout=max(0.0, deadline - time.monotonic()))
        if rx is None:
            break
        if not rx.is_extended_id and rx.arbitration_id == 0x580 + NODE:
            m = rx
            break
    if m is None:
        return None
    d = bytes(m.data)
    scs = d[0] >> 5
    if d[0] == 0x80 or scs == 4:               # abort
        return {"abort": int.from_bytes(d[4:8], "little")}
    if d[0] & 0x02:                            # expedited upload
        n = (d[0] >> 2) & 0x3 if d[0] & 0x01 else 0
        payload = d[4:4 + (4 - n)]
        out = {"value": int.from_bytes(payload, "little"), "raw": d.hex()}
        if payload and all(32 <= b < 127 for b in payload):
            out["ascii"] = payload.decode()
        return out
    size = int.from_bytes(d[4:8], "little") if d[0] & 0x01 else None  # segmented
    return {"segmented": size, "raw": d.hex()}

def fmt(index, sub, res):
    head = f"0x{index:04X}:0x{sub:02X}"
    if "value" in res:
        s = f"{head}  value={res['value']} (0x{res['value']:X})  raw={res['raw']}"
        if "ascii" in res:
            s += f"  ascii={res['ascii']!r}"
        return s
    if "segmented" in res:
        return f"{head}  segmented({res['segmented']})  raw={res['raw']}"
    return f"{head}  abort=0x{res['abort']:08X}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=lambda x: int(x, 0), default=0x1000)
    ap.add_argument("--end",   type=lambda x: int(x, 0), default=0x6FFF)
    ap.add_argument("--port",  default=PORT)
    args = ap.parse_args()

    switch_to_slcan(args.port)
    print(f"# node 0x{NODE:02X}  vendor CI (Curtis Instruments)  "
          f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}", flush=True)
    print(f"# scan 0x{args.start:04X}..0x{args.end:04X}", flush=True)

    found = 0
    with can.Bus(interface="slcan", channel=args.port, bitrate=250000) as bus:
        for index in range(args.start, args.end + 1):
            if index % 0x200 == 0:
                print(f"...scanning 0x{index:04X} ({found} objects so far)",
                      file=sys.stderr, flush=True)
            sub0 = sdo_upload(bus, index, 0)
            if sub0 is None or sub0.get("abort") == ABORT_NO_OBJECT:
                continue                                     # object absent
            # Object present. Subindices are sparse and missing ones abort with a
            # non-standard code, so scan the whole range and print only real hits.
            hit = False
            for sub in range(0, MAX_SUB + 1):
                res = sub0 if sub == 0 else sdo_upload(bus, index, sub)
                if res and ("value" in res or "segmented" in res):
                    print(fmt(index, sub, res), flush=True)
                    hit = True
            if hit:
                found += 1
    print(f"# done: {found} objects", flush=True)

if __name__ == "__main__":
    main()
