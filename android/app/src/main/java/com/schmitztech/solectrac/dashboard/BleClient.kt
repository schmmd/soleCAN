package com.schmitztech.solectrac.dashboard

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.util.Log
import org.json.JSONObject
import java.util.UUID

/**
 * Connects to the ESP32 over BLE and forwards reassembled JSON snapshots to a Listener.
 *
 * Scans for an advertiser exposing [NUS_SVC], connects to the first match,
 * negotiates a 517-byte MTU, subscribes to the TX characteristic, and
 * reassembles length-prefixed framed messages from the notification stream.
 *
 * Reconnects automatically when disconnected, with exponential backoff.
 */
@SuppressLint("MissingPermission")
class BleClient(
    private val context: Context,
    private val listener: Listener
) {

    interface Listener {
        /** Called on main thread. */
        fun onStateChange(state: State, detail: String)
        /** Called on main thread with one reassembled JSON message. */
        fun onJson(json: String)
    }

    enum class State { IDLE, SCANNING, CONNECTING, CONNECTED, DISCONNECTED, ERROR }

    private val handler = Handler(Looper.getMainLooper())
    private val btManager = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
    private val adapter: BluetoothAdapter? = btManager.adapter

    private var scanCallback: ScanCallback? = null
    private var gatt: BluetoothGatt? = null
    private var txChar: BluetoothGattCharacteristic? = null

    private val rxBuffer = ByteArrayBuilder()
    private var reconnectAttempts = 0
    private var wantConnected = false
    private var lastDevice: BluetoothDevice? = null

    fun start() {
        wantConnected = true
        beginScan()
    }

    fun stop() {
        wantConnected = false
        stopScanInternal()
        gatt?.disconnect()
        gatt?.close()
        gatt = null
        txChar = null
        rxBuffer.reset()
        notifyState(State.IDLE, "Stopped")
    }

    /** User-triggered: cancel any in-flight connection and start a fresh scan. */
    fun rescan() {
        gatt?.disconnect()
        gatt?.close()
        gatt = null
        txChar = null
        rxBuffer.reset()
        lastDevice = null
        reconnectAttempts = 0
        wantConnected = true
        beginScan()
    }

    // ── Scanning ──────────────────────────────────────────────────────────────

    private fun beginScan() {
        // Re-fetch scanner each time: BT stack restarts or adapter toggles can
        // invalidate the cached reference, leading to silent scan failures.
        val s = adapter?.bluetoothLeScanner
        if (adapter?.isEnabled != true || s == null) {
            notifyState(State.ERROR, "Bluetooth off")
            return
        }
        stopScanInternal()

        val filter = ScanFilter.Builder()
            .setServiceUuid(ParcelUuid(NUS_SVC))
            .build()
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()

        val cb = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                val dev = result.device ?: return
                // Guard against duplicate results racing into connectTo.
                if (gatt != null) return
                Log.i(TAG, "Found ${dev.address} rssi=${result.rssi}")
                stopScanInternal()
                connectTo(dev)
            }
            override fun onScanFailed(errorCode: Int) {
                Log.w(TAG, "Scan failed code=$errorCode")
                stopScanInternal()
                wantConnected = false
                notifyState(State.ERROR, "Scan failed ($errorCode)")
            }
        }
        scanCallback = cb
        notifyState(State.SCANNING, "Scanning for tractor…")
        try {
            s.startScan(listOf(filter), settings, cb)
        } catch (t: Throwable) {
            Log.w(TAG, "startScan threw", t)
            scanCallback = null
            wantConnected = false
            notifyState(State.ERROR, "Scan start failed")
            return
        }

        // Safety timeout: stop scan after 20 s and back off.
        handler.postDelayed(scanTimeout, 20_000)
    }

    private val scanTimeout = Runnable {
        if (gatt == null && wantConnected) {
            stopScanInternal()
            // No auto-retry: device may simply be off. Wait for the user to
            // tap Scan rather than burning battery scanning forever.
            wantConnected = false
            notifyState(State.DISCONNECTED, "No device found")
        }
    }

    private fun stopScanInternal() {
        handler.removeCallbacks(scanTimeout)
        scanCallback?.let { cb ->
            try { adapter?.bluetoothLeScanner?.stopScan(cb) } catch (_: Throwable) {}
        }
        scanCallback = null
    }

    // ── Connect & GATT ────────────────────────────────────────────────────────

    private fun connectTo(device: BluetoothDevice) {
        lastDevice = device
        notifyState(State.CONNECTING, "Connecting to ${device.address}")
        gatt = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    notifyState(State.CONNECTING, "Negotiating MTU")
                    g.requestMtu(517)
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    g.close()
                    gatt = null
                    txChar = null
                    rxBuffer.reset()
                    notifyState(State.DISCONNECTED, "Disconnected (status $status)")
                    if (wantConnected) scheduleReconnect()
                }
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            Log.i(TAG, "MTU=$mtu status=$status")
            g.discoverServices()
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            val svc = g.getService(NUS_SVC)
            val tx = svc?.getCharacteristic(NUS_TX)
            if (tx == null) {
                notifyState(State.ERROR, "NUS service not found")
                g.disconnect()
                return
            }
            txChar = tx
            g.setCharacteristicNotification(tx, true)
            val cccd = tx.getDescriptor(CCCD_UUID)
            if (cccd != null) {
                cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                g.writeDescriptor(cccd)
            }
        }

        override fun onDescriptorWrite(
            g: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int
        ) {
            if (descriptor.uuid == CCCD_UUID) {
                reconnectAttempts = 0
                notifyState(State.CONNECTED, "Connected")
            }
        }

        override fun onCharacteristicChanged(
            g: BluetoothGatt, characteristic: BluetoothGattCharacteristic
        ) {
            if (characteristic.uuid != NUS_TX) return
            val value = characteristic.value ?: return
            rxBuffer.append(value)
            drainFrames()
        }
    }

    private fun drainFrames() {
        while (true) {
            val frame = rxBuffer.takeFrame() ?: return
            val s = String(frame, Charsets.UTF_8)
            // Bookend check inside takeFrame is necessary but not sufficient — a
            // dropped chunk can leave us with a frame that happens to end in '}'
            // from inside the JSON. Parse here so a corrupt frame triggers a
            // buffer reset and we resync on the next 200ms push.
            try {
                JSONObject(s)
            } catch (_: Throwable) {
                Log.w(TAG, "frame failed JSON parse — resyncing")
                rxBuffer.reset()
                return
            }
            handler.post { listener.onJson(s) }
        }
    }

    // ── Backoff ───────────────────────────────────────────────────────────────

    private fun scheduleReconnect() {
        if (!wantConnected) return
        reconnectAttempts++
        // 1s, 2s, 4s, 8s, capped at 15s.
        val delayMs = (1000L shl minOf(reconnectAttempts - 1, 4)).coerceAtMost(15_000L)
        handler.postDelayed({
            if (!wantConnected) return@postDelayed
            // Prefer fresh scan over reusing cached device — handles MAC randomization.
            beginScan()
        }, delayMs)
    }

    private fun notifyState(state: State, detail: String) {
        handler.post { listener.onStateChange(state, detail) }
    }

    companion object {
        private const val TAG = "BleClient"
        val NUS_SVC: UUID = UUID.fromString("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
        val NUS_TX:  UUID = UUID.fromString("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")
        val CCCD_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805F9B34FB")
    }
}

/**
 * Reassembles length-prefixed messages from arbitrarily-sliced BLE notifications.
 *
 * Wire format: [u16 big-endian length] [length bytes of payload].
 * Notifications may carry any byte boundary, so we buffer and emit whole frames.
 */
private class ByteArrayBuilder {
    private var buf = ByteArray(4096)
    private var size = 0

    fun append(data: ByteArray) {
        // If a dropped notification has left us with garbage that keeps growing
        // without ever yielding a valid frame, cut our losses and resync on the
        // next push instead of buffering unboundedly.
        if (size + data.size > MAX_BUFFER) {
            android.util.Log.w("BleClient", "rxBuffer overflow — resyncing")
            size = 0
            if (data.size > MAX_BUFFER) return
        }
        ensure(size + data.size)
        System.arraycopy(data, 0, buf, size, data.size)
        size += data.size
    }

    fun reset() { size = 0 }

    /** Returns the next complete payload, or null if not enough bytes buffered. */
    fun takeFrame(): ByteArray? {
        if (size < 2) return null
        val len = ((buf[0].toInt() and 0xFF) shl 8) or (buf[1].toInt() and 0xFF)
        if (len == 0 || len > MAX_FRAME_LEN) {
            android.util.Log.w("BleClient", "implausible frame len=$len — resyncing")
            size = 0
            return null
        }
        if (size < 2 + len) return null
        // Payload is always a JSON object; mismatched bookends mean a dropped
        // chunk has desynced our byte count. Reset and wait for the next push.
        if (buf[2] != '{'.code.toByte() || buf[2 + len - 1] != '}'.code.toByte()) {
            android.util.Log.w("BleClient", "frame not JSON-bracketed — resyncing")
            size = 0
            return null
        }
        val out = buf.copyOfRange(2, 2 + len)
        val remaining = size - (2 + len)
        if (remaining > 0) System.arraycopy(buf, 2 + len, buf, 0, remaining)
        size = remaining
        return out
    }

    private fun ensure(needed: Int) {
        if (needed <= buf.size) return
        var n = buf.size
        while (n < needed) n *= 2
        buf = buf.copyOf(n)
    }

    companion object {
        private const val MAX_FRAME_LEN = 16 * 1024
        private const val MAX_BUFFER    = 32 * 1024
    }
}
