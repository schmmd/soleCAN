// Transparent USB <-> UART bridge for bench-testing the Kelly ETS serial link.
//
// Purpose: prove that a 3.3 V ESP32 can read the Kelly KLS pump controller's
// 5 V TTL diagnostic port (SM-4P) before wiring it into the RejsaCAN board.
// The ESP32 does nothing but shovel bytes between its native USB (to the laptop)
// and UART1 (to the Kelly). Run the normal monitor against the ESP32's serial
// port and, if frames decode, the 5 V -> 3.3 V protection on RX is good.
//
// Wiring (Adafruit Reverse TFT Feather, 5 V TTL Kelly port — see kelly/README.md):
// Use the pins silkscreened RX and TX on the Feather (GPIO38 / GPIO39).
//   Kelly Tx (green) --[ 1-2.2 kOhm series ]--> Feather RX pin (GPIO38)
//   Kelly Rx (blue)  <-------------------------- Feather TX pin (GPIO39)  (plain wire)
//   Kelly V- (black) <-------------------------- Feather GND
//   Kelly V+ (red, 12 V) -- LEAVE UNCONNECTED
//
// The series resistor is NOT a divider: the ESP32's internal clamp diode holds
// the pin at 3.3 V and the resistor limits clamp current to <1 mA. The TX
// direction needs no protection (3.3 V clears the Kelly's input-high threshold).
//
// Build notes (Arduino IDE):
//   * Board: your ESP32-S3 dev board.
//   * Enable "USB CDC On Boot" so `Serial` is the native-USB port the laptop
//     sees (or swap `Serial` -> `USBSerial` below).
//   * GPIO18/17 are safe UART pins on most S3 boards; avoid strapping pins
//     (0, 45, 46). Adjust KELLY_RX/KELLY_TX if your board differs.
//
// Usage:
//   python3 kelly/solectrac-kelly-monitor.py --port /dev/cu.usbmodemXXXX
//   (tractor powered — the Kelly only answers with PWR above ~18 V)

// Set to 1 to emit a once-per-second heartbeat on the USB port. Use it to prove
// the sketch runs and the device->host USB path works, with no wiring at all.
// MUST be 0 for real Kelly monitoring — the heartbeat text corrupts the frame
// stream the monitor parses.
#define SELFTEST 0

// Feather silkscreen RX = GPIO38, TX = GPIO39. These are the pins labeled RX/TX
// on the board edge — the ones actually being used. (Not A0/A1.)
static const long KELLY_BAUD = 19200;   // Kelly ETS: 19200 8N1 (an earlier ~19,900
                                        // reading came from a corrupted receive path;
                                        // see kelly/README.md "Actual line rate")
static const int  KELLY_RX   = 38;      // "RX" pin  <- Kelly Tx (green), via series resistor
static const int  KELLY_TX   = 39;      // "TX" pin  -> Kelly Rx (blue)

void setup() {
  Serial.begin(KELLY_BAUD);                              // USB CDC to laptop
  Serial1.begin(KELLY_BAUD, SERIAL_8N1, KELLY_RX, KELLY_TX);
#if SELFTEST
  delay(200);
  Serial.println("kelly-bridge selftest: booted");
#endif
}

#if SELFTEST
static unsigned long lastBeat = 0;
#endif

void loop() {
  while (Serial.available())  Serial1.write(Serial.read());   // laptop -> Kelly
  while (Serial1.available()) Serial.write(Serial1.read());   // Kelly  -> laptop
#if SELFTEST
  if (millis() - lastBeat >= 1000) {
    lastBeat = millis();
    Serial.println("kelly-bridge selftest: alive");
  }
#endif
}
