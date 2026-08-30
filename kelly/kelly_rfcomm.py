#!/usr/bin/env python3
"""Bluetooth SPP transport for the Kelly tools, via native macOS IOBluetooth.

WHY THIS EXISTS
---------------
The Kelly Bluetooth dongle is a Classic SPP bridge, and macOS publishes it as
a serial node (`/dev/cu.26061702`). On some Macs that node is inert: it opens
instantly, logs no Bluetooth activity, and passes zero bytes forever, while
`blueutil --is-connected` still reports 1 and the dongle's LED keeps blinking
(no SPP session). The pairing record is correct — `RFCOMMChannel = 1` in
com.apple.Bluetooth — the tty-to-RFCOMM bridge simply never fires.

Opening the RFCOMM channel through IOBluetooth directly sidesteps that layer
and works on the same machine, byte-for-byte in both directions (verified
against a CH340 wired to the dongle's SM-4P data pins).

`open_port()` is the single entry point both Kelly tools call. It hands back a
real `serial.Serial` for a USB adapter and an `RFCOMMPort` for a Bluetooth one,
so callers see one duck-typed interface either way. This module transports
bytes only; it has no idea what a Kelly frame is, and the read-only command
guard stays where it was, in each tool's `_transmit()`.

Needs `pyobjc-framework-IOBluetooth` (`pip install '.[bluetooth]'`), macOS only.
"""

import re
import time

import serial

# Accepts the two spellings IOBluetooth itself accepts.
_BT_ADDR = re.compile(r"^([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$")

# Chunk the run loop is pumped in while waiting for bytes. Small enough that a
# read timeout is honoured promptly, large enough not to spin.
_PUMP_INTERVAL = 0.05


def _iobluetooth():
    """Import IOBluetooth lazily so non-macOS hosts only fail if they ask for BT."""
    try:
        import IOBluetooth
        from Foundation import NSDate, NSObject, NSRunLoop
        import objc
    except ImportError as e:
        raise serial.SerialException(
            "Bluetooth SPP needs pyobjc-framework-IOBluetooth (macOS only): "
            f"pip install 'pyobjc-framework-IOBluetooth'  [{e}]"
        )
    return IOBluetooth, NSObject, NSRunLoop, NSDate, objc


def resolve_bt_address(port: str):
    """Return the Bluetooth address `port` refers to, or None if it's a serial port.

    Accepts a bare address (`20-24-06-19-06-77`) and also the serial node macOS
    names after the device (`/dev/cu.26061702`) — the latter is what the docs
    tell people to use, and the node that silently reads nothing on an affected
    Mac, so mapping it here is the whole point.
    """
    if _BT_ADDR.match(port):
        return port
    name = re.sub(r"^/dev/(cu|tty)\.", "", port)
    if name == port:  # not a /dev/cu.* or /dev/tty.* path
        return None
    try:
        IOBluetooth = _iobluetooth()[0]
    except serial.SerialException:
        return None  # no pyobjc: fall through to pyserial, as before
    for dev in IOBluetooth.IOBluetoothDevice.pairedDevices() or []:
        if dev.name() == name:
            return dev.addressString()
    return None


class RFCOMMPort:
    """A Classic-Bluetooth RFCOMM channel behind the slice of the pyserial API
    the Kelly tools use: read/write/flush/reset_input_buffer/close.

    Failures raise `serial.SerialException` so the callers' existing reconnect
    and error paths work unchanged.
    """

    def __init__(self, address: str, timeout: float = 0.15, channel_id=None):
        self.address = address
        self.timeout = timeout
        IOBluetooth, NSObject, NSRunLoop, NSDate, objc = _iobluetooth()
        self._runloop, self._nsdate = NSRunLoop, NSDate

        class _Delegate(NSObject):
            def init(self):
                self = objc.super(_Delegate, self).init()
                self.buf = bytearray()
                self.closed = False
                return self

            def rfcommChannelData_data_length_(self, chan, data, length):
                self.buf += bytes(data[:length])

            def rfcommChannelClosed_(self, chan):
                self.closed = True

        dev = IOBluetooth.IOBluetoothDevice.deviceWithAddressString_(address)
        if dev is None:
            raise serial.SerialException(f"no Bluetooth device at {address}")

        if channel_id is None:
            channel_id = self._sdp_channel(dev)

        self._delegate = _Delegate.alloc().init()
        ret, chan = dev.openRFCOMMChannelSync_withChannelID_delegate_(
            None, channel_id, self._delegate
        )
        if ret != 0 or chan is None:
            raise serial.SerialException(
                f"could not open RFCOMM channel {channel_id} on {address} "
                f"(IOReturn {ret}) — is the dongle powered and in range, and "
                f"not already held by a phone? These adapters take one host."
            )
        self._chan = chan

    def _sdp_channel(self, dev) -> int:
        """Ask the dongle which RFCOMM channel its serial port lives on."""
        dev.performSDPQuery_(None)
        self._pump(4.0)
        for rec in dev.services() or []:
            ret, cid = rec.getRFCOMMChannelID_(None)
            if ret == 0:
                return cid
        # SDP can come back empty if the device just woke; channel 1 is what the
        # dongle advertises and what macOS cached in com.apple.Bluetooth.
        return 1

    def _pump(self, seconds: float) -> None:
        """Run the run loop so IOBluetooth can deliver delegate callbacks."""
        self._runloop.currentRunLoop().runUntilDate_(
            self._nsdate.dateWithTimeIntervalSinceNow_(seconds)
        )

    def _check_open(self) -> None:
        if self._delegate.closed:
            raise serial.SerialException(f"RFCOMM channel to {self.address} closed")

    def read(self, size: int = 1) -> bytes:
        deadline = time.monotonic() + self.timeout
        while len(self._delegate.buf) < size:
            self._check_open()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._pump(min(remaining, _PUMP_INTERVAL))
        out = bytes(self._delegate.buf[:size])
        del self._delegate.buf[: len(out)]
        return out

    def write(self, data: bytes) -> int:
        self._check_open()
        ret = self._chan.writeSync_length_(bytes(data), len(data))
        if ret != 0:
            raise serial.SerialException(f"RFCOMM write failed (IOReturn {ret})")
        return len(data)

    def flush(self) -> None:
        pass  # writeSync has already gone out by the time it returns

    def reset_input_buffer(self) -> None:
        self._pump(0)  # collect anything already delivered, then drop it
        self._delegate.buf.clear()

    def close(self) -> None:
        try:
            self._chan.closeChannel()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_port(port: str, baud: int, timeout: float = 0.15):
    """Open `port` as a Kelly transport — Bluetooth RFCOMM or USB serial.

    `baud` is ignored for Bluetooth: over SPP the wire rate is fixed by the
    dongle's own UART setting and nothing the host sets ever reaches the wire.
    """
    address = resolve_bt_address(port)
    if address:
        return RFCOMMPort(address, timeout=timeout)
    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=timeout,
    )


def _demo() -> None:
    """Self-check: the port-vs-address routing, which is the part with branches.

    The RFCOMM path itself needs the dongle powered and in range, so it is
    exercised by `--port` against real hardware, not here.
    """
    assert resolve_bt_address("20-24-06-19-06-77") == "20-24-06-19-06-77"
    assert resolve_bt_address("20:24:06:19:06:77") == "20:24:06:19:06:77"
    assert resolve_bt_address("/dev/cu.usbserial-10") is None
    assert resolve_bt_address("/dev/ttyUSB0") is None
    assert resolve_bt_address("COM3") is None
    assert resolve_bt_address("/dev/cu.definitely-not-paired-xyz") is None
    print("kelly_rfcomm self-check OK")


if __name__ == "__main__":
    _demo()
