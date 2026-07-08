// Transparent USB <-> UART passthrough for the Kelly ETS serial link, on the
// RejsaCAN-ESP32-S3 board.
//
// Purpose: turn a RejsaCAN board into a dumb wire between the laptop and the
// Kelly KLS pump controller's serial diagnostic port (SM-4P). The ESP32 does
// nothing but shovel bytes between its native USB (to the laptop) and UART1
// (to the Kelly), so tools that expect a plain serial port — chiefly
// solectrac-kelly-monitor.py and solectrac-kelly-dump-config.py — can talk to
// the controller through the exact pins and 5 V-TTL protection the production
// firmware uses. If frames decode through here, the -DENABLE_KELLY build's
// wiring is proven good.
//
// This is the RejsaCAN sibling of ../esp32-bridge/esp32-bridge.ino (which
// targets an Adafruit Feather). Same idea, different board: the Kelly wires land
// on plain GPIOs 47/48 — NOT the rear RXD/TXD pads (the ESP32-S3 UART0 console
// pins GPIO44/43), which corrupted the receive waveform on this board.
//
// Wiring (5 V-TTL Kelly port — see kelly/README.md). RX/TX are deliberately
// moved OFF the board's default RXD/TXD pads (GPIO44/43 = the ESP32-S3 UART0
// console pins) onto plain GPIOs 48/47: on this board those console pins pick up
// framing-error corruption that the Feather's plain GPIO does not. Wire to
// whatever pads expose GPIO48/47:
//   Kelly Tx (green) --[ 1-2.2 kOhm series ]--> GPIO47   (RX)
//   Kelly Rx (blue)  <-------------------------- GPIO48   (TX)   (plain wire)
//   Kelly V- (black) <-------------------------- board GND
//   Kelly V+ (red, 12 V) -- LEAVE UNCONNECTED  (never wire to VCC/HV — it is 12 V)
//
// The series resistor is NOT a divider: the ESP32's internal clamp diode holds
// the RX pin at 3.3 V and the resistor limits clamp current to <1 mA. The TX
// direction needs no protection (3.3 V clears the Kelly's input-high threshold).
//
// Grounding matters more than anything here. The Kelly is powered from the
// traction pack, so its V- is NOT chassis ground. Keep this board floating
// (bench-power the laptop off its battery; don't let the board share tractor
// chassis ground) or the chassis-to-traction noise corrupts the link. Always
// wire V- as the signal return. See kelly/README.md "Grounding dominates".
//
// Baud: the Kelly link runs at its documented 19200 baud on the UART1 (Kelly)
// side. (An earlier ~19,900 theory came from a corrupted receive path — the
// noisy console pads plus an Arduino core 2.0.x firmware bug — see
// kelly/README.md "Actual line rate".) The USB (host) side baud is irrelevant:
// this is native USB CDC, so
// whatever baud the monitor opens the port at is ignored by the hardware and
// only the value below governs the wire to the Kelly. Don't "fix" a decode
// problem by changing the monitor's --baud; change KELLY_BAUD here.
//
// Build notes (Arduino IDE):
//   * Board: "ESP32S3 Dev Module" (the RejsaCAN is a bare ESP32-S3-WROOM-1).
//   * USB CDC On Boot: Enabled  — makes `Serial` the native-USB port the laptop
//     sees. (If it's disabled, swap `Serial` -> `USBSerial` below.)
//   * USB Mode: Hardware CDC and JTAG.
//   * Flash Size: 16MB.  PSRAM: OPI PSRAM (harmless if left default).
// Or flash the equivalent PlatformIO build instead: the production firmware's
//   -DKELLY_PASSTHROUGH flag on [env:rejsacan] does exactly this.
//
// Usage (tractor powered — the Kelly only answers with PWR above ~18 V):
//   python3 kelly/solectrac-kelly-monitor.py --port /dev/cu.usbmodemXXXX
//   python3 kelly/solectrac-kelly-dump-config.py --port /dev/cu.usbmodemXXXX

// Set to 1 to emit a once-per-second heartbeat on the USB port. Use it to prove
// the sketch runs and the device->host USB path works with no Kelly wired at
// all. MUST be 0 for real monitoring — the heartbeat text corrupts the frame
// stream the monitor parses.
#define SELFTEST 0

// Kelly ETS wire rate on the UART1 (Kelly) side — the documented 19200. See the
// baud note in the header. The host/USB side runs at native-USB speed regardless
// of this value.
static const long KELLY_BAUD = 19200;

// Pin map. RX/TX are moved off the default RXD/TXD pads (GPIO44/43, the
// ESP32-S3 UART0 console pins) onto plain GPIOs 47/48: on this board the console
// pins picked up framing-error corruption the Feather's plain GPIO did not.
// GPIO47/48 are free on the WROOM-1-N16R8 (not strapping, not flash/PSRAM,
// unused by the production firmware); any UART peripheral can route to them.
static const int KELLY_RXD_PIN     = 47;   // <- Kelly Tx (green), via series resistor
static const int KELLY_TXD_PIN     = 48;   // -> Kelly Rx (blue)
static const int FORCE_ON_PIN      = 17;   // hold HIGH so the auto-shutdown circuit keeps power on
static const int WARN_LED_PIN      = 11;   // yellow, active-high: lit = firmware ready
static const int ACTIVITY_LED_PIN  = 10;   // blue,  active-high: blinks on Kelly->host traffic

// How long the blue LED stays lit after the last byte from the Kelly.
static const unsigned long LED_ON_MS = 60;

void setup() {
  // Keep the board powered regardless of the host 12 V rail. Without this the
  // RejsaCAN's auto-shutdown circuit can cut power mid-session (it is designed
  // to ride out key-off), which would drop the USB port under the monitor.
  pinMode(FORCE_ON_PIN, OUTPUT);
  digitalWrite(FORCE_ON_PIN, HIGH);

  // LEDs are the only safe feedback channel here: anything printed to `Serial`
  // would corrupt the byte stream the monitor parses. Yellow = ready, blue =
  // Kelly is answering.
  pinMode(WARN_LED_PIN, OUTPUT);
  pinMode(ACTIVITY_LED_PIN, OUTPUT);
  digitalWrite(WARN_LED_PIN, LOW);
  digitalWrite(ACTIVITY_LED_PIN, LOW);

  Serial.begin(KELLY_BAUD);                              // native USB CDC to laptop
  Serial1.begin(KELLY_BAUD, SERIAL_8N1, KELLY_RXD_PIN, KELLY_TXD_PIN);

  digitalWrite(WARN_LED_PIN, HIGH);                      // ready
#if SELFTEST
  delay(200);
  Serial.println("kelly rejsacan passthrough selftest: booted");
#endif
}

void loop() {
  while (Serial.available())  Serial1.write(Serial.read());   // laptop -> Kelly

  bool rx_activity = false;
  while (Serial1.available()) {                               // Kelly  -> laptop
    Serial.write(Serial1.read());
    rx_activity = true;
  }

  // Blue LED tracks Kelly->host traffic so a bench operator can see the link is
  // alive without reading the decoded output.
  static unsigned long last_rx_ms = 0;
  static bool saw_rx = false;
  unsigned long now = millis();
  if (rx_activity) { last_rx_ms = now; saw_rx = true; }
  digitalWrite(ACTIVITY_LED_PIN, (saw_rx && now - last_rx_ms < LED_ON_MS) ? HIGH : LOW);

#if SELFTEST
  static unsigned long last_beat_ms = 0;
  if (now - last_beat_ms >= 1000) {
    last_beat_ms = now;
    Serial.println("kelly rejsacan passthrough selftest: alive");
  }
#endif
}
