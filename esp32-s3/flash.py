#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["esptool>=5"]
# ///
"""Flash a merged firmware image (local path or URL) onto an ESP32-S3.

    uv run flash.py [--erase] [--port /dev/cu.usbmodemXXXX] FIRMWARE

--erase wipes the whole chip first (including NVS, i.e. saved WiFi credentials).

URLs are cached in ~/.cache/solecan-fw and reused on later runs.
"""
import argparse
import glob
import hashlib
import pathlib
import subprocess
import sys
import urllib.request

CACHE = pathlib.Path.home() / ".cache" / "solecan-fw"


def fetch(src: str) -> pathlib.Path:
    if "://" not in src:
        return pathlib.Path(src)
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / (hashlib.sha256(src.encode()).hexdigest()[:12] + "-" + src.rsplit("/", 1)[-1])
    if dest.exists():
        print(f"using cached {dest}")
    else:
        print(f"downloading {src}")
        tmp = dest.with_suffix(".part")
        urllib.request.urlretrieve(src, tmp)
        tmp.rename(dest)
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("firmware", help="path or URL of a *-merged.bin")
    ap.add_argument("--port", help="serial port (default: the single /dev/cu.usbmodem*)")
    ap.add_argument("--baud", default="921600")
    ap.add_argument("--erase", action="store_true", help="erase_flash (incl. NVS) before writing")
    a = ap.parse_args()

    port = a.port
    if not port:
        ports = glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/ttyACM*")
        if len(ports) != 1:
            sys.exit(f"expected one device, found {ports or 'none'}; pass --port")
        port = ports[0]

    fw = fetch(a.firmware)
    if not fw.is_file():
        sys.exit(f"no such file: {fw}")

    base = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port, "--baud", a.baud]
    if a.erase:
        subprocess.run(base + ["erase-flash"], check=True)
    subprocess.run(base + ["write-flash", "0x0", str(fw)], check=True)


if __name__ == "__main__":
    main()
