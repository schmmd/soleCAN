#!/usr/bin/env python3
"""Time-synced dual-CAN capture via Canalyst-II.

CAN0 (channel 0): BMS diagnostic port — actively polled for the ~30-DID
                  baseline set (see bms/solectrac-bms-diagnostics.py).
CAN1 (channel 1): OBD2 / vehicle bus — passively logged.

Both buses write to one ASC file with msg.channel tagging so frames from
both share a single host timeline.  Time alignment is automatic: the
canalystii driver stamps frames with the host monotonic clock, and both
channels run through one adapter so there's no inter-device offset.

Usage:
    util/capture_dual_can.py session.asc
    util/capture_dual_can.py session.asc --rate 1.0 --duration 600
    util/capture_dual_can.py session.asc --diag-channel 0 --obd-channel 1
"""

import argparse
import importlib.util
import os
import signal
import sys
import threading
import time

import can

# Pull IsoTp / read_did / ALL_POLLS / REQ_ID / RESP_ID from the diagnostics
# script.  Its filename has a hyphen, so direct `import` won't work; use
# importlib to load it as a module instead.
HERE = os.path.dirname(os.path.abspath(__file__))
DIAG_PATH = os.path.normpath(os.path.join(HERE, "..", "bms",
                                          "solectrac-bms-diagnostics.py"))
_spec = importlib.util.spec_from_file_location("bms_diag", DIAG_PATH)
_diag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_diag)

IsoTp = _diag.IsoTp
read_did = _diag.read_did
UdsError = _diag.UdsError
IsoTpError = _diag.IsoTpError
REQ_ID = _diag.REQ_ID
RESP_ID = _diag.RESP_ID
ALL_POLLS = _diag.ALL_POLLS

BITRATE = 250_000


class TaggedForwarder(can.Listener):
    """Listener that stamps msg.channel, counts frames, and forwards downstream.

    canalystii's multi-channel Bus already sets msg.channel on RX, but TX
    frames echoed back through IsoTp don't carry one — tag defensively so
    the ASC always records which bus a frame came from.  The frame counter
    and last-seen timestamp drive the live status line.
    """

    def __init__(self, channel: int, target: can.Listener):
        self.channel = channel
        self.target = target
        self.frames = 0
        self.last_ts = 0.0
        self.unique_ids: set[int] = set()

    def on_message_received(self, msg: can.Message) -> None:
        if msg.channel is None or msg.channel == "":
            msg.channel = self.channel
        self.frames += 1
        self.last_ts = time.monotonic()
        self.unique_ids.add(msg.arbitration_id)
        self.target.on_message_received(msg)


class ChannelDispatcher(can.Listener):
    """Route incoming frames from a multi-channel bus to per-channel listeners."""

    def __init__(self, routes: dict[int, list[can.Listener]]):
        self.routes = routes

    def on_message_received(self, msg: can.Message) -> None:
        listeners = self.routes.get(msg.channel)
        if not listeners:
            return
        for listener in listeners:
            listener.on_message_received(msg)


class ChannelBus:
    """Thin bus shim that pins msg.channel on TX before forwarding to ``bus.send``.

    canalystii's multi-channel Bus uses msg.channel to pick which CAN
    controller to transmit on; IsoTp doesn't know about channels, so we
    stamp the channel here and otherwise pass through.
    """

    def __init__(self, bus: can.BusABC, channel: int):
        self._bus = bus
        self._channel = channel

    def send(self, msg: can.Message, timeout: float | None = None) -> None:
        msg.channel = self._channel
        self._bus.send(msg, timeout=timeout)


def status_thread(diag_fwd: TaggedForwarder, obd_fwd: TaggedForwarder,
                  state: dict, stop: threading.Event,
                  started: float, interval: float = 1.0) -> None:
    """Print a single-line live status until stop is set."""
    def fmt_age(last_ts: float) -> str:
        if last_ts == 0.0:
            return "no rx"
        age = time.monotonic() - last_ts
        if age < 1.0:
            return "live"
        return f"{age:.0f}s ago"

    while not stop.is_set():
        elapsed = time.monotonic() - started
        line = (
            f"\r[{elapsed:6.1f}s] "
            f"diag {diag_fwd.frames:>6}f / {len(diag_fwd.unique_ids):>3} ids "
            f"({fmt_age(diag_fwd.last_ts):>7}) | "
            f"obd  {obd_fwd.frames:>6}f / {len(obd_fwd.unique_ids):>3} ids "
            f"({fmt_age(obd_fwd.last_ts):>7}) | "
            f"polls ok={state['polls_ok']} err={state['polls_err']}"
        )
        # Pad to clear any leftover characters from a previous longer line.
        sys.stderr.write(line.ljust(120))
        sys.stderr.flush()
        stop.wait(interval)
    sys.stderr.write("\n")
    sys.stderr.flush()


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("output", help="ASC capture file path (e.g. data/dual/session.asc)")
    p.add_argument("--rate", type=float, default=1.0,
                   help="DID poll rate in Hz on the diagnostic bus (default: 1.0)")
    p.add_argument("--duration", type=float, default=0.0,
                   help="stop after N seconds (default: 0 = run until Ctrl-C)")
    p.add_argument("--diag-channel", type=int, default=0,
                   help="canalystii channel for BMS diagnostic bus (default: 0)")
    p.add_argument("--obd-channel", type=int, default=1,
                   help="canalystii channel for OBD2 bus (default: 1)")
    args = p.parse_args()

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir and not os.path.isdir(out_dir):
        raise SystemExit(f"output directory does not exist: {out_dir}")

    # canalystii's USB device can only be claimed once per process, so both
    # CAN channels live on a single Bus instance with channel=[a, b].
    # Outgoing msg.channel selects which controller transmits; incoming
    # msg.channel reports which controller received.
    bus = can.Bus(
        interface="canalystii",
        channel=[args.diag_channel, args.obd_channel],
        bitrate=BITRATE,
    )

    writer = can.ASCWriter(args.output)
    diag_reader = can.BufferedReader()

    diag_fwd = TaggedForwarder(args.diag_channel, writer)
    obd_fwd = TaggedForwarder(args.obd_channel, writer)

    dispatcher = ChannelDispatcher({
        args.diag_channel: [diag_fwd, diag_reader],
        args.obd_channel: [obd_fwd],
    })
    notifier = can.Notifier(bus, [dispatcher])

    # IsoTp gets a channel-pinning shim around the shared bus so its TX
    # frames go out on the diagnostic channel.  The BufferedReader feeds
    # responses (filtered by channel via the dispatcher); the
    # TaggedForwarder also logs our outgoing UDS requests to the ASC.
    tp = IsoTp(ChannelBus(bus, args.diag_channel), REQ_ID, RESP_ID,
               reader=diag_reader, writer=diag_fwd)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    period = 1.0 / args.rate
    started = time.monotonic()
    state = {"polls_ok": 0, "polls_err": 0}
    next_t = started

    print(f"Capturing → {args.output}", file=sys.stderr)
    print(f"  diag  ch{args.diag_channel}: polling {len(ALL_POLLS)} DIDs "
          f"@ {args.rate} Hz", file=sys.stderr)
    print(f"  obd2  ch{args.obd_channel}: passive log", file=sys.stderr)
    print("Ctrl-C to stop.\n", file=sys.stderr)

    status = threading.Thread(
        target=status_thread,
        args=(diag_fwd, obd_fwd, state, stop, started),
        daemon=True,
    )
    status.start()

    try:
        while not stop.is_set():
            sweep_err = False
            for did, _decoder in ALL_POLLS:
                if stop.is_set():
                    break
                try:
                    read_did(tp, did)
                    state["polls_ok"] += 1
                except UdsError:
                    # DID-level NRC is still recorded in the ASC — count as
                    # "we got a response" and keep walking the list.
                    state["polls_ok"] += 1
                except IsoTpError as e:
                    state["polls_err"] += 1
                    sweep_err = True
                    # Newline so we don't overwrite the status line.
                    sys.stderr.write(f"\n  iso-tp error on 0x{did:04X}: {e}\n")
                    sys.stderr.flush()
                    break

            if args.duration and (time.monotonic() - started) >= args.duration:
                break

            next_t += period
            slack = next_t - time.monotonic()
            if slack > 0:
                stop.wait(slack)
            else:
                # Sweep overran the period; reset cadence rather than free-run.
                next_t = time.monotonic()
            if sweep_err:
                # Give a wedged bus a moment before the next sweep.
                stop.wait(0.5)
    finally:
        stop.set()
        status.join(timeout=2.0)
        notifier.stop()
        writer.stop()
        bus.shutdown()
        elapsed = time.monotonic() - started
        print(
            f"Stopped after {elapsed:.1f}s.\n"
            f"  diag  ch{args.diag_channel}: "
            f"{diag_fwd.frames} frames, {len(diag_fwd.unique_ids)} unique IDs\n"
            f"  obd2  ch{args.obd_channel}: "
            f"{obd_fwd.frames} frames, {len(obd_fwd.unique_ids)} unique IDs\n"
            f"  polls: ok={state['polls_ok']} err={state['polls_err']}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
