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

Protocol (Kelly "ETS", 19200 8N1)
---------------------------------
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
    Kelly SM-4P pin 1 (V+, ~5 V) -> leave unconnected
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
    error_status: int       # 16-bit fault bitmask
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
    def __init__(self, port: str, baud: int = BAUD, read_timeout: float = 0.6):
        self.read_timeout = read_timeout
        self.ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.15,
        )

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
        self.ser.reset_input_buffer()
        self.ser.write(build_tx(cmd, data))
        self.ser.flush()

    def _read_response(self, expected_cmd: int) -> bytes:
        # Resync on the echoed command byte, then read LEN + DATA + CHECKSUM and
        # validate. Bad/garbage frames are skipped until the deadline.
        deadline = time.monotonic() + self.read_timeout
        while time.monotonic() < deadline:
            first = self.ser.read(1)
            if not first or first[0] != expected_cmd:
                continue
            length_byte = self.ser.read(1)
            if not length_byte:
                continue
            length = length_byte[0]
            rest = self.ser.read(length + 1)
            if len(rest) < length + 1:
                continue
            data, rx_checksum = rest[:length], rest[length]
            frame = bytes([expected_cmd, length]) + data
            if checksum(frame) == rx_checksum:
                return data
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


def format_block(mon: Monitor, version: str, when: float) -> str:
    ts = time.strftime("%H:%M:%S", time.localtime(when))
    faults = "no faults" if mon.error_status == 0 else (
        "bits " + ",".join(str(i) for i in range(16) if mon.error_status & (1 << i))
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
            bits = ",".join(str(i) for i in range(16) if mon.error_status & (1 << i))
            err = Text(f"Error Status:   0x{mon.error_status:04X}  bits {bits}",
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
        version = reader.code_version()
    except KellyError:
        pass

    try:
        with Live(console=console, screen=not once, auto_refresh=False) as live:
            while True:
                try:
                    live.update(render(reader.read_monitor(), version), refresh=True)
                except KellyError as e:
                    live.update(Text(f"read error: {e}", style="red"), refresh=True)
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
        "e-hydraulic pump controller over USB serial.",
    )
    parser.add_argument(
        "-p", "--port",
        help="serial device (e.g. /dev/cu.usbserial-XXXX). "
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
    args = parser.parse_args()

    if args.tui and args.json:
        print("--tui and --json cannot be combined", file=sys.stderr)
        return 2

    if not args.port:
        list_serial_ports()
        print("\nSpecify a port with --port.", file=sys.stderr)
        return 2

    try:
        reader = KellyReader(args.port, baud=args.baud)
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
        try:
            version = reader.code_version()
        except KellyError:
            pass  # version is a nicety; keep going for telemetry

        while True:
            now = time.time()
            try:
                mon = reader.read_monitor()
            except KellyError as e:
                print(f"read error: {e}", file=sys.stderr)
            else:
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
