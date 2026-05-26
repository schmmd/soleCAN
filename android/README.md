# Solectrac Android app

Mirrors the ESP32's web dashboard (`esp32/src/dashboard.html`) over BLE so the
phone doesn't have to join the `solectrac` WiFi network. The app loads the same
HTML inside a `WebView` and pipes JSON snapshots — pushed from the ESP32's
Nordic UART Service whenever any displayed signal changes — into the page via a
`@JavascriptInterface` bridge.

## One-time setup

The Gradle wrapper JAR is not checked in. From this directory:

```bash
gradle wrapper --gradle-version 8.7
```

(or open the project in Android Studio, which will materialize it for you).

## Build & install

```bash
./gradlew installDebug
```

## Architecture

- `BleClient.kt` — scans for the NUS service UUID, connects to the first
  advertiser, negotiates MTU 517, subscribes to TX notifications, and
  reassembles framed messages (`[u16 BE length][payload]`).
- `MainActivity.kt` — hosts the WebView, manages runtime permissions, shows a
  status bar that auto-hides on connect, exposes the `SolectracBridge` JS
  interface, and forwards each JSON message via
  `window.dispatchSolectracUpdate(...)`.
- `assets/dashboard.html` — **manual copy** of `esp32/src/dashboard.html`.
  After editing the master, re-copy:

  ```bash
  cp ../esp32/src/dashboard.html app/src/main/assets/dashboard.html
  ```

## Permissions

- Android 12+: `BLUETOOTH_SCAN` (with `neverForLocation`) and `BLUETOOTH_CONNECT`.
- Android ≤ 11: `ACCESS_FINE_LOCATION` (required for BLE scans on legacy APIs).

## Wire protocol

The ESP32 sends, on each change, a single logical message:

```
[u16 big-endian length] [length bytes of compact JSON]
```

split across N BLE notifications of up to ~180 bytes each. The Android client
buffers raw notification bytes and slices out whole frames using the prefix.
