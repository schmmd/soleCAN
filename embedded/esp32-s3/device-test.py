#!/usr/bin/env python3
"""Bench acceptance test for a flashed Solectrac CAN-monitor device.

Run this once per device before shipping. It exercises every external
interface of the firmware against real hardware:

  - HTTP: /json schema + health counters, / dashboard, captive-portal 302
  - mDNS: <name>.local resolves and serves the same device
  - socketcand (port 28600): handshake, busy-slot refusal, invalid channel
  - SLCAN over USB CDC: version query, channel open, TX-rejection (confirms
    the shipping build is a listen-only passive tap)
  - CAN receive + decode, end to end: injects synthetic J1939 frames from a
    bench CAN adapter and checks the decoded engineering values in /json,
    plus the raw-frame taps (socketcand and SLCAN)
  - VIN sense (RejsaCAN): 12 V rail reading
  - SD session logging (RejsaCAN): card mounted and logging, no latched
    failure, drop/recovery counters; plus an optional sustained-write soak
    (--sd-soak) that streams frames and checks bytes land on the card
  - BLE (optional, needs `bleak`): NUS notify stream reassembles to valid JSON
  - LEDs (optional, --interactive): operator visual checks

Stages degrade gracefully: anything whose prerequisite flag isn't given is
reported as SKIP, so the suite is useful even WiFi-only.

Bench requirements for the injection stage (--inject-channel):

  - Wire the injector adapter's CANH/CANL (and GND) to the device's CAN
    pins, with ~120 ohm termination on the bench bus.
  - The device under test is listen-only and never ACKs. A lone injector
    therefore gets no ACK, goes error-passive, and retransmits its first
    frame forever while the rest queue behind it — the decode sweep cannot
    run. Wire a second ACK-capable adapter to the bench bus and pass it as
    --ack-interface/--ack-channel: the suite opens it in normal mode purely
    to provide ACKs. The suite probes for the no-ACK condition right after
    the first frame and skips the decode sweep with one clear failure if
    the bench can't ACK.
  - NEVER run the injection stage with the device plugged into the tractor:
    the injector would spoof BMS/motor frames onto the real bus.

Test one device at a time — every device broadcasts the same AP SSID and
mDNS name.

Examples (from the repo root, where the uv project lives):

  # WiFi-only smoke test, Mac joined to the device's `tractor` AP
  uv run python embedded/esp32-s3/device-test.py

  # Full pre-ship run: USB serial + bench injector + ACK adapter + BLE +
  # LED prompts. Pass --expect-vin only when the board is powered from a
  # 12 V supply — on USB power the rail sense reads ~4.6 V.
  uv run python embedded/esp32-s3/device-test.py \
      --serial /dev/cu.usbmodem101 \
      --inject-interface slcan --inject-channel /dev/cu.usbserial-A50 \
      --ack-interface canalystii --ack-channel 0 \
      --expect-version $(git rev-parse --short HEAD) \
      --expect-sd --sd-soak 60 \
      --ble --interactive

Exit code 0 when every executed check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

# ── Device constants (mirror src/main.cpp — update together) ─────────────────

HTTP_PORT = 80
SOCKETCAND_PORT = 28600
SOCKETCAND_HANDSHAKE_MS = 10000
SLCAN_VERSION_REPLY = b"V1013"
DASHBOARD_MARKER = b"Tractor Dashboard"

NUS_SVC_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

# ── Injected J1939 fixtures ───────────────────────────────────────────────────
# Each entry: (label, can_id, data-hex). Expected decoded values are asserted
# inline in stage_inject(); they are hand-computed from the firmware scalings
# (PACK_CURRENT_BIAS_RAW 0x7D00, 0.1 V/bit, RPM_BIAS 0x0C80, temp offset +40).

FIX_PACK = (0x18F100F3, "02D47D7BC8000000")  # 72.4 V, -12.3 A, SoC 79.2 %
FIX_CELLS = (0x18F113F3, "0E420E430E440E45")  # cells 1-4: 3.650..3.653 V
FIX_CELLSUM = (0x18F102F3, "0E450E4204010003")  # max 3653 (#4) min 3650 (#1)
FIX_TEMPSUM = (0x18F104F3, "413E030103000000")  # max 25 C (#3) min 22 C (#1)
FIX_F106 = (0x18F106F3, "0501000000000000")  # state bytes 0x05 / 0x01
FIX_F107 = (0x18F107F3, "3A980F3202000064")  # 150 A dis / 38.9 A chg / +1000 W
FIX_F108_SET = (0x18F108F3, "0100000000000000")  # fault code 100 active
FIX_F108_CLR = (0x18F108F3, "0000000000000000")
FIX_MOTOR = (0x18FF21CA, "64005C1241460014")  # 1500 RPM fwd, R2, 25/30 C
FIX_DM1_SET = (0x18FECACA, "04FF080203010000")  # SPN 520 FMI 3 OC 1
FIX_DM1_CLR = (0x18FECACA, "0000000000000000")
FIX_CHARGER = (0x18FF50E5, "0320006400000000")  # 80.0 V, 10.0 A, flags 0
FIX_CHGRCMD = (0x1806E5F4, "0352018600000000")  # cmd 85.0 V / 39.0 A, en 0
FIX_VC = (0x18F100D0, "0200000000000000")  # vehicle-controller state 2
FIX_DASH = (0x18FF2112, "0500000000000000")  # dash heartbeat 5

STATIC_FIXTURES = [
    FIX_PACK, FIX_CELLS, FIX_CELLSUM, FIX_TEMPSUM, FIX_F106, FIX_F107,
    FIX_F108_SET, FIX_DM1_SET, FIX_CHARGER, FIX_CHGRCMD, FIX_VC, FIX_DASH,
]

# ── Result recording ──────────────────────────────────────────────────────────

USE_COLOR = sys.stdout.isatty()
STYLES = {
    "PASS": "\x1b[32m", "FAIL": "\x1b[31m", "WARN": "\x1b[33m",
    "SKIP": "\x1b[36m", "INFO": "\x1b[2m",
}
RESULTS: list[tuple[str, str]] = []


def report(status: str, msg: str) -> None:
    RESULTS.append((status, msg))
    tag = f"{STYLES[status]}{status:<4}\x1b[0m" if USE_COLOR else f"{status:<4}"
    print(f"  {tag}  {msg}")


def check(cond: bool, name: str, detail: str = "") -> bool:
    suffix = f" — {detail}" if detail else ""
    report("PASS" if cond else "FAIL", f"{name}{suffix}")
    return cond


def section(title: str) -> None:
    print(f"\n== {title} ==")


def approx(a, b, tol: float = 0.06) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


def jget(obj, path: str, default=None):
    """Dotted-path lookup: jget(j, 'pack.cells.max_mv')."""
    for key in path.split("."):
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return obj


# ── HTTP helpers ──────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def http_get(host: str, path: str, timeout: float = 6.0):
    """Returns (status, headers, body) without following redirects."""
    url = f"http://{host}:{HTTP_PORT}{path}"
    try:
        with OPENER.open(url, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def fetch_json(host: str, retries: int = 2):
    last_err = None
    for _ in range(retries + 1):
        try:
            status, _, body = http_get(host, "/json")
            if status == 200:
                return json.loads(body)
            last_err = f"HTTP {status}"
        except Exception as e:  # noqa: BLE001 — report any transport error
            last_err = e
        time.sleep(0.5)
    raise RuntimeError(f"GET /json failed: {last_err}")


# ── socketcand client ─────────────────────────────────────────────────────────

class ScClient:
    """Minimal socketcand rawmode client. Tokens are '< ... >' — no newlines."""

    def __init__(self, host: str, timeout: float = 5.0):
        self.sock = socket.create_connection((host, SOCKETCAND_PORT), timeout=timeout)
        self.sock.settimeout(0.2)
        self.buf = b""

    def recv_token(self, deadline_s: float):
        """Next '< ... >' token, '' on EOF, None on timeout."""
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            i = self.buf.find(b"<")
            j = self.buf.find(b">", i + 1) if i >= 0 else -1
            if i >= 0 and j >= 0:
                tok = self.buf[i:j + 1]
                self.buf = self.buf[j + 1:]
                return tok.decode(errors="replace")
            try:
                chunk = self.sock.recv(4096)
            except TimeoutError:
                continue
            except OSError:
                return ""
            if not chunk:
                return ""
            self.buf += chunk
        return None

    def send(self, text: str) -> None:
        self.sock.sendall(text.encode())

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def sc_open_rawmode(host: str, channel: str = "can0"):
    """Connect and complete the hi/open/rawmode handshake. Returns client."""
    sc = ScClient(host)
    if sc.recv_token(5) != "< hi >":
        sc.close()
        raise RuntimeError("no '< hi >' greeting")
    sc.send(f"< open {channel} >")
    if sc.recv_token(5) != "< ok >":
        sc.close()
        raise RuntimeError(f"'< open {channel} >' not acknowledged")
    sc.send("< rawmode >")
    if sc.recv_token(5) != "< ok >":
        sc.close()
        raise RuntimeError("'< rawmode >' not acknowledged")
    return sc


def sc_wait_frame(sc: ScClient, id_hex: str, data_hex: str, deadline_s: float) -> bool:
    """True if a '< frame <id> <ts> <data> >' token matching both arrives."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        tok = sc.recv_token(end - time.monotonic())
        if not tok:
            return False
        # "< frame <id> <secs.usecs> <datahex> >"
        parts = tok.strip("<> ").split()
        if len(parts) >= 4 and parts[0] == "frame" \
                and parts[1].upper() == id_hex and parts[3].upper() == data_hex:
            return True
    return False


# ── Stages ────────────────────────────────────────────────────────────────────

def stage_http(args) -> dict | None:
    section("HTTP")
    try:
        j = fetch_json(args.host)
    except RuntimeError as e:
        check(False, "GET /json", str(e))
        return None
    check(True, "GET /json returns valid JSON")

    uptime = j.get("uptime")
    if check(isinstance(uptime, (int, float)), "uptime present",
             f"{uptime:.0f} s" if isinstance(uptime, (int, float)) else "missing"):
        if uptime < 10:
            report("WARN", "uptime < 10 s — device rebooted very recently; "
                           "watch for a brownout/crash loop")

    version = j.get("version")
    if version is None or version == "unknown":
        report("WARN", f"firmware version is {version!r} — build was made "
                       "without GIT_SHA; flashed image is not identifiable")
    else:
        report("INFO", f"firmware version {version}")
    if args.expect_version:
        check(version == args.expect_version, "firmware version matches",
              f"expected {args.expect_version}, got {version}")

    state = jget(j, "can.state")
    check(state == "running", "CAN controller running", f"state={state}")
    mode = jget(j, "can.mode")
    check(mode == args.expect_mode, f"CAN bus mode is {args.expect_mode}",
          f"got {mode}")
    bus_errors = jget(j, "can.bus_errors", 0)
    report("INFO", f"can counters: frames_rx={jget(j, 'can.frames_rx')} "
                   f"tec={jget(j, 'can.tec')} rec={jget(j, 'can.rec')} "
                   f"bus_errors={bus_errors} "
                   f"recoveries={jget(j, 'can.bus_recoveries')}")
    if isinstance(bus_errors, int) and bus_errors > 100:
        report("WARN", f"bus_errors={bus_errors} — check bench wiring/termination")

    check(j.get("tractor") in ("on", "off"), "tractor on/off field present",
          f"got {j.get('tractor')!r}")

    # VIN sense (RejsaCAN only)
    vin = j.get("vin_v")
    if args.expect_vin is not None:
        if check(vin is not None, "vin_v reported",
                 f"{vin} V" if vin is not None
                 else "missing — expected on a RejsaCAN board"):
            in_tol = approx(vin, args.expect_vin, args.vin_tol)
            detail = f"read {vin} V, expected {args.expect_vin} ± {args.vin_tol} V"
            if not in_tol and 3.5 <= vin <= 5.5:
                detail += (" — reading matches USB power; pass --expect-vin "
                           "only when powered from a 12 V supply")
            check(in_tol, "VIN sense within tolerance", detail)
    elif vin is not None:
        report("INFO", f"vin_v = {vin} V (pass --expect-vin to check it "
                       "when powered from a 12 V supply)")

    status, _, body = http_get(args.host, "/")
    check(status == 200 and DASHBOARD_MARKER in body and len(body) > 10000,
          "GET / serves the embedded dashboard",
          f"status={status}, {len(body)} bytes")

    status, headers, _ = http_get(args.host, "/definitely-not-a-page")
    loc = headers.get("Location", "")
    check(status == 302 and loc.startswith("http://"),
          "unknown path 302-redirects (captive portal)",
          f"status={status}, Location={loc!r}")
    return j


def stage_sd(args, j: dict) -> None:
    section("SD logging")
    sd = j.get("sd")
    if sd is None:
        if args.expect_sd:
            check(False, "sd status present",
                  "no 'sd' object in /json — is this an SD-capable build?")
        else:
            report("SKIP", "no 'sd' object in /json (board without microSD)")
        return

    state = sd.get("state")
    if state == "error":
        check(False, "SD logging healthy",
              f"latched error: fail_op={sd.get('fail_op')!r} after "
              f"{sd.get('fail_kb')} KB — card unresponsive through all "
              "remount attempts; reseat or replace the card and reboot")
        return
    if state == "no_card":
        if args.expect_sd:
            check(False, "card mounted", "state=no_card — insert a card and "
                  "reboot (the card is probed once at boot)")
        else:
            report("WARN", "no card at boot — session logging dormant "
                           "(pass --expect-sd to make this a failure)")
        return

    check(state == "logging", "SD session logging", f"state={state}")
    check(sd.get("session", 0) >= 1, "session directory open",
          f"session={sd.get('session')}")
    free_mb = sd.get("free_mb", 0)
    check(free_mb > 0, "card has free space", f"free_mb={free_mb}")
    if free_mb < 1024:
        report("WARN", f"only {free_mb} MB free — the reaper will start "
                       "deleting old sessions below 512 MB")

    drops = sd.get("raw_dropped", 0) + sd.get("json_dropped", 0)
    check(drops == 0, "no ring-buffer drops",
          f"raw={sd.get('raw_dropped', 0)} json={sd.get('json_dropped', 0)}")
    recoveries = sd.get("recoveries", 0)
    if recoveries:
        report("WARN", f"{recoveries} remount recoveries this boot — "
                       "logging survived, but the card link is glitching")


def stage_mdns(args) -> None:
    section("mDNS")
    if args.skip_mdns:
        report("SKIP", "mDNS check disabled (--skip-mdns)")
        return
    name = f"{args.mdns_name}.local"
    result: dict = {}

    def resolve():
        try:
            result["addr"] = socket.getaddrinfo(name, HTTP_PORT,
                                                proto=socket.IPPROTO_TCP)
        except OSError as e:
            result["err"] = e

    t = threading.Thread(target=resolve, daemon=True)
    t.start()
    t.join(8)
    if "addr" not in result:
        check(False, f"{name} resolves",
              str(result.get("err", "timed out after 8 s")))
        return
    addrs = sorted({a[4][0] for a in result["addr"]})
    check(True, f"{name} resolves", ", ".join(addrs))
    try:
        fetch_json(name, retries=0)
        check(True, f"{name} serves /json")
    except RuntimeError as e:
        check(False, f"{name} serves /json", str(e))


def stage_socketcand(args) -> None:
    section("socketcand")
    try:
        sc = sc_open_rawmode(args.host)
    except (OSError, RuntimeError) as e:
        check(False, "handshake (hi/open can0/rawmode)", str(e))
        return
    check(True, "handshake (hi/open can0/rawmode)")

    if args.channels == 1:
        # With the single slot held, a second connection must be refused
        # without a greeting (the firmware keeps the existing client).
        try:
            probe = ScClient(args.host)
            tok = probe.recv_token(5)
            check(tok == "", "second connection refused while slot is busy",
                  "closed without a greeting" if tok == ""
                  else f"expected silent close, got {tok!r}")
            probe.close()
        except OSError as e:
            check(False, "second connection refused while slot is busy", str(e))
    else:
        report("SKIP", f"busy-slot refusal (board has {args.channels} slots; "
                       "test written for 1)")
    sc.close()

    # Slot must be reclaimed after disconnect; then an out-of-range channel
    # must be rejected with '< error >' and the slot must still accept can0.
    sc2 = None
    for _ in range(10):
        try:
            sc2 = ScClient(args.host)
            if sc2.recv_token(2) == "< hi >":
                break
            sc2.close()
            sc2 = None
        except OSError:
            pass
        time.sleep(0.5)
    if not check(sc2 is not None, "slot reclaimed after disconnect"):
        return
    bad_ch = f"can{args.channels}"
    sc2.send(f"< open {bad_ch} >")
    check(sc2.recv_token(5) == "< error >",
          f"'< open {bad_ch} >' rejected (channel doesn't exist)")
    sc2.send("< open can0 >")
    check(sc2.recv_token(5) == "< ok >", "slot still usable after rejected open")
    sc2.close()

    if args.slow:
        # A client that never completes the handshake must be dropped after
        # SOCKETCAND_HANDSHAKE_MS so it can't pin the slot forever.
        time.sleep(1)  # let the previous slot free up
        try:
            idler = ScClient(args.host)
            if idler.recv_token(5) != "< hi >":
                check(False, "handshake-timeout reclaim", "no greeting")
                return
            t0 = time.monotonic()
            tok = idler.recv_token(SOCKETCAND_HANDSHAKE_MS / 1000 + 3)
            took = time.monotonic() - t0
            check(tok == "" and took < SOCKETCAND_HANDSHAKE_MS / 1000 + 2.5,
                  "idle client dropped after handshake timeout",
                  f"closed after {took:.1f} s")
            idler.close()
        except OSError as e:
            check(False, "handshake-timeout reclaim", str(e))


def open_serial(port: str):
    import serial  # pyserial — project dependency
    return serial.Serial(port, 115200, timeout=0.2)


def stage_slcan(args) -> None:
    section("SLCAN (USB serial)")
    if not args.serial:
        report("SKIP", "no --serial port given")
        return
    try:
        ser = open_serial(args.serial)
    except Exception as e:  # noqa: BLE001 — pyserial raises various types
        check(False, f"open {args.serial}", str(e))
        return
    with ser:
        ser.reset_input_buffer()
        # Version query before opening the channel, so no frame lines can
        # interleave with the reply.
        ser.write(b"V\r")
        reply = ser.read_until(b"\r")
        ver_ok = reply.rstrip(b"\r") == SLCAN_VERSION_REPLY
        check(ver_ok, "'V' version query",
              "" if ver_ok
              else f"expected {SLCAN_VERSION_REPLY!r}, got {reply!r}")

        ser.write(b"O\r")
        check(ser.read_until(b"\r") == b"\r", "'O' opens the channel")

        if args.expect_mode == "listen_only":
            # The firmware must answer any injection attempt with BELL.
            # This is the pre-ship confirmation that the passive-tap
            # guarantee is actually in the flashed image.
            ser.reset_input_buffer()
            ser.write(b"T1FFFFFFF0\r")
            deadline = time.monotonic() + 2
            got_bell = False
            while time.monotonic() < deadline and not got_bell:
                got_bell = b"\a" in ser.read(256)
            check(got_bell, "TX attempt rejected with BELL (listen-only build)")
        else:
            report("SKIP", "TX-rejection check (build expected to allow TX)")

        ser.write(b"C\r")
        time.sleep(0.2)
    check(True, "'C' closes the channel and port released")


class MotorInjector(threading.Thread):
    """Repeats the motor fixture at 20 Hz until stopped, keeping the firmware's
    500 ms motor.alive window continuously satisfied."""

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.stop_evt = threading.Event()
        self.error = None

    def run(self):
        import can
        can_id, data_hex = FIX_MOTOR
        msg = can.Message(arbitration_id=can_id, is_extended_id=True,
                          data=bytes.fromhex(data_hex))
        while not self.stop_evt.is_set():
            try:
                self.bus.send(msg, timeout=1.0)
            except Exception as e:  # noqa: BLE001
                self.error = e
                return
            self.stop_evt.wait(0.05)


def stage_inject(args) -> None:
    section("CAN receive + decode (bench injection)")
    if not args.inject_channel:
        report("SKIP", "no --inject-channel given")
        return
    try:
        import can
    except ImportError as e:
        check(False, "import python-can", str(e))
        return

    # Optional ACK node: a second adapter opened in normal mode ACKs every
    # frame in hardware, so the listen-only device under test doesn't leave
    # the injector un-ACKed. Opened first so it's live before the first send.
    ack_bus = None
    if args.ack_channel:
        ack_channel = args.ack_channel
        if ack_channel.isdigit():
            ack_channel = int(ack_channel)
        try:
            ack_bus = can.Bus(interface=args.ack_interface,
                              channel=ack_channel,
                              bitrate=args.inject_bitrate)

            def drain():  # keep host-side buffers empty; ACKs are hardware
                try:
                    while True:
                        ack_bus.recv(1.0)
                except Exception:  # noqa: BLE001 — ends at bus shutdown
                    pass

            threading.Thread(target=drain, daemon=True).start()
            report("INFO", f"ACK node up on {args.ack_interface}:{ack_channel}")
        except Exception as e:  # noqa: BLE001
            report("WARN", f"could not open ACK adapter "
                           f"({args.ack_interface}:{args.ack_channel}): {e}")

    channel = args.inject_channel
    if channel.isdigit():
        channel = int(channel)
    try:
        bus = can.Bus(interface=args.inject_interface, channel=channel,
                      bitrate=args.inject_bitrate)
    except Exception as e:  # noqa: BLE001
        check(False, f"open injector ({args.inject_interface}:{channel})", str(e))
        if ack_bus:
            ack_bus.shutdown()
        return

    def send(fix):
        can_id, data_hex = fix
        bus.send(can.Message(arbitration_id=can_id, is_extended_id=True,
                             data=bytes.fromhex(data_hex)), timeout=1.0)

    # Open the raw-frame taps before injecting so they see the pack frame.
    sc_tap = None
    try:
        sc_tap = sc_open_rawmode(args.host)
    except (OSError, RuntimeError) as e:
        report("WARN", f"socketcand tap unavailable: {e}")
    ser_tap = None
    if args.serial:
        try:
            ser_tap = open_serial(args.serial)
            ser_tap.reset_input_buffer()
            ser_tap.write(b"O\r")
            # Wait for the CR ack so the channel is confirmed open before
            # anything is injected — otherwise the first frame can race the
            # 'O' and never be emitted on this tap.
            deadline = time.monotonic() + 1.0
            ack = b""
            while time.monotonic() < deadline and b"\r" not in ack:
                ack += ser_tap.read(16)
            if b"\r" not in ack:
                report("WARN", "SLCAN tap: no response to 'O' within 1 s")
        except Exception as e:  # noqa: BLE001
            report("WARN", f"SLCAN tap unavailable: {e}")
            ser_tap = None

    try:
        baseline = fetch_json(args.host)
        base_rx = jget(baseline, "can.frames_rx", 0)
        base_decoded = jget(baseline, "can.frames_decoded", 0)

        # First frame triples as the reception smoke test, the tap check,
        # and the no-ACK probe.
        send(FIX_PACK)
        pack_id, pack_data = FIX_PACK
        eff_id = f"{pack_id | 0x80000000:X}"
        slcan_line = f"T{pack_id:08X}8{pack_data}".encode()

        def slcan_scan(window_s: float) -> bool:
            deadline = time.monotonic() + window_s
            seen = b""
            while time.monotonic() < deadline and slcan_line not in seen:
                seen += ser_tap.read(4096)
            return slcan_line in seen

        sc_seen = sc_wait_frame(sc_tap, eff_id, pack_data, 3.0) if sc_tap else False
        ser_seen = slcan_scan(3.0) if ser_tap else False

        # No-ACK probe, on a raw tap rather than /json. Nothing else is
        # sending, so the pack frame arriving *again* means nothing ACKed
        # the injector and it is retransmitting — back-to-back, ~1600
        # frames/s, which floods the device hard enough that the HTTP
        # server can starve. Don't trust /json until the bench is known
        # to ACK. Must run before any tap retries below, which re-send.
        if sc_tap:
            storm = sc_wait_frame(sc_tap, eff_id, pack_data, 1.5)
        elif ser_tap:
            ser_tap.reset_input_buffer()
            time.sleep(1.5)
            storm = slcan_line in ser_tap.read(65536)
        else:
            try:
                rx1 = jget(fetch_json(args.host), "can.frames_rx", 0)
                time.sleep(1.2)
                storm = jget(fetch_json(args.host),
                             "can.frames_rx", 0) > rx1 + 2
            except RuntimeError:
                storm = True   # HTTP starving is itself a storm symptom
        if not check(not storm, "bench bus provides ACKs",
                     "no retransmissions observed" if not storm else
                     "the pack frame keeps repeating with nothing being "
                     "sent — nothing ACKs the injector, so it retransmits "
                     "forever and later fixtures never leave the adapter; "
                     "wire a second adapter in normal mode to the bench bus "
                     "and pass it as --ack-channel"):
            report("SKIP", "decode sweep, fault latch/clear, and motor "
                           "liveness (unreliable without ACKs)")
            return

        # Tap results, retrying a missed tap now that ACKs are confirmed
        # (re-sending the same frame is idempotent for the decoder). A miss
        # on the first attempt is usually the harness racing the injection
        # — e.g. the frame arriving before the device processed the SLCAN
        # 'O' — so a persistent miss across retries is what indicts the tap.
        sc_tries = 1
        while sc_tap and not sc_seen and sc_tries < 3:
            sc_tries += 1
            send(FIX_PACK)
            sc_seen = sc_wait_frame(sc_tap, eff_id, pack_data, 2.0)
        if sc_tap:
            check(sc_seen, "pack frame seen on socketcand tap",
                  f"needed {sc_tries} attempts" if sc_tries > 1 else "")
        ser_tries = 1
        while ser_tap and not ser_seen and ser_tries < 3:
            ser_tries += 1
            send(FIX_PACK)
            ser_seen = slcan_scan(2.0)
        if ser_tap:
            check(ser_seen, "pack frame seen on SLCAN tap",
                  f"needed {ser_tries} attempts" if ser_tries > 1 else "")

        j = fetch_json(args.host)
        if jget(j, "can.frames_rx", 0) <= base_rx:
            check(False, "device receives bench frames",
                  "frames_rx did not increase — check CANH/CANL wiring, "
                  "termination, and shared ground")
            return
        check(True, "device receives bench frames")

        # Pack (already injected): 72.4 V, -12.3 A, -890.5 W, SoC 79.2 %
        check(approx(jget(j, "pack.voltage_v"), 72.4), "pack voltage 72.4 V",
              f"got {jget(j, 'pack.voltage_v')}")
        check(approx(jget(j, "pack.current_a"), -12.3), "pack current -12.3 A",
              f"got {jget(j, 'pack.current_a')}")
        check(approx(jget(j, "pack.power_w"), -890.5, tol=0.2),
              "pack power -890.5 W", f"got {jget(j, 'pack.power_w')}")
        check(approx(jget(j, "pack.soc_pct"), 79.2), "SoC 79.2 %",
              f"got {jget(j, 'pack.soc_pct')}")

        for fix in STATIC_FIXTURES[1:]:
            send(fix)
        time.sleep(0.5)
        j = fetch_json(args.host)

        # Cells 1-4 + summary
        volts = jget(j, "pack.cells.voltages", [])
        got4 = volts[:4] if isinstance(volts, list) else []
        want4 = [3.65, 3.651, 3.652, 3.653]
        check(len(got4) == 4 and all(approx(a, b, 0.0006)
                                     for a, b in zip(got4, want4)),
              "cell voltages 1-4 decode", f"got {got4}")
        check(jget(j, "pack.cells.max_mv") == 3653
              and jget(j, "pack.cells.min_mv") == 3650
              and jget(j, "pack.cells.spread_mv") == 3
              and jget(j, "pack.cells.max_n") == 4
              and jget(j, "pack.cells.min_n") == 1,
              "cell min/max summary decode",
              f"got {jget(j, 'pack.cells')}")
        check(approx(jget(j, "pack.v_estimate"), 73.03, 0.005),
              "pack voltage estimate 73.03 V",
              f"got {jget(j, 'pack.v_estimate')}")

        # Temp summary
        ts = jget(j, "pack.cells.temp_summary", {})
        check(ts.get("max_c") == 25 and ts.get("min_c") == 22
              and ts.get("max_n") == 3 and ts.get("min_n") == 1
              and ts.get("spread_c") == 3,
              "temperature summary decode", f"got {ts}")

        # BMS state + limits
        check(jget(j, "bms.state.byte0") == 5 and jget(j, "bms.state.byte1") == 1,
              "BMS state bytes decode", f"got {jget(j, 'bms.state')}")
        check(approx(jget(j, "bms.limit.discharge_a"), 150.0)
              and approx(jget(j, "bms.limit.charge_a"), 38.9)
              and approx(jget(j, "bms.limit.charge_power_extra_w"), 1000, 1)
              and jget(j, "bms.limit.mode") == 2,
              "BMS current limits decode", f"got {jget(j, 'bms.limit')}")

        # Faults: BMS code 100 + MC SPN 520 latched
        faults_latched = check(jget(j, "faults.bms") == [100],
                               "BMS fault code 100 active",
                               f"got {jget(j, 'faults.bms')}")
        faults_latched &= check(jget(j, "faults.mc") == [520],
                                "MC DTC SPN 520 active",
                                f"got {jget(j, 'faults.mc')}")
        check(jget(j, "dm1.dtc_spn") == 520 and jget(j, "dm1.dtc_fmi") == 3
              and jget(j, "dm1.dtc_oc") == 1,
              "DM1 SPN/FMI/OC decode", f"got {jget(j, 'dm1')}")

        # Charger + BMS→charger command
        check(approx(jget(j, "charger.voltage_v"), 80.0)
              and approx(jget(j, "charger.current_a"), 10.0)
              and jget(j, "charger.flags") == 0,
              "charger telemetry decode", f"got {jget(j, 'charger')}")
        check(approx(jget(j, "chgr_cmd.voltage_v"), 85.0)
              and approx(jget(j, "chgr_cmd.current_a"), 39.0)
              and jget(j, "chgr_cmd.enable") == 0,
              "charger command decode", f"got {jget(j, 'chgr_cmd')}")

        # Vehicle controller + dash heartbeat
        check(jget(j, "vc.state") == 2, "vehicle-controller state decode",
              f"got {jget(j, 'vc.state')}")
        check(jget(j, "dash.alive") == 5, "dash heartbeat decode",
              f"got {jget(j, 'dash.alive')}")

        # Fault clear: both bitmaps back to empty, DM1 unlatched. Send the
        # clears regardless (leave the device clean) but only assert when the
        # latch was observed — otherwise "already empty" would pass vacuously.
        send(FIX_F108_CLR)
        send(FIX_DM1_CLR)
        time.sleep(0.5)
        if faults_latched:
            j = fetch_json(args.host)
            check(jget(j, "faults.bms") == [] and jget(j, "faults.mc") == [],
                  "fault bitmaps clear on all-zero frames",
                  f"got bms={jget(j, 'faults.bms')} mc={jget(j, 'faults.mc')}")
            check("dm1" not in j, "DM1 unlatches on healthy frame")
        else:
            report("SKIP", "fault-clear checks (fault latch was not observed)")

        # Motor liveness: stream FF21CA at 20 Hz, then let it go stale.
        injector = MotorInjector(bus)
        injector.start()
        time.sleep(1.0)
        alive_ok = False
        for _ in range(3):
            j = fetch_json(args.host)
            if jget(j, "motor.alive") and j.get("tractor") == "on":
                alive_ok = True
                break
        check(alive_ok, "motor.alive + tractor 'on' while FF21CA streams")
        check(jget(j, "motor.rpm_magnitude") == 1500
              and jget(j, "motor.direction") == 1
              and jget(j, "motor.range") == 2
              and jget(j, "motor.torque_raw") == 100
              and jget(j, "motor.controller_temp_c") == 25
              and jget(j, "motor.motor_temp_c") == 30,
              "motor telemetry decode (1500 RPM fwd, R2, 25/30 C)",
              f"got {jget(j, 'motor')}")

        if args.interactive:
            ans = input("      >> Is the BLUE LED blinking right now? [y/n] ")
            check(ans.strip().lower().startswith("y"),
                  "blue LED blinks on CAN activity (operator)")

        injector.stop_evt.set()
        injector.join(2)
        if injector.error:
            report("WARN", f"motor injector stopped early: {injector.error}")

        # The firmware's motor.alive window is 500 ms; 1.5 s of bus silence
        # is decisive. (ACKs were confirmed above, so nothing retransmits.)
        time.sleep(1.5)
        j = fetch_json(args.host)
        check(not jget(j, "motor.alive") and j.get("tractor") == "off",
              "motor.alive goes stale after frames stop",
              f"alive={jget(j, 'motor.alive')} tractor={j.get('tractor')}")

        j = fetch_json(args.host)
        check(jget(j, "can.frames_decoded", 0) > base_decoded,
              "frames_decoded counter advanced",
              f"{base_decoded} -> {jget(j, 'can.frames_decoded')}")

        # SD write soak: stream frames flat-out and confirm bytes actually
        # land on the card. This is the regression test for the sustained-
        # write card lockups: the pass condition is that the session is
        # still "logging" afterwards, with the byte counter tracking what
        # was sent. Recoveries during the soak are tolerated (the firmware
        # absorbs them losslessly) but reported, so a worsening card link
        # is visible at ship time.
        if args.sd_soak:
            sd0 = jget(j, "sd")
            if not isinstance(sd0, dict) or sd0.get("state") != "logging":
                report("SKIP", "SD write soak (sd state: "
                               f"{sd0.get('state') if isinstance(sd0, dict) else 'absent'})")
            else:
                can_id, data_hex = FIX_MOTOR
                msg = can.Message(arbitration_id=can_id, is_extended_id=True,
                                  data=bytes.fromhex(data_hex))
                sent = 0
                end = time.monotonic() + args.sd_soak
                while time.monotonic() < end:
                    bus.send(msg, timeout=1.0)
                    sent += 1
                time.sleep(2.5)  # let the writer drain and hit a flush cycle
                sd1 = jget(fetch_json(args.host), "sd", {})
                report("INFO", f"soak sent {sent} frames in {args.sd_soak} s")
                if not check(sd1.get("state") == "logging",
                             "SD still logging after write soak",
                             f"state={sd1.get('state')} "
                             f"fail_op={sd1.get('fail_op')!r}"):
                    return
                # ~55 bytes per .asc line; require half the expectation so
                # MB-granularity rounding can't fail a healthy run.
                expect_mb = sent * 55 / (1 << 20)
                got_mb = sd1.get("mb_written", 0) - sd0.get("mb_written", 0)
                check(got_mb >= expect_mb / 2,
                      "soak bytes landed on the card",
                      f"mb_written +{got_mb} (sent ≈ {expect_mb:.1f} MB)")
                rec = sd1.get("recoveries", 0) - sd0.get("recoveries", 0)
                if rec:
                    report("WARN", f"{rec} remount recoveries during soak — "
                                   "absorbed, but the card link is glitching")
                soak_drops = (sd1.get("raw_dropped", 0) - sd0.get("raw_dropped", 0)
                              + sd1.get("json_dropped", 0) - sd0.get("json_dropped", 0))
                check(soak_drops == 0, "no ring drops during soak",
                      f"dropped {soak_drops} lines")
    except Exception as e:  # noqa: BLE001 — HTTP, socket, and can.CanError
        check(False, "injection stage aborted", str(e))
    finally:
        if sc_tap:
            sc_tap.close()
        if ser_tap:
            try:
                ser_tap.write(b"C\r")
                ser_tap.close()
            except Exception:  # noqa: BLE001
                pass
        bus.shutdown()
        if ack_bus:
            ack_bus.shutdown()


def stage_ble(args) -> None:
    section("BLE (Nordic UART Service)")
    if not args.ble:
        report("SKIP", "BLE check disabled (pass --ble to enable; needs bleak)")
        return
    try:
        import asyncio
        from bleak import BleakClient, BleakScanner
    except ImportError:
        check(False, "import bleak", "install with: uv pip install bleak")
        return

    async def run() -> dict:
        dev = await BleakScanner.find_device_by_name(args.mdns_name, timeout=15)
        if dev is None:
            for d in await BleakScanner.discover(
                    timeout=10, service_uuids=[NUS_SVC_UUID.lower()]):
                dev = d
                break
        if dev is None:
            raise RuntimeError(f"no BLE device named {args.mdns_name!r} "
                               "advertising the NUS service")
        report("INFO", f"found {dev.name or '?'} [{dev.address}]")

        buf = bytearray()
        payloads: list[bytes] = []
        got = asyncio.Event()

        def on_notify(_char, data: bytearray):
            # Wire format: [u16 BE length][JSON], chunked. Resync by scanning
            # for a plausible length followed by '{' in case we subscribed
            # mid-frame.
            buf.extend(data)
            while True:
                while len(buf) >= 3 and not (
                        ((buf[0] << 8) | buf[1]) <= 8192 and buf[2:3] == b"{"):
                    del buf[0]
                if len(buf) < 3:
                    return
                want = (buf[0] << 8) | buf[1]
                if len(buf) - 2 < want:
                    return
                payload = bytes(buf[2:2 + want])
                del buf[:2 + want]
                if payload.endswith(b"}"):
                    payloads.append(payload)
                    got.set()

        async with BleakClient(dev) as client:
            await client.start_notify(NUS_TX_UUID, on_notify)
            await asyncio.wait_for(got.wait(), timeout=20)
        return json.loads(payloads[0])

    try:
        j = asyncio.run(run())
    except Exception as e:  # noqa: BLE001 — scanner/GATT errors vary by OS
        check(False, "BLE JSON snapshot received", str(e))
        return
    check(True, "BLE JSON snapshot received and reassembled")
    check("uptime" in j and "can" in j and "tractor" in j,
          "BLE payload has expected fields", f"keys: {sorted(j.keys())}")


def stage_interactive(args) -> None:
    if not args.interactive:
        return
    section("Operator checks")
    prompts = [
        ("Is the GREEN power LED lit?", "green power LED lit"),
        ("Is the YELLOW LED completely off? (it blinks only when CAN init "
         "or WiFi failed)", "yellow LED off — network + CAN healthy"),
    ]
    for question, name in prompts:
        ans = input(f"      >> {question} [y/n] ")
        check(ans.strip().lower().startswith("y"), f"{name} (operator)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bench acceptance test for a flashed Solectrac "
                    "CAN-monitor device.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--host", default="192.168.4.1",
                    help="device IP or hostname (AP address by default)")
    ap.add_argument("--mdns-name", default="tractor",
                    help="mDNS hostname / BLE device name the build advertises")
    ap.add_argument("--skip-mdns", action="store_true",
                    help="skip the mDNS resolution check")
    ap.add_argument("--serial", metavar="PORT",
                    help="device USB CDC port (enables SLCAN checks), "
                         "e.g. /dev/cu.usbmodem101")
    ap.add_argument("--inject-interface", default="slcan",
                    help="python-can interface of the bench injector")
    ap.add_argument("--inject-channel", metavar="CHANNEL",
                    help="bench injector channel (enables the CAN decode "
                         "stage), e.g. /dev/cu.usbserial-A50 or 0")
    ap.add_argument("--inject-bitrate", type=int, default=250000)
    ap.add_argument("--ack-interface", default="slcan",
                    help="python-can interface of the ACK adapter")
    ap.add_argument("--ack-channel", metavar="CHANNEL",
                    help="second bench adapter opened in normal mode purely "
                         "to ACK frames (the device under test is listen-only "
                         "and never ACKs)")
    ap.add_argument("--expect-mode", choices=["listen_only", "normal"],
                    default="listen_only",
                    help="CAN bus mode the flashed build should report")
    ap.add_argument("--expect-version", metavar="SHA",
                    help="git short SHA the flashed build should report")
    ap.add_argument("--expect-vin", type=float, metavar="VOLTS",
                    help="expected 12 V rail reading (RejsaCAN VIN sense)")
    ap.add_argument("--expect-sd", action="store_true",
                    help="require a mounted, logging microSD (RejsaCAN); "
                         "without this, a missing card is only a warning")
    ap.add_argument("--sd-soak", type=int, metavar="SECONDS", default=0,
                    help="stream frames flat-out for this long during the "
                         "injection stage and verify the bytes land on the "
                         "card (30+ recommended; needs --inject-channel)")
    ap.add_argument("--vin-tol", type=float, default=0.8,
                    help="VIN sense tolerance in volts")
    ap.add_argument("--channels", type=int, default=1,
                    help="socketcand channel count of this board "
                         "(RejsaCAN/Feather 1, T-2CAN 2)")
    ap.add_argument("--ble", action="store_true",
                    help="run the BLE stage (requires bleak)")
    ap.add_argument("--interactive", action="store_true",
                    help="prompt the operator for LED checks")
    ap.add_argument("--slow", action="store_true",
                    help="include the ~12 s socketcand handshake-timeout check")
    args = ap.parse_args()

    print(f"Solectrac device acceptance test — target {args.host}")
    print("Reminder: test one device at a time; every unit broadcasts the "
          "same AP SSID and mDNS name.")

    j = stage_http(args)
    if j is None:
        print("\nDevice unreachable over HTTP — aborting remaining stages.")
        print("Join the device's WiFi AP (default SSID 'tractor') or pass "
              "--host with its bench-network address.")
        return 1

    stage_sd(args, j)
    stage_mdns(args)
    stage_socketcand(args)
    stage_slcan(args)
    stage_inject(args)
    stage_ble(args)
    stage_interactive(args)

    counts = {s: sum(1 for st, _ in RESULTS if st == s)
              for s in ("PASS", "FAIL", "WARN", "SKIP")}
    print(f"\n{'=' * 60}")
    print(f"{counts['PASS']} passed, {counts['FAIL']} failed, "
          f"{counts['WARN']} warnings, {counts['SKIP']} skipped")
    if counts["FAIL"]:
        print("Result: NOT ship-ready — fix the failures above and rerun.")
        for st, msg in RESULTS:
            if st == "FAIL":
                print(f"  FAIL  {msg}")
        return 1
    skipped_major = counts["SKIP"] > 0
    verdict = "all executed checks passed"
    if skipped_major:
        verdict += " (some stages skipped — see above)"
    print(f"Result: ship-ready — {verdict}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
