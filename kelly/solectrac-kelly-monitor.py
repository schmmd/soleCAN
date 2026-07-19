#!/usr/bin/env python3
"""Read-only live monitor for the Solectrac e25G e-hydraulic Kelly KLS pump
controller, over its SM-4P serial port via a USB-serial adapter.

STRICTLY READ-ONLY BY CONSTRUCTION
----------------------------------
This tool only ever transmits the controller's live-monitor and code-version
query commands. It never opens a flash session and never sends any write,
erase, burn, commit, or motor-identify command, so it cannot alter controller
configuration. Every byte written to the port passes through a single
`_transmit()` choke-point that refuses any command code outside
``READ_ONLY_COMMANDS`` (0x11, 0x3A, 0x3B, 0x3C). Flash reads are also avoided,
so the controller is never even placed in a programming session.

Protocol (Kelly "ETS", 8N1, nominally 19200 baud)
-------------------------------------------------
    frame     = [CMD][LEN][DATA 0..16][CHECKSUM]
    checksum  = (CMD + LEN + sum(DATA)) & 0xFF
    monitor   = three zero-data queries 0x3A / 0x3B / 0x3C, each replying with
                16 data bytes; concatenated they form a 48-byte monitor block.
    tx query  = [CMD, 0x00, CMD]   (zero-data; checksum == CMD)
    rx reply  = [CMD, 0x10, <16 data bytes>, CHECKSUM]

Wiring (see DOCUMENTATION.md "E-hydraulic Kelly serial diagnostics")
--------------------------------------------------------------------
    Kelly SM-4P pin 2 (Tx) -> USB-serial RX
    Kelly SM-4P pin 3 (Rx) -> USB-serial TX
    Kelly SM-4P pin 4 (V-) -> USB-serial GND
    Kelly SM-4P pin 1 (V+, ~12 V) -> leave unconnected
Confirm the SM-4P signal level before wiring: true bipolar RS-232 needs a
MAX3232-class adapter; a logic-level port needs a plain 3.3/5 V USB-UART.

The framing, checksum, command codes, and monitor field offsets were taken
from the community kelly-connect-oss PROTOCOL.md and cross-checked against live
AC-Monitor readings on the tractor. Field scalings can vary by firmware, so
validate decoded values against the Kelly app the first time.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("This tool needs pyserial:  pip install pyserial")


# The controller transmits at its documented 19200 baud — CONFIRMED. An earlier
# ~19,900 reading was measured through a corrupted receive path (see README
# "Actual line rate"); on a clean path the documented rate decodes reliably.
BAUD = 19200

CMD_CODE_VERSION = 0x11
CMD_USER_MONITOR1 = 0x3A
CMD_USER_MONITOR2 = 0x3B
CMD_USER_MONITOR3 = 0x3C

# The ONLY command codes this tool is permitted to transmit. Every write,
# erase, burn, flash-session, and motor-identify command is deliberately
# excluded so controller configuration can never be touched. _transmit()
# enforces this allowlist on every outbound frame.
READ_ONLY_COMMANDS = frozenset(
    {CMD_CODE_VERSION, CMD_USER_MONITOR1, CMD_USER_MONITOR2, CMD_USER_MONITOR3}
)

MONITOR_COMMANDS = (CMD_USER_MONITOR1, CMD_USER_MONITOR2, CMD_USER_MONITOR3)

# Short human labels for each frame, keyed by command byte. Used only by
# --debug to annotate the raw line dump with what the frame carries.
# The three monitor replies are the successive 16-byte slices of the 48-byte
# block (0x3A -> bytes 0..15, 0x3B -> 16..31, 0x3C -> 32..47).
FRAME_DESC = {
    CMD_CODE_VERSION:  "code version",
    CMD_USER_MONITOR1: "pedals, switches, halls, volts, temps, directions",
    CMD_USER_MONITOR2: "error status, motor speed, phase current",
    CMD_USER_MONITOR3: "extended monitor (undecoded)",
}

# Grid layout of the Kelly app's "AC Monitor" screen: six rows of three
# (label, Monitor-attribute) pairs, in the same positions the app uses.
GRID_ROWS = (
    ("TPS Pedel", "tps_pedal", "Hall A", "hall_a", "Setting Dir", "set_direction"),
    ("Brake Pedel", "brake_pedal", "Hall B", "hall_b", "Actual Dir", "actual_direction"),
    ("Brake Switch", "brake_switch", "Hall C", "hall_c", "Brake Switch2", "brake_switch2"),
    ("Foot Switch", "foot_switch", "B+ Volt", "b_plus_v", "Low Speed", "low_speed"),
    ("Forward Switch", "forward_switch", "Motor Temp", "motor_temp_c", "Motor Speed", "motor_speed_rpm"),
    ("Reversed", "reverse_switch", "Controller Temp", "controller_temp_c", "Phase Current", "phase_current_a"),
)

# Fault names by bit position in the 16-bit error_status word (monitor offset 16,
# big-endian). Display-only. Verified two independent ways: the kelly-connect-oss
# reimplementation's error-name array — whose unit tests fix this bit order
# (bit 0 "Identify Err", bit 1 "Over Volt", bit 15 "Current Meter Err") — and the
# KLS-M/N manual's Table 1 buzzer codes, whose row-major order 1,1..4,4 lines up
# with these bit positions. No non-zero error_status has been observed on this
# tractor, so the labels are cross-validated against those sources, not seen live.
ERROR_NAMES = (
    "Identify Err",        # bit 0  — manual 1,1 auto-identify (phase/hall wiring)
    "Over Volt",           # bit 1  — 1,2
    "Low Volt",            # bit 2  — 1,3
    "Reserved",            # bit 3  — 1,4
    "Locking",             # bit 4  — 2,1 motor did not start
    "V+ Err",              # bit 5  — 2,2 internal volts fault
    "Overtemp",            # bit 6  — 2,3 controller over-temperature
    "High Pedel",          # bit 7  — 2,4 throttle high at power-up
    "Reserved",            # bit 8  — 3,1
    "Reset Error",         # bit 9  — 3,2 internal reset
    "Pedel Error",         # bit 10 — 3,3 hall throttle open/short
    "Hall Sensor Error",   # bit 11 — 3,4 angle/speed sensor
    "Reserved",            # bit 12 — 4,1
    "Emergency Rev Err",   # bit 13 — 4,2 (manual: reserved)
    "Motor OverTemp Err",  # bit 14 — 4,3 motor over-temperature
    "Current Meter Err",   # bit 15 — 4,4 hall galvanometer
)


def error_names(error_status: int) -> list[str]:
    """Names of the faults flagged in the 16-bit error_status bitmask."""
    return [ERROR_NAMES[b] for b in range(16) if error_status & (1 << b)]


class KellyError(Exception):
    """A read or framing failure talking to the controller."""


class NotReadOnly(KellyError):
    """A code path attempted to transmit a non-read-only command."""


def checksum(frame: bytes) -> int:
    return sum(frame) & 0xFF


def build_tx(cmd: int, data: bytes = b"") -> bytes:
    if len(data) > 16:
        raise ValueError("ETS data payload is at most 16 bytes")
    body = bytes([cmd, len(data)]) + data
    return body + bytes([checksum(body)])


@dataclass
class Monitor:
    """Decoded 48-byte monitor block (only offsets 0..21 are defined by the
    protocol; the remainder is unknown and preserved in ``raw``)."""

    tps_pedal: int          # 0..255 throttle A/D
    brake_pedal: int        # 0..255 brake A/D
    brake_switch: int
    foot_switch: int
    forward_switch: int
    reverse_switch: int     # the app labels this "Reversed"
    hall_a: int
    hall_b: int
    hall_c: int
    b_plus_v: int           # battery volts
    motor_temp_c: int
    controller_temp_c: int
    set_direction: int      # 0 = forward
    actual_direction: int   # 0 = forward
    brake_switch2: int
    low_speed: int          # low-speed mode select
    error_status: int       # 16-bit fault bitmask (bit -> ERROR_NAMES)
    motor_speed_rpm: int
    phase_current_a: int
    raw: bytes

    @classmethod
    def decode(cls, b: bytes) -> "Monitor":
        if len(b) < 22:
            raise KellyError(f"monitor block too short: {len(b)} bytes")

        def be16(i: int) -> int:
            return (b[i] << 8) | b[i + 1]

        return cls(
            tps_pedal=b[0],
            brake_pedal=b[1],
            brake_switch=b[2],
            foot_switch=b[3],
            forward_switch=b[4],
            reverse_switch=b[5],
            hall_a=b[6],
            hall_b=b[7],
            hall_c=b[8],
            b_plus_v=b[9],
            motor_temp_c=b[10],
            controller_temp_c=b[11],
            set_direction=b[12],
            actual_direction=b[13],
            brake_switch2=b[14],
            low_speed=b[15],
            error_status=be16(16),
            motor_speed_rpm=be16(18),
            phase_current_a=be16(20),
            raw=bytes(b),
        )


class KellyReader:
    def __init__(self, port: str, baud: int = BAUD, read_timeout: float = 0.6,
                 debug: bool = False):
        self.port = port
        self.baud = baud
        self.read_timeout = read_timeout
        self.debug = debug  # dump raw line traffic to stderr
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

    def __enter__(self) -> "KellyReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _transmit(self, cmd: int, data: bytes = b"") -> None:
        # The one place bytes leave for the controller. This guard is what makes
        # the tool read-only: a write/erase/burn command can never reach here.
        if cmd not in READ_ONLY_COMMANDS:
            raise NotReadOnly(f"refusing to transmit non-read-only command 0x{cmd:02X}")
        frame = build_tx(cmd, data)
        self._dbg_frame("tx", frame, b"")
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()

    def _dbg_frame(self, kind: str, frame: bytes, skipped: bytes,
                   note: str = "") -> None:
        # With --debug, echo the raw line traffic to stderr: any bytes
        # skipped while resyncing, then the framed transmission (good or bad).
        if not self.debug:
            return
        if skipped:
            print(f"  skip {skipped.hex()}", file=sys.stderr, flush=True)
        desc = FRAME_DESC.get(frame[0], "unknown command")
        if note:
            desc = f"{desc}; {note}"
        print(f"  {kind:4} {frame.hex()}  ({desc})", file=sys.stderr, flush=True)

    def _read_response(self, expected_cmd: int) -> bytes:
        # Resync on the echoed command byte, then read LEN + DATA + CHECKSUM and
        # validate. Bad/garbage frames are skipped until the deadline. Bytes
        # dropped before locking onto a frame accumulate in `skipped` so
        # --debug can show the corruption, not just the survivor.
        deadline = time.monotonic() + self.read_timeout
        skipped = bytearray()
        while time.monotonic() < deadline:
            first = self.ser.read(1)
            if not first:
                continue
            if first[0] != expected_cmd:
                skipped += first
                continue
            length_byte = self.ser.read(1)
            if not length_byte:
                skipped += first
                continue
            length = length_byte[0]
            rest = self.ser.read(length + 1)
            frame = bytes([expected_cmd, length]) + rest
            if len(rest) < length + 1:
                self._dbg_frame("BAD", frame, skipped, "short read")
                skipped.clear()
                continue
            data, rx_checksum = rest[:length], rest[length]
            want = checksum(bytes([expected_cmd, length]) + data)
            if want == rx_checksum:
                self._dbg_frame("ok", frame, skipped)
                return data
            self._dbg_frame("BAD", frame, skipped,
                            f"checksum {rx_checksum:02X}!={want:02X}")
            skipped.clear()
        raise KellyError(f"no valid response to command 0x{expected_cmd:02X}")

    def _query(self, cmd: int, data: bytes = b"") -> bytes:
        self._transmit(cmd, data)
        return self._read_response(cmd)

    def code_version(self) -> str:
        """Best-effort firmware/code-version string (read-only, no flash)."""
        data = self._query(CMD_CODE_VERSION)
        return " ".join(f"{x:02X}" for x in data)

    def read_monitor(self) -> Monitor:
        block = bytearray()
        for cmd in MONITOR_COMMANDS:
            block += self._query(cmd)
        return Monitor.decode(block)

    def read_monitor_safe(self) -> Monitor:
        """read_monitor, but transparently reopen the port if the underlying
        link dropped (Bluetooth SPP in particular). A dropped link surfaces as
        a serial error; a merely-late/garbled frame surfaces as KellyError.
        Either way the caller just retries — the reconnect happens here."""
        try:
            return self.read_monitor()
        except serial.SerialException as e:
            self.reopen()  # raises KellyError if it truly can't come back
            raise KellyError(f"link reset ({e}); reconnected") from e


def format_block(mon: Monitor, version: str, when: float) -> str:
    ts = time.strftime("%H:%M:%S", time.localtime(when))
    faults = "no faults" if mon.error_status == 0 else ", ".join(
        f"bit{b} {ERROR_NAMES[b]}"
        for b in range(16) if mon.error_status & (1 << b)
    )
    direction = "FWD" if mon.actual_direction == 0 else "REV"
    lines = [
        f"Kelly KLS monitor  {ts}   code-version {version or '?'}",
        f"  Error status    : 0x{mon.error_status:04X}  ({faults})",
        f"  Motor speed     : {mon.motor_speed_rpm:>5} RPM"
        f"        Phase current   : {mon.phase_current_a:>3} A",
        f"  B+ voltage      : {mon.b_plus_v:>5} V"
        f"          Motor temp      : {mon.motor_temp_c:>3} C"
        f"   Controller temp : {mon.controller_temp_c} C",
        f"  TPS pedal       : {mon.tps_pedal:>5}"
        f"            Brake pedal     : {mon.brake_pedal}",
        f"  Direction       : {direction}  (set={mon.set_direction}"
        f" act={mon.actual_direction})   low-speed={mon.low_speed}"
        f"  reversed={mon.reverse_switch}",
        f"  Switches        : brake={mon.brake_switch} brake2={mon.brake_switch2}"
        f" foot={mon.foot_switch} fwd={mon.forward_switch} rev={mon.reverse_switch}",
        f"  Halls           : A={mon.hall_a} B={mon.hall_b} C={mon.hall_c}",
    ]
    return "\n".join(lines)


def to_json(mon: Monitor, version: str, when: float) -> str:
    d = asdict(mon)
    d["raw"] = mon.raw.hex()
    d["errors"] = error_names(mon.error_status)
    d["code_version"] = version
    d["timestamp"] = when
    return json.dumps(d)


def run_tui(reader: "KellyReader", interval: float, once: bool) -> int:
    """Full-screen live view laid out like the Kelly app's AC Monitor screen."""
    try:
        from rich import box
        from rich.console import Console, Group
        from rich.live import Live
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        print("--tui needs rich:  pip install rich", file=sys.stderr)
        return 1

    console = Console()

    def render(mon: Monitor, version: str):
        header = Table.grid(expand=True)
        header.add_column(justify="left")
        header.add_column(justify="right")
        header.add_row("  AC Calibration          [bold]AC Monitor[/bold]",
                       f"code {version}  " if version else "  ")
        header.style = "white on blue"

        if mon.error_status == 0:
            err = Text("Error Status:   (none)", style="green")
        else:
            faults = ", ".join(f"bit{b} {ERROR_NAMES[b]}"
                               for b in range(16) if mon.error_status & (1 << b))
            err = Text(f"Error Status:   0x{mon.error_status:04X}  {faults}",
                       style="bold red")

        grid = Table(box=box.SQUARE, show_header=False, pad_edge=False)
        for _ in range(3):
            grid.add_column(justify="left")
            grid.add_column(justify="right", style="bold")
        for l1, a1, l2, a2, l3, a3 in GRID_ROWS:
            grid.add_row(l1, str(getattr(mon, a1)),
                         l2, str(getattr(mon, a2)),
                         l3, str(getattr(mon, a3)))
        return Group(header, Text(""), err, Text(""), grid)

    version = ""
    try:
        with Live(console=console, screen=not once, auto_refresh=False) as live:
            while True:
                try:
                    mon = reader.read_monitor_safe()
                except KellyError as e:
                    # warmup / dropped-and-reconnecting link — keep waiting
                    live.update(Text(f"waiting for controller… ({e})",
                                     style="yellow"), refresh=True)
                    time.sleep(interval)
                    continue
                if not version:
                    try:
                        version = reader.code_version()
                    except (KellyError, serial.SerialException):
                        version = ""
                live.update(render(mon, version), refresh=True)
                if once:
                    break
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    return 0


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
        description="Read-only live monitor for the Solectrac Kelly KLS "
        "e-hydraulic pump controller over a USB-serial or Bluetooth-SPP adapter.",
    )
    parser.add_argument(
        "-p", "--port",
        help="serial device — a USB adapter (e.g. /dev/cu.usbserial-XXXX) or a "
        "Bluetooth SPP adapter, which appears as /dev/cu.<name>. "
        "Omit to list available ports.",
    )
    parser.add_argument("-b", "--baud", type=int, default=BAUD,
                        help=f"baud rate (default {BAUD})")
    parser.add_argument("-i", "--interval", type=float, default=0.5,
                        help="seconds between polls (default 0.5)")
    parser.add_argument("--once", action="store_true",
                        help="take one reading and exit")
    parser.add_argument("--json", action="store_true",
                        help="emit one JSON object per reading")
    parser.add_argument("--tui", action="store_true",
                        help="full-screen TUI laid out like the Kelly app "
                        "AC Monitor screen (needs rich)")
    parser.add_argument("--raw", action="store_true",
                        help="also print the raw 48-byte monitor block as hex")
    parser.add_argument("--debug", action="store_true",
                        help="dump raw line traffic to stderr: each transmitted "
                        "query, each good frame's hex, each bad frame's hex with "
                        "its checksum mismatch, and any garbage bytes skipped "
                        "while resyncing (useful for diagnosing EMI corruption)")
    args = parser.parse_args()

    if args.tui and args.json:
        print("--tui and --json cannot be combined", file=sys.stderr)
        return 2

    if not args.port:
        list_serial_ports()
        print("\nSpecify a port with --port.", file=sys.stderr)
        return 2

    try:
        reader = KellyReader(args.port, baud=args.baud, debug=args.debug)
    except serial.SerialException as e:
        print(f"Could not open {args.port}: {e}", file=sys.stderr)
        return 1

    if args.tui:
        try:
            return run_tui(reader, args.interval, args.once)
        finally:
            reader.close()

    try:
        version = ""
        misses = 0
        while True:
            now = time.time()
            try:
                mon = reader.read_monitor_safe()
            except KellyError as e:
                # Bluetooth links take a moment to come up, and can open "silent"
                # (port opens but no bytes flow). Show progress, and after a
                # stretch of silence reopen the port to re-establish the link.
                misses += 1
                secs = misses * args.interval
                if misses == 3:
                    print("waiting for controller frames…", file=sys.stderr)
                elif not args.once and misses % 20 == 0:
                    print(f"still no frames after ~{secs:.0f}s — reopening the "
                          "port to re-establish the link…", file=sys.stderr)
                    try:
                        reader.reopen()
                    except KellyError:
                        pass
                if args.once and misses >= 20:
                    print(f"no valid frames after {misses} tries: {e}",
                          file=sys.stderr)
                    return 1
                time.sleep(args.interval)
                continue

            misses = 0
            if not version:  # fetch lazily, once the link is up
                try:
                    version = reader.code_version()
                except (KellyError, serial.SerialException):
                    version = ""
            if args.json:
                print(to_json(mon, version, now), flush=True)
            else:
                print(format_block(mon, version, now), flush=True)
                if args.raw:
                    print(f"  raw             : {mon.raw.hex()}", flush=True)
                print(flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
