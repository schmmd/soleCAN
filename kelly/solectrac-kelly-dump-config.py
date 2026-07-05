#!/usr/bin/env python3
"""Read-only dump of the Solectrac e25G e-hydraulic Kelly KLS pump
controller's stored configuration (the 512-byte flash calibration region),
over its SM-4P serial port via a USB-serial or Bluetooth-SPP adapter.

READ-ONLY BY CONSTRUCTION
-------------------------
This tool transmits exactly three command codes: 0x11 (code version), 0xF1
(open flash session) and 0xF2 (read one 16-byte flash block). Every byte
written to the port passes through a single `_transmit()` choke-point that
refuses any command outside ``ALLOWED_COMMANDS``, so the write, erase and
burn commands (0xF3, 0xF4, 0xB1..0xB4) can never be sent and the stored
configuration cannot be altered.

Reading does require opening a flash session (0xF1) first — the protocol
defines block reads only inside one. No close is needed or sent: the
close command 0xF4 doubles as the flash *burn* (commit), and the official
Kelly app only ever sends it after a write. After a pure read the app simply
leaves the session open (it does this on every connect, to detect the
controller model), and so does this tool. The session clears when the
controller powers off.

Protocol (Kelly "ETS", 19200 8N1)
---------------------------------
    frame     = [CMD][LEN][DATA 0..16][CHECKSUM]
    checksum  = (CMD + LEN + sum(DATA)) & 0xFF
    open      = [0xF1, 0x00, 0xF1]              (zero-data query)
    read      = [0xF2, 0x03, lo, 0x10, hi, ck]  reply [0xF2, 0x10, <16 B>, ck]
                where lo/hi are the low/high bytes of the block address;
                32 blocks of 16 bytes cover the whole 512-byte region.

The wire protocol and the KBLS_0109 parameter map come from the community
kelly-connect-oss project (PROTOCOL.md and the protocol/ Kotlin module); the
vendor-sheet reference values come from the Compage "Controller Change
Document" for the KLS7218MC/NC hydraulic pump controller (PDF at the repo
root). The parameter map and field meanings are cross-checked against the
Kelly app's AC Calibration screens on this tractor. See kelly/README.md.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("This tool needs pyserial:  pip install pyserial")


BAUD = 19200

CMD_CODE_VERSION = 0x11
CMD_FLASH_OPEN = 0xF1
CMD_FLASH_READ = 0xF2

# The ONLY command codes this tool is permitted to transmit. The flash
# write/burn/erase command set (0xF3, 0xF4, 0xB1..0xB4) is deliberately
# excluded so the stored configuration can never be touched. _transmit()
# enforces this allowlist on every outbound frame.
ALLOWED_COMMANDS = frozenset({CMD_CODE_VERSION, CMD_FLASH_OPEN, CMD_FLASH_READ})

FLASH_SIZE = 512
BLOCK_SIZE = 16
BLOCK_COUNT = FLASH_SIZE // BLOCK_SIZE  # 32


class KellyError(Exception):
    """A read or framing failure talking to the controller."""


class NotReadOnly(KellyError):
    """A code path attempted to transmit a command outside the allowlist."""


def checksum(frame: bytes) -> int:
    return sum(frame) & 0xFF


def build_tx(cmd: int, data: bytes = b"") -> bytes:
    if len(data) > 16:
        raise ValueError("ETS data payload is at most 16 bytes")
    body = bytes([cmd, len(data)]) + data
    return body + bytes([checksum(body)])


# ---------------------------------------------------------------------------
# Parameter map: KBLS_0109 firmware family (version word >= 265; this
# tractor reads 0x0206 = 518). Offsets and encodings from kelly-connect-oss
# ParameterDefinitions.kt; names as the Kelly app displays them.
#
# size: "bit" (pos = bit position), "byte", or "word" (pos = byte count - 1,
# big-endian). fmt: "u" unsigned int, "h" hex string, "a" ASCII. ro marks
# fields the app treats as read-only. vendor is the KLS7218MC factory value
# from the Compage vendor sheet (None where the sheet has no entry or the
# value is unit-specific, like the serial number).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Param:
    name: str
    category: str
    offset: int
    size: str
    pos: int = 0
    fmt: str = "u"
    ro: bool = False
    vendor: object = None


PARAMS_KBLS_0109 = (
    Param("Module Name", "General", 0, "word", 7, "a", ro=True),
    Param("User Name", "General", 8, "word", 3, "a", ro=True, vendor="bzbb"),
    Param("Serial Number", "General", 12, "word", 3, "h", ro=True),
    Param("Software Version", "General", 16, "word", 3, "h", ro=True),
    Param("Startup H-Pedel", "General", 20, "bit", 0, vendor=1),
    Param("Brake H-Pedel", "General", 20, "bit", 1, vendor=0),
    Param("NTL H-Pedel", "General", 20, "bit", 2, vendor=0),
    Param("Joystick", "General", 20, "bit", 4, vendor=0),
    Param("Three Gears Switch", "General", 20, "bit", 6, vendor=0),
    Param("Boost", "General", 20, "bit", 7, vendor=0),
    Param("Foot Switch", "General", 21, "bit", 0, vendor=0),
    Param("SW Level", "General", 21, "bit", 1, ro=True, vendor=1),
    Param("0,HIM;1,KIM", "General", 21, "bit", 3, ro=True, vendor=1),
    Param("Cruise", "General", 21, "bit", 4, vendor=0),
    Param("Anti-theft", "General", 21, "bit", 5, vendor=0),
    Param("Anti slip", "General", 21, "bit", 6, vendor=0),
    Param("Change Dir", "General", 21, "bit", 7, vendor=0),
    Param("Controller Volt", "Protection", 23, "word", 1, ro=True),
    Param("Low Volt", "Protection", 25, "word", 1, vendor=24),
    Param("Over Volt", "Protection", 27, "word", 1, vendor=85),
    Param("Hall Galvan Rate", "Protection", 29, "word", 1, ro=True, vendor=360),
    Param("PhaseCurr Max AD", "Protection", 31, "word", 1, ro=True, vendor=300),
    Param("Motor_Current%", "Protection", 37, "byte", vendor=50),
    Param("Batt_Current%", "Protection", 38, "byte", vendor=50),
    Param("Identify Angle", "Motor", 56, "byte", vendor=85),
    Param("Brake SW Level", "Braking", 82, "byte", ro=True, vendor=1),
    Param("TPS Low", "Throttle", 92, "byte", vendor=0),
    Param("TPS High", "Throttle", 93, "byte", vendor=95),
    Param("TPS Type", "Throttle", 95, "byte", vendor=1),
    Param("TPS Dead Low", "Throttle", 96, "byte", vendor=20),
    Param("TPS Dead High", "Throttle", 97, "byte", vendor=80),
    Param("TPS Forw MAP", "Throttle", 98, "byte", vendor=30),
    Param("TPS Rev MAP", "Throttle", 99, "byte", vendor=20),
    Param("Brake Type", "Braking", 100, "byte", vendor=0),
    Param("Brake Dead Low", "Braking", 101, "byte", vendor=20),
    Param("Brake Dead High", "Braking", 102, "byte", vendor=80),
    Param("Max Output Fre", "Speed", 105, "word", 1, vendor=1000),
    Param("Max Speed", "Speed", 107, "word", 1, vendor=2800),
    Param("Max Forw Speed%", "Speed", 109, "byte", vendor=85),
    Param("Max Rev Speed%", "Speed", 110, "byte", vendor=85),
    Param("MidSpeed Forw Speed", "Speed", 111, "byte", vendor=55),
    Param("MidSpeed Rev Speed", "Speed", 112, "byte", vendor=55),
    Param("LowSpeed Forw Speed", "Speed", 113, "byte", vendor=30),
    Param("LowSpeed Rev Speed", "Speed", 114, "byte", vendor=20),
    Param("Three Speed", "Speed", 126, "byte", vendor=1),
    Param("PWM frequency", "Speed", 127, "byte", vendor=16),
    Param("IQ Kp", "PID tuning", 128, "word", 1, vendor=500),
    Param("IQ Ki", "PID tuning", 130, "word", 1, vendor=10),
    Param("ID Kp", "PID tuning", 134, "word", 1, vendor=1500),
    Param("ID Ki", "PID tuning", 136, "word", 1, vendor=30),
    Param("ID Err", "PID tuning", 138, "word", 1),
    Param("HS_ACQR_Kp", "Advanced", 200, "word", 1),
    Param("HS_ACQR_Ki", "Advanced", 202, "word", 1),
    Param("HS_ACDR_Kp", "Advanced", 204, "word", 1),
    Param("HS_ACDR_Ki", "Advanced", 206, "word", 1),
    Param("BRK_AD Brk %#", "Braking", 226, "byte", vendor=0),
    Param("Anti-theft Curr#", "Braking", 227, "byte", vendor=15),
    Param("Brk_Speed Limit", "Braking", 228, "word", 1),
    Param("RLS_TPS Brk Per%", "Braking", 230, "byte", vendor=0),
    Param("NTL Brk Per%", "Braking", 231, "byte", vendor=0),
    Param("Accel Time", "Speed", 239, "byte", vendor=150),
    Param("Accel Release Time", "Speed", 240, "byte", vendor=5),
    Param("Brake Time", "Speed", 241, "byte", vendor=5),
    Param("Brake Release Time", "Speed", 242, "byte", vendor=1),
    Param("BRK_SW Brk Per%", "Braking", 243, "byte", vendor=10),
    Param("Change Dir Brk%", "Braking", 244, "byte", vendor=0),
    Param("Compensation Per%", "Braking", 245, "byte", vendor=20),
    Param("IVT BRK Max", "Braking", 246, "word", 1, vendor=50),
    Param("IVT BRK Min", "Braking", 248, "word", 1, vendor=50),
    Param("Torque Speed Kp", "PID tuning", 250, "word", 1, vendor=2000),
    Param("Torque Speed Ki", "PID tuning", 252, "word", 1, vendor=60),
    Param("Speed Err Limit", "PID tuning", 254, "word", 1, vendor=1000),
    Param("Motor Nominal Curr", "Motor", 258, "word", 1, vendor=80),
    Param("Motor Poles", "Motor", 268, "byte", vendor=10),
    Param("Speed Sensor Type", "Motor", 269, "byte", vendor=2),
    Param("Resolver Poles", "Motor", 272, "byte", vendor=2),
    Param("Min Excitation Curr", "Motor", 310, "word", 1, vendor=0),
    Param("Motor Temp Sensor", "Motor", 318, "byte", vendor=1),
    Param("High Temp Cut C", "Protection", 319, "byte", vendor=110),
    Param("High Temp Resume", "Protection", 320, "byte", vendor=90),
    Param("High Temp Str C", "Protection", 321, "byte", vendor=100),
    Param("High Temp Week %", "Protection", 322, "byte", vendor=0),
    Param("Line Hall Zero", "Motor", 332, "word", 1, vendor=508),
    Param("Line Hall amplitude", "Motor", 334, "word", 1, vendor=410),
    Param("Line Hall High Err", "Motor", 336, "word", 1, vendor=972),
    Param("Line Hall Low Err", "Motor", 338, "word", 1, vendor=50),
    Param("Swap Motor Phase", "Motor", 340, "byte", vendor=0),
    Param("Resolver init angle", "Motor", 341, "word", 1, vendor=8129),
    Param("0 deg Hall", "Motor", 343, "byte", vendor=6),
    Param("60 deg Hall", "Motor", 344, "byte", vendor=2),
    Param("120 deg Hall", "Motor", 345, "byte", vendor=3),
    Param("180 deg Hall", "Motor", 346, "byte", vendor=1),
    Param("240 deg Hall", "Motor", 347, "byte", vendor=5),
    Param("300 deg Hall", "Motor", 348, "byte", vendor=4),
    Param("Forw A Rise Hall", "Motor", 349, "byte", vendor=5),
    Param("Forw A Fall Hall", "Motor", 350, "byte", vendor=2),
    Param("Rev A Rise Hall", "Motor", 351, "byte", vendor=6),
    Param("Rev A Fall Hall", "Motor", 352, "byte", vendor=1),
)

CATEGORY_ORDER = ("General", "Protection", "Throttle", "Braking", "Speed",
                  "PID tuning", "Advanced", "Motor")

# Model detection threshold for the version word at offset 16..17
# (big-endian): >= 265 is the KBLS_0109 91-parameter map decoded here;
# 262..264 is the older KBLS_0106 map, which this tool does not decode.
KBLS_0109_MIN_VERSION = 265


def read_param(flash: bytes, p: Param):
    if p.size == "bit":
        return (flash[p.offset] >> p.pos) & 1
    n = 1 if p.size == "byte" else p.pos + 1
    chunk = flash[p.offset:p.offset + n]
    if p.fmt == "a":
        return chunk.decode("ascii", "replace").strip("\x00").strip()
    if p.fmt == "h":
        return chunk.hex()
    return int.from_bytes(chunk, "big")


def version_word(flash: bytes) -> int:
    return (flash[16] << 8) | flash[17]


class KellyConfigDumper:
    debug = False  # class default so instances built with __new__ stay valid

    def __init__(self, port: str, baud: int = BAUD, read_timeout: float = 0.6,
                 debug: bool = False):
        self.port = port
        self.baud = baud
        self.read_timeout = read_timeout
        self.debug = debug
        self.ser = self._open()

    def _open(self) -> "serial.Serial":
        return serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.15,
        )

    def reopen(self, retries: int = 5, delay: float = 1.0) -> None:
        """Close and reopen the port — used to recover a dropped Bluetooth link."""
        try:
            self.ser.close()
        except Exception:
            pass
        last = None
        for _ in range(retries):
            try:
                self.ser = self._open()
                return
            except serial.SerialException as e:
                last = e
                time.sleep(delay)
        raise KellyError(f"could not reopen {self.port}: {last}")

    def close(self) -> None:
        self.ser.close()

    def __enter__(self) -> "KellyConfigDumper":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _transmit(self, cmd: int, data: bytes = b"") -> None:
        # The one place bytes leave for the controller. This guard is what
        # keeps the tool read-only: a write/erase/burn command can never
        # reach here.
        if cmd not in ALLOWED_COMMANDS:
            raise NotReadOnly(f"refusing to transmit non-read-only command 0x{cmd:02X}")
        frame = build_tx(cmd, data)
        if self.debug:
            print(f"    TX {frame.hex(' ')}", file=sys.stderr)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()

    def _read_response(self, expected_cmd: int) -> bytes:
        # Resync on the echoed command byte, then read LEN + DATA + CHECKSUM
        # and validate. Bad/garbage frames are skipped until the deadline.
        deadline = time.monotonic() + self.read_timeout
        seen = bytearray()  # everything read this call, for --debug diagnosis
        while time.monotonic() < deadline:
            first = self.ser.read(1)
            if not first:
                continue
            seen += first
            if first[0] != expected_cmd:
                continue
            length_byte = self.ser.read(1)
            if not length_byte:
                continue
            seen += length_byte
            length = length_byte[0]
            rest = self.ser.read(length + 1)
            seen += rest
            if len(rest) < length + 1:
                continue
            data, rx_checksum = rest[:length], rest[length]
            frame = bytes([expected_cmd, length]) + data
            if checksum(frame) == rx_checksum:
                return data
        if self.debug:
            got = bytes(seen).hex(" ") if seen else "(nothing)"
            print(f"    RX no valid 0x{expected_cmd:02X} frame; saw: {got}",
                  file=sys.stderr)
        raise KellyError(f"no valid response to command 0x{expected_cmd:02X}")

    def _query(self, cmd: int, data: bytes = b"") -> bytes:
        self._transmit(cmd, data)
        return self._read_response(cmd)

    def code_version(self) -> str:
        """Best-effort firmware/code-version string."""
        data = self._query(CMD_CODE_VERSION)
        return " ".join(f"{x:02X}" for x in data)

    def open_flash(self) -> None:
        """Open the flash session. Required before block reads; there is no
        matching close — see the module docstring."""
        self._query(CMD_FLASH_OPEN)

    def read_flash(self) -> bytes:
        """Read the full 512-byte configuration region, 16 bytes per query."""
        flash = bytearray()
        for block in range(BLOCK_COUNT):
            addr = block * BLOCK_SIZE
            payload = bytes([addr & 0xFF, BLOCK_SIZE, (addr >> 8) & 0xFF])
            data = self._query(CMD_FLASH_READ, payload)
            if len(data) != BLOCK_SIZE:
                raise KellyError(
                    f"flash block {block}: expected {BLOCK_SIZE} bytes, got {len(data)}")
            flash += data
        return bytes(flash)

    def dump(self) -> tuple[str, bytes]:
        """One pass: open the session, read all 512 bytes, then grab the
        version (best-effort). No retry — a failure surfaces immediately.
        Open first, since it's the first exchange that must land before the
        32 block reads."""
        self.open_flash()
        flash = self.read_flash()
        try:
            version = self.code_version()
        except KellyError:
            version = ""  # some firmware doesn't answer 0x11; harmless
        return version, flash


def hexdump(data: bytes) -> str:
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:04x}  {hexpart:<47}  {asciipart}")
    return "\n".join(lines)


def format_text(flash: bytes, code_version: str, when: float) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))
    ver = version_word(flash)
    lines = [
        f"Kelly KLS flash configuration  {ts}   code-version {code_version or '?'}",
        f"  version word {ver}"
        + ("  (KBLS_0109 parameter map)" if ver >= KBLS_0109_MIN_VERSION
           else "  (pre-0109 firmware: the map below may not apply!)"),
    ]
    by_cat: dict[str, list[Param]] = {}
    for p in PARAMS_KBLS_0109:
        by_cat.setdefault(p.category, []).append(p)
    for cat in CATEGORY_ORDER:
        lines.append("")
        lines.append(cat)
        for p in by_cat.get(cat, []):
            val = read_param(flash, p)
            note = "  (read-only)" if p.ro else ""
            if p.vendor is not None and val != p.vendor:
                note += f"  << vendor sheet: {p.vendor}"
            lines.append(f"  {p.name:<22} {str(val):>8}{note}")
    return "\n".join(lines)


def to_json(flash: bytes, code_version: str, port: str, when: float) -> str:
    params = {p.name: read_param(flash, p) for p in PARAMS_KBLS_0109}
    diffs = {
        p.name: {"value": read_param(flash, p), "vendor": p.vendor}
        for p in PARAMS_KBLS_0109
        if p.vendor is not None and read_param(flash, p) != p.vendor
    }
    return json.dumps({
        "timestamp": when,
        "port": port,
        "code_version": code_version,
        "version_word": version_word(flash),
        "parameters": params,
        "vendor_diffs": diffs,
        "raw": flash.hex(),
    })


def list_serial_ports() -> None:
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.", file=sys.stderr)
        return
    print("Available serial ports:", file=sys.stderr)
    for p in ports:
        print(f"  {p.device}  {p.description}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only dump of the Solectrac Kelly KLS e-hydraulic "
        "pump controller's 512-byte flash configuration, over a USB-serial "
        "or Bluetooth-SPP adapter. Transmits only the version query, the "
        "flash-session open, and block reads — never a write or burn.",
    )
    parser.add_argument(
        "-p", "--port",
        help="serial device — a USB adapter (e.g. /dev/cu.usbserial-XXXX) or a "
        "Bluetooth SPP adapter, which appears as /dev/cu.<name>. "
        "Omit to list available ports.",
    )
    parser.add_argument("-b", "--baud", type=int, default=BAUD,
                        help=f"baud rate (default {BAUD})")
    parser.add_argument("-o", "--out",
                        help="path for the raw 512-byte dump "
                        "(default kelly-config-<timestamp>.bin)")
    parser.add_argument("--no-save", action="store_true",
                        help="do not write the raw dump file")
    parser.add_argument("--json", action="store_true",
                        help="emit one JSON object instead of the text listing")
    parser.add_argument("--raw", action="store_true",
                        help="also print a hexdump of the 512 bytes")
    parser.add_argument("--debug", action="store_true",
                        help="log every TX frame and the raw bytes seen for "
                        "each reply (for diagnosing a silent controller)")
    args = parser.parse_args()

    if not args.port:
        list_serial_ports()
        print("\nSpecify a port with --port.", file=sys.stderr)
        return 2

    try:
        dumper = KellyConfigDumper(args.port, baud=args.baud, debug=args.debug)
    except serial.SerialException as e:
        print(f"Could not open {args.port}: {e}", file=sys.stderr)
        return 1

    when = time.time()
    try:
        version, flash = dumper.dump()
    except (KellyError, serial.SerialException) as e:
        print(f"{e}", file=sys.stderr)
        return 1
    finally:
        dumper.close()

    if not args.no_save:
        out = args.out or time.strftime("kelly-config-%Y%m%d-%H%M%S.bin",
                                        time.localtime(when))
        with open(out, "wb") as f:
            f.write(flash)
        print(f"raw dump saved to {out}", file=sys.stderr)

    if args.json:
        print(to_json(flash, version, args.port, when))
    else:
        print(format_text(flash, version, when))
        if args.raw:
            print()
            print(hexdump(flash))

    module = str(read_param(flash, PARAMS_KBLS_0109[0]))
    if "LS" not in module:
        print(f"warning: module name {module!r} does not look like a KLS "
              "controller; the decoded map may not apply", file=sys.stderr)
    print("note: the flash session is left open, matching the official "
          "app's read behavior; it clears when the controller powers off.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
