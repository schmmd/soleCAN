#!/usr/bin/env python3
"""Pull SD-card sessions over the USB console — the /sd/* WiFi API without WiFi.

Drives the firmware's `sd` console command (see "Pulling files over USB" in
esp32-s3/README.md). Works in the `logging` and `slcan` USB modes; not `kelly`.

    sd-pull.py PORT status
    sd-pull.py PORT list
    sd-pull.py PORT get N [-o FILE]      # -> s0000N.tar (exact USTAR, like curl -O -J)
    sd-pull.py PORT delete N

Exit status 1 on any `sd: error …` reply or a short tar stream.
"""
import argparse
import sys
import time

import serial  # pyserial — project dependency


def command(ser, line: str) -> str:
    """Send one `sd …` line and return the first reply line (minus 'sd: ').

    Log/heartbeat lines can arrive before the reply; anything not prefixed
    'sd: ' is skipped. The reply is one line, written by the firmware in a
    single Serial write, so it can't be split by a log line.
    """
    ser.reset_input_buffer()
    ser.write((line + "\r\n").encode())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        raw = ser.readline()
        if raw.startswith(b"sd: "):
            return raw[4:].rstrip(b"\r\n").decode()
    sys.exit("no reply from device (USB port in kelly mode? wrong port?)")


def get(ser, sid: int, out_path: str) -> None:
    reply = command(ser, f"sd get {sid}")
    if not reply.startswith("tar "):
        sys.exit(f"error: {reply}")
    _, _, total = reply.split()
    total = int(total)
    got = 0
    t0 = time.monotonic()
    with open(out_path, "wb") as f:
        while got < total:
            chunk = ser.read(min(65536, total - got))
            if not chunk:   # read timeout: the firmware truncated (card error)
                break
            f.write(chunk)
            got += len(chunk)
    secs = time.monotonic() - t0
    if got < total:
        sys.exit(f"short read: {got} of {total} bytes -> {out_path} (truncated by device)")
    print(f"{out_path}: {total} bytes in {secs:.1f} s ({total / secs / 1024:.0f} KB/s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", help="USB serial port, e.g. /dev/cu.usbmodem101")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("list")
    g = sub.add_parser("get")
    g.add_argument("id", type=int)
    g.add_argument("-o", "--output", help="output file (default s<id>.tar)")
    d = sub.add_parser("delete")
    d.add_argument("id", type=int)
    args = ap.parse_args()

    with serial.Serial(args.port, 115200, timeout=5) as ser:
        if args.cmd == "status":
            print(command(ser, "sd"))
        elif args.cmd == "list":
            print(command(ser, "sd list"))
        elif args.cmd == "get":
            get(ser, args.id, args.output or f"s{args.id:05d}.tar")
        elif args.cmd == "delete":
            reply = command(ser, f"sd delete {args.id}")
            if reply.startswith("error"):
                sys.exit(reply)
            print(reply)


if __name__ == "__main__":
    main()
