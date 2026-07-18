/*
 * Solectrac CAN monitor for ESP32
 *
 * Reads J1939 CAN frames from the Solectrac 25G tractor (250 kbit/s) via the
 * ESP32's built-in TWAI peripheral, decodes all known signals, and serves
 * them as JSON over a simple HTTP endpoint.
 *
 * Hardware:
 *   Connect a CAN transceiver (SN65HVD230, TJA1050, MCP2551) between the
 *   ESP32 and the CAN bus. Adjust CAN_TX_PIN and CAN_RX_PIN below to match
 *   your wiring.
 *
 * Endpoints:
 *   GET /       — mobile-friendly dashboard (auto-refreshing)
 *   GET /json   — raw JSON
 *   anything else 302-redirects to / (captive-portal auto-open on the AP)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <DNSServer.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include "driver/twai.h"
#include <sys/select.h>
#if defined(BOARD_LILYGO_T2CAN)
  #include <SPI.h>
  #include <ACAN2515.h>
#endif
#if defined(BOARD_REJSACAN)
  // Onboard microSD session logging (see the "SD-card session logging" section).
  #include <SPI.h>
  #include <SD.h>
  #include <esp_timer.h>
  #include <esp_heap_caps.h>
  #include "freertos/stream_buffer.h"
#endif
// Energy-saving deep sleep is on by default; build with -DNO_AUTOSHUTDOWN to
// keep the board fully awake through CAN-bus silence.
#if !defined(NO_AUTOSHUTDOWN) && !defined(AUTOSHUTDOWN)
  #define AUTOSHUTDOWN
#endif
#if defined(AUTOSHUTDOWN)
  #include <esp_sleep.h>
  #include "driver/gpio.h"   // gpio_hold_en() etc. — only called on BOARD_REJSACAN, but cheap to include for all
#endif

// ── Configuration ─────────────────────────────────────────────────────────────

// WiFi identity overrides (WIFI_SSID / WIFI_PASS / AP_SSID / AP_PASS /
// MDNS_NAME env vars), generated into the build dir by
// inject_build_overrides.py. Absent when no override is set.
#if __has_include("wifi_overrides.h")
#include "wifi_overrides.h"
#endif

// Optional home network the board also joins (for bench use). Leave unset to
// run AP-only — the board still broadcasts its own hotspot (see AP_SSID below),
// which is the stable default for field use. Set both to join a network.
#ifndef WIFI_SSID
#define WIFI_SSID ""
#endif
#ifndef WIFI_PASS
#define WIFI_PASS ""
#endif

// ── CAN transmit / bus mode ───────────────────────────────────────────────────
// Safe by default: both CAN controllers come up in *listen-only* mode, so the
// hardware physically cannot drive the bus — no ACKs, no error frames, no
// injection. This makes the stock firmware a true passive tap; a bug in a
// transmit path can't perturb the bus because the silicon never drives a bit.
//
// Build with -DCAN_ALLOW_TX to switch both controllers to NORMAL mode. That
// makes them ACK received frames AND arms every transmit path — SLCAN t/T
// injection over USB and socketcand `< send >` over WiFi. Only build this way
// when you intend to write to the bus. Note: in listen-only mode a two-node
// bench setup gets no ACKs (the lone talker retransmits and eventually goes
// bus-off); on the tractor's four-ECU bus the real nodes ACK each other.
#if defined(CAN_ALLOW_TX)
  #define CAN_TX_ENABLED 1
#else
  #define CAN_TX_ENABLED 0
#endif

// Per-board pin map. Selected via -DBOARD_* in platformio.ini.
#if defined(BOARD_ADAFRUIT_FEATHER_S3)
  #define CAN_TX_PIN       GPIO_NUM_8
  #define CAN_RX_PIN       GPIO_NUM_14
  #define LED_PIN          GPIO_NUM_33
  #define LED_POWER_PIN    GPIO_NUM_21
  #define LED_IS_NEOPIXEL  1
#elif defined(BOARD_LILYGO_T2CAN)
  // LilyGo T-2CAN exposes both CAN ports: CAN B is the ESP32-S3's native TWAI
  // on GPIO 6/7; CAN A is an MCP2515 on SPI. We stream both buses out
  // over socketcand as channels can0 (TWAI) and can1 (MCP2515).
  // pin_config.h documents no user-controllable LED.
  #define CAN_TX_PIN       GPIO_NUM_7
  #define CAN_RX_PIN       GPIO_NUM_6
  #define MCP2515_CS_PIN   GPIO_NUM_10
  #define MCP2515_SCK_PIN  GPIO_NUM_12
  #define MCP2515_MOSI_PIN GPIO_NUM_11
  #define MCP2515_MISO_PIN GPIO_NUM_13
  #define MCP2515_INT_PIN  GPIO_NUM_8
  // Active-low hardware reset, wired to the MCP2515 RESET pin. The ESP32 must
  // drive this high to bring the chip out of reset; left undriven the MCP2515
  // never answers SPI and ACAN2515::begin() returns kNoMCP2515 (err bit 0).
  #define MCP2515_RST_PIN  GPIO_NUM_9
  // MCP2515 crystal frequency. The LilyGo T-2CAN populates a 16 MHz can (the
  // stock firmware calls setBitrate() with the default 16 MHz clock).
  #define MCP2515_QUARTZ_HZ (16UL * 1000UL * 1000UL)
  #define HAS_MCP2515      1
  #define LED_IS_NEOPIXEL  0
#elif defined(BOARD_REJSACAN)
  // RejsaCAN-ESP32-S3 v3.x: ESP32-S3-WROOM-1-N16R8 driving a TJA1051-style
  // transceiver. Pin numbers come from the vendor's getall_s3.ino example.
  #define CAN_TX_PIN       GPIO_NUM_14
  #define CAN_RX_PIN       GPIO_NUM_13
  // CAN_RS controls the transceiver's mode: LOW = high-speed normal,
  // HIGH = listen-only/low-power. Leaving it floating puts the transceiver in
  // slope-control mode, which silently mangles 250/500 kbit/s frames.
  #define CAN_RS_PIN       GPIO_NUM_38
  // FORCE_ON keeps the auto-shutdown circuit from cutting power once the host
  // 12 V drops (the board is designed to ride out engine cranking / key-off).
  // Driving it high here means the firmware decides when to shut down, not the
  // hardware — convenient for continuous tractor capture.
  #define FORCE_ON_PIN     GPIO_NUM_17
  // Two user LEDs. Yellow carries warnings (no WiFi / CAN didn't init); blue
  // carries CAN-bus activity. Each is independently controlled — the board's
  // third LED is a hard-wired green power indicator we can't drive.
  #define WARN_LED_PIN     GPIO_NUM_11   // yellow
  #define ACTIVITY_LED_PIN GPIO_NUM_10   // blue
  #define LED_IS_DUAL_GPIO 1
  #define LED_IS_NEOPIXEL  0
  // 12 V input sense: an R18(120K)/R6(33K) divider off the protected VCC rail
  // feeds GPIO 9 (ADC1_CH8). Vin = Vadc × (R18+R6)/R6 = Vadc × 153/33 ≈ ×4.636.
  // With the default 12 dB attenuation the ADC saturates around ~14.4 V at the
  // VIN terminal — plenty for a 12 V tractor accessory rail.
  #define VIN_SENSE_PIN    GPIO_NUM_9
  #define VIN_DIVIDER_NUM  153    // (R18 + R6) in kΩ
  #define VIN_DIVIDER_DEN  33     // R6 in kΩ
  #define HAS_VIN_SENSE    1
  // Onboard microSD reader on a dedicated SPI bus. Pins verified against the
  // vendor's s3-sdcard.ino example and "RejsaCAN v3.4 - Pinout.h". They avoid
  // GPIO33–37, which the N16R8 module's octal PSRAM occupies — using those
  // would guru-meditate at boot. CS is the only signal unique to the card;
  // nothing else on this board is on SPI.
  #define SD_SCK_PIN       GPIO_NUM_39
  #define SD_MOSI_PIN      GPIO_NUM_40
  #define SD_MISO_PIN      GPIO_NUM_41
  #define SD_CS_PIN        GPIO_NUM_45
  #define HAS_SD           1
#else
  #error "Define a board: BOARD_ADAFRUIT_FEATHER_S3, BOARD_LILYGO_T2CAN, or BOARD_REJSACAN"
#endif

#ifndef LED_IS_DUAL_GPIO
  #define LED_IS_DUAL_GPIO 0
#endif

// WiFi runs in dual AP+STA mode: the board always broadcasts its own hotspot
// (so it's reachable in the field), and concurrently tries to join the
// configured home network for bench use. AP IP is 192.168.4.1. MDNS_NAME is
// the mDNS hostname the board advertises (MDNS_NAME.local).
//
// AP_SSID/AP_PASS/MDNS_NAME default to the values below. They can optionally
// be overridden at build time via environment variables (see .env.example);
// when unset, the build is unchanged.
#ifndef AP_SSID
#define AP_SSID "tractor"
#endif
#ifndef AP_PASS
#define AP_PASS "electricity"
#endif
#ifndef MDNS_NAME
#define MDNS_NAME "tractor"
#endif

// ── J1939 source addresses ────────────────────────────────────────────────────

#define SRC_BMS         0xF3   // BMS broadcast
#define SRC_BMS_CHGR_IF 0xF4   // BMS charger-interface role (sends 0x000600)
#define SRC_CHARGER     0xE5   // External charger
#define SRC_VEHICLE     0xD0   // Vehicle controller
#define SRC_MOTOR       0xCA   // Motor / drive ECU
#define SRC_DASH        0x12   // Dashboard heartbeat

// ── PGN constants ─────────────────────────────────────────────────────────────

#define PGN_CELL_FIRST  0xF113   // BMS cell voltage frames (4 cells each)
#define PGN_CELL_LAST   0xF13C
#define PGN_TEMP_FIRST  0xF155   // BMS module temp frames (8 temps each)
#define PGN_TEMP_LAST   0xF15E
#define PGN_F100        0xF100   // Pack status: V, I, SoC
#define PGN_F102        0xF102   // Cell min/max summary
#define PGN_F104        0xF104   // Temp min/max summary
#define PGN_F106        0xF106   // BMS state flags
#define PGN_F107        0xF107   // BMS current limits
#define PGN_F108        0xF108   // BMS active fault bitmap
#define PGN_FF50        0xFF50   // Charger telemetry
#define PGN_FF21        0xFF21   // Motor telemetry / dash heartbeat
#define PGN_FECA        0xFECA   // DM1 (Active Diagnostic Trouble Codes)
#define PGN_PROP_0600   0x0600   // BMS→charger command (PDU1, dest 0xE5)

// ── Decode constants ──────────────────────────────────────────────────────────

#define NUM_CELLS               20
#define NUM_TEMPS               7
#define TEMP_OFFSET_C           40
#define PACK_CURRENT_BIAS_RAW   0x7D00   // raw u16 value at 0 A
#define PACK_CURRENT_LSB_A      0.1f     // A per bit
#define PACK_VOLTAGE_LSB_V      0.1f     // V per bit (F100F3 / FF50E5 bytes 0-1 BE)
#define RPM_BIAS                0x0C80   // raw u16 value at 0 RPM
#define LIMIT_CURRENT_LSB_A     0.01f    // A per bit for F107 limits
#define LIMIT_POWER_EXTRA_LSB_W 10.0f    // W per bit for F107 charge allowance above 100 A baseline
#define CHARGER_I_LSB_A         0.1f     // A per bit for charger current

// FF50E5 byte 4: Elcon/TC charger fault flags. 0x00 = actively delivering.
#define CHGR_FLAG_HW_FAIL       0x01     // hardware failure
#define CHGR_FLAG_OVER_TEMP     0x02     // charger over-temperature
#define CHGR_FLAG_NO_AC         0x04     // input (AC) voltage abnormal / absent
#define CHGR_FLAG_NO_BATTERY    0x08     // battery voltage not detected at output
#define CHGR_FLAG_COMM_TIMEOUT  0x10     // no 1806E5F4 command received
#define PACK_CAPACITY_WH        25000.0f // nominal usable pack energy (Solectrac e25 spec)

// ── BMS fault code tables ─────────────────────────────────────────────────────
// Bytes 0–6: each element maps one bit (LSB first) to a vendor fault code.
// 0 = silent (no code on dashboard for this bit).
// Based on injection sweep 2026-05-10.

static const uint8_t FAULT_BYTES_0_TO_6[7][8] = {
    {100, 100, 101, 101, 102, 102, 103, 103},   // byte 0
    {104, 104, 105, 105, 106, 106, 107, 107},   // byte 1
    {108, 108, 109, 109, 110, 110, 111, 111},   // byte 2
    {112, 112, 113, 113,   0,   0,   0,   0},   // byte 3 (114/115 reserved)
    {116, 117, 118, 119, 120, 121, 122, 123},   // byte 4
    {124, 125, 126, 127,   0,   0,   0,   0},   // byte 5
    {  0,   0,   0,   0,   0,   0,   0,   0},   // byte 6 (all silent)
};

// Byte 7: bit → code. 0 = silent. Bit 5 and bit 6 both map to 144 (confirmed
// duplicate). Code 146 does NOT appear in F108.
static const uint8_t FAULT_BYTE7[8] = {140, 0, 0, 142, 143, 144, 144, 145};

// ── State structs ─────────────────────────────────────────────────────────────

struct PackState {
    float   voltage_v  = NAN;
    float   current_a  = NAN;
    int32_t current_raw = -1;
    float   power_w    = NAN;
    uint8_t soc_raw    = 0;
    float   soc_pct    = NAN;
    // F102
    int16_t cell_max_mv   = -1;
    int16_t cell_min_mv   = -1;
    int16_t cell_spread_mv = -1;
    uint8_t cell_max_n    = 0;
    uint8_t cell_min_n    = 0;
    int16_t cell_spread_mv_reported = -1;
    float   v_estimate    = NAN;
    // F104
    int8_t  temp_max_c   = INT8_MIN;
    int8_t  temp_min_c   = INT8_MIN;
    uint8_t temp_max_n   = 0;
    uint8_t temp_min_n   = 0;
    int8_t  temp_spread_c = -1;
};

// Raw F106 state bitmaps. The individual bits are decoded downstream (see
// dashboard.html B0_NAMES/B1_NAMES and solecan_proto F106), not here.
struct BmsStateFlags {
    uint8_t byte0 = 0, byte1 = 0;
    bool    valid = false;
};

struct BmsLimits {
    float    discharge_a   = NAN;
    float    charge_a      = NAN;
    float    charge_power_extra_w = NAN;
    uint8_t  mode          = 0;
    uint8_t  byte5         = 0;
    bool     valid         = false;
    uint32_t last_seen_ms  = 0;
};

struct BmsFaults {
    uint8_t  bytes[8]          = {};
    uint64_t active_codes_mask = 0;  // bit (code-100) set if code active
    bool     any_fault         = false;
};

struct MotorState {
    int16_t  rpm_signed    = 0;
    uint16_t rpm_magnitude = 0;
    int8_t   direction     = 0;
    uint8_t  range    = 1;
    // LE u16: commanded effort magnitude; peaks past 255 under hard
    // acceleration (observed 262), so a single byte would wrap.
    uint16_t torque_raw  = 0;
    int8_t   controller_temp_c = INT8_MIN;
    int8_t   motor_temp_c      = INT8_MIN;
    bool     valid             = false;
    uint32_t last_seen_ms      = 0;
};

struct ChargerState {
    uint8_t  flags    = 0;
    uint16_t v_raw    = 0;
    uint16_t i_raw    = 0;
    float    voltage_v = NAN;
    float    current_a = NAN;
    bool     valid    = false;
};

struct ChgrCmdState {
    float    voltage_v = NAN;
    float    current_a = NAN;
    uint8_t  enable    = 1;
    uint16_t v_raw     = 0;
    uint16_t i_raw     = 0;
    bool     valid     = false;
};

struct Dm1State {
    uint8_t  lamp_byte0 = 0, lamp_byte1 = 0;
    uint32_t dtc_spn    = 0;
    uint8_t  dtc_fmi    = 0, dtc_cm = 0, dtc_oc = 0;
    bool     valid      = false;
};

// ── Global state ──────────────────────────────────────────────────────────────
// All updated from the CAN decode path inside loop(), read when building JSON
// from the same thread — no locking needed.

float       g_cell_v[NUM_CELLS];
float       g_temp_c[NUM_TEMPS];
PackState   g_pack;
BmsStateFlags g_bms_state;
BmsLimits   g_bms_limit;
BmsFaults   g_bms_faults;
MotorState  g_motor;
ChargerState g_charger;
ChgrCmdState g_chgr_cmd;
uint8_t     g_vc_state   = 0xFF;   // 0xFF = never seen
uint8_t     g_dash_alive = 0xFF;
Dm1State    g_dm1;

// CAN bus health counters
uint32_t    g_frames_rx      = 0;   // total frames received
uint32_t    g_frames_decoded = 0;   // frames matching a known PGN/source
uint32_t    g_last_frame_ms  = 0;   // millis() at last received frame
uint32_t    g_can_recoveries = 0;   // bus-off recoveries initiated since boot
uint32_t    g_socketcand_tx_dropped = 0;   // frames dropped on full client TCP buffers
bool        g_can_initialized = false;
bool        g_ap_running      = false;

// STA join telemetry for /config. Written by the WiFi event callback, which
// runs on the system event task rather than the loop task — hence volatile
// single-word values (torn reads aren't possible at word width or below).
volatile uint32_t g_sta_disconnects = 0;
volatile uint8_t  g_sta_last_disconnect_reason = 0;   // 0 = never disconnected

#if defined(HAS_MCP2515)
bool        g_mcp_initialized = false;
uint32_t    g_mcp_init_err    = 0xFFFFFFFFUL;   // sentinel: never attempted
uint32_t    g_mcp_frames_rx   = 0;
// MCP2515: external classic-CAN 2.0B controller wired to SPI, run at 250 kbit/s
// to match the J1939 main bus. The library's RX buffers are drained from
// loop(); frames are forwarded to socketcand as channel can1. Crystal frequency
// is board-dependent — see MCP2515_QUARTZ_HZ if can1 stays silent.
static ACAN2515 g_mcp(MCP2515_CS_PIN, SPI, MCP2515_INT_PIN);
#endif

#if defined(HAS_VIN_SENSE)
float       g_vin_v = NAN;   // most recent 12 V supply reading, averaged
#endif

#if defined(AUTOSHUTDOWN)
esp_sleep_wakeup_cause_t g_wake_cause = ESP_SLEEP_WAKEUP_UNDEFINED;   // set once in setup()
#endif

// Session energy tracking (integrated power since boot)
uint32_t    g_session_last_ms   = 0;
uint32_t    g_session_active_ms = 0;   // sum of valid dt's — excludes bus-silent gaps
float       g_session_wh_drawn  = 0.0f;
float       g_session_wh_charged = 0.0f;
// First BMS-published SOC seen this session; current - start = session ΔSOC.
float       g_session_soc_start_pct = NAN;

WebServer server(80);
DNSServer  dns_server;

// Dashboard HTML, embedded at build time via board_build.embed_txtfiles in
// platformio.ini. The linker generates these symbols from the file path:
// src/dashboard.html → _binary_src_dashboard_html_{start,end}. The data is
// null-terminated (embed_txtfiles), so the start pointer is usable as a
// C-string for server.send_P().
extern const uint8_t dashboard_html_start[] asm("_binary_src_dashboard_html_start");
extern const uint8_t dashboard_html_end[]   asm("_binary_src_dashboard_html_end");

// ── SD-card session logging (RejsaCAN) ──────────────────────────────────────────
// Records two streams to the onboard microSD whenever a card is present at boot:
//
//   /sNNNNN/can_PP.asc    every received CAN frame, Vector ASCII — replayable by
//                         solecan-analyze.py / solecan-stream.py --replay unchanged
//   /sNNNNN/data_PP.jsonl one buildJson() snapshot per line at SD_JSON_HZ (default 1)
//
// Design (see also the plan): loop() (core 1) only *formats* bytes and pushes them
// into two PSRAM ring buffers; a dedicated writer task (core 0) does every SD I/O.
// This keeps the decoded-state globals single-threaded — buildJson() and the .asc
// formatter both run on the loop task, and only opaque bytes cross to the writer —
// and it keeps SD stalls (block erase) off the CAN drain: a full ring drops whole
// lines (counted) rather than blocking loop(). Mid-session SD failures are
// handled by remount-and-resume (see sdRecoverOrFail); only repeated back-to-back
// failures latch "error" until reboot. No hot-insert: the card is probed once at
// boot; if it isn't there, the whole feature stays dormant (reboot to use a card
// inserted later).
#if defined(HAS_SD)

// ── Tunables ──
#ifndef SD_JSON_HZ
#define SD_JSON_HZ          1                         // decoded-snapshot cadence
#endif
#define SD_MAX_PART_BYTES   (64ULL * 1024 * 1024)     // roll raw/json parts at this size
#define SD_MIN_FREE_BYTES   (512ULL * 1024 * 1024)    // reap oldest sessions below this
#define SD_FLUSH_MS         1000                      // fsync cadence → ≤~1 s lost on power cut
#define SD_RAW_RING_BYTES   (1024UL * 1024)           // PSRAM ring for raw .asc
#define SD_JSON_RING_BYTES  (256UL * 1024)            // PSRAM ring for json lines
#define SD_FREE_CHECK_MS    10000                     // usedBytes() is slow — poll sparingly
#define SD_RECOVER_ATTEMPTS 5                         // remount tries before latching error
#define SD_RECOVER_DELAY_MS 250                       // backoff base between remount tries
#define SD_WRITE_CHUNK_BYTES 8192                     // batch writes into bursts this size

struct SdState {
    const char* state = "no_card";     // no_card | logging | error
    uint32_t          session   = 0;
    volatile uint32_t kb_written = 0;  // writer task (core 0) owns these two
    volatile uint32_t free_mb    = 0;
    const char*       fail_op  = "";     // which SD call failed (state == "error")
    volatile uint32_t fail_kb  = 0;      // KB written when it failed
    volatile uint32_t recoveries = 0;    // successful remount-and-resume cycles
};
static SdState g_sd;

// Set true once at boot after a successful mount + first session open; gates the
// producer in loop(). Only ever cleared again (mid-session card failure), never
// re-armed until reboot. Cross-core rule for this flag and the status fields in
// SdState/SdStream: every field has exactly one writer, and readers (buildJson)
// tolerate a torn multi-field snapshot — no control flow crosses the core
// boundary except this flag; the writer task otherwise sees nothing but bytes
// in the ring buffers.
volatile bool g_sd_active = false;

// Serializes every SD/SPI touch between the writer task (core 0) and the
// /sd/* HTTP handlers (loop task, core 1). The writer holds it per
// drain/flush/roll burst; HTTP holds it per operation — and per read chunk
// while streaming a tar — so a download stalls logging only briefly and the
// PSRAM rings absorb it. sdFail() must release it before self-deleting.
static SemaphoreHandle_t g_sd_mutex = nullptr;

struct SdLock {
    SdLock()  { xSemaphoreTake(g_sd_mutex, portMAX_DELAY); }
    ~SdLock() { xSemaphoreGive(g_sd_mutex); }
};

// One logged stream: a PSRAM ring fed by loop() (core 1) and drained to the
// current part file by the writer task (core 0). The raw .asc and the json
// .jsonl are two instances; everything that touches a stream goes through the
// shared helpers below so the two lifecycles can't drift apart.
struct SdStream {
    const char*          path_fmt;    // "/s%05lu/<name>_%02u.<ext>" (session, part)
    const char*          write_op;    // fail_op tag for a failed write
    const char*          open_op;     // fail_op tag for a failed part open
    bool                 asc_header;  // new parts get the Vector ASCII header
    size_t               ring_bytes;
    StreamBufferHandle_t sb = nullptr;
    StaticStreamBuffer_t sb_struct;
    uint8_t*             storage = nullptr;
    File                 file;            // writer task (core 0) owns these two
    uint64_t             part_bytes = 0;
    volatile uint16_t    part = 0;
    volatile uint32_t    dropped = 0;     // producer (core 1) owns this
};
static SdStream g_sd_raw  = { "/s%05lu/can_%02u.asc",    "raw_write",  "raw_open",  true,  SD_RAW_RING_BYTES };
static SdStream g_sd_json = { "/s%05lu/data_%02u.jsonl", "json_write", "json_open", false, SD_JSON_RING_BYTES };

static int64_t g_sd_session_start_us = 0;   // esp_timer µs at session open (log t=0)

// ── Producer side (runs on the loop task, core 1) ──

// Format one frame as a Vector ASCII data line and push it to the raw ring.
// Line-atomic: if the whole line doesn't fit, drop it and count it rather than
// write a fragment or block the CAN drain.
static inline void sdEnqueueRaw(const twai_message_t& msg) {
    char line[96];
    double ts = (esp_timer_get_time() - g_sd_session_start_us) / 1e6;
    int n = snprintf(line, sizeof line, " %.6f 1  %0*lX%s   Rx   d %u",
                     ts, msg.extd ? 8 : 3, (unsigned long)msg.identifier,
                     msg.extd ? "x" : "", (unsigned)msg.data_length_code);
    for (int i = 0; i < msg.data_length_code && n < (int)sizeof line - 4; i++)
        n += snprintf(line + n, sizeof line - n, " %02X", msg.data[i]);
    if (n < (int)sizeof line - 1) line[n++] = '\n';
    if (xStreamBufferSpacesAvailable(g_sd_raw.sb) >= (size_t)n)
        xStreamBufferSend(g_sd_raw.sb, line, n, 0);
    else
        g_sd_raw.dropped++;
}

// Push one JSON snapshot (built by the caller) as a line to the json ring. Only a
// single producer touches the ring, so the space check can't race with a shrink
// (the consumer only frees space), making the two sends effectively atomic.
static inline void sdEnqueueJson(const String& js) {
    size_t len = js.length();
    if (xStreamBufferSpacesAvailable(g_sd_json.sb) >= len + 1) {
        xStreamBufferSend(g_sd_json.sb, js.c_str(), len, 0);
        xStreamBufferSend(g_sd_json.sb, "\n", 1, 0);
    } else {
        g_sd_json.dropped++;
    }
}

// ── Writer side (runs on the dedicated task, core 0) ──

// Vector ASCII header. The ESP32 has no RTC/NTP, so the wall-clock date is
// nominal; what matters to the readers is "base hex" + "timestamps absolute"
// and monotonic per-line timestamps (which are session-relative seconds).
static void sdWriteAscHeader(File& f) {
    f.print("date Thu Jan 1 00:00:00.000 1970\n");
    f.print("base hex  timestamps absolute\n");
    f.print("internal events logged\n");
    f.print("   0.000000 Start of measurement\n");
}

// Basename of a directory entry — e.name() may or may not carry a leading path
// depending on core version.
static const char* sdBasename(const char* name) {
    const char* slash = strrchr(name, '/');
    return slash ? slash + 1 : name;
}

// Parse a session directory basename: exactly 's' followed by digits. Anything
// else — a user's "stuff/" folder, "saved" — is rejected; strtoul alone would
// parse those to 0 and permanently jam the reaper on a nonexistent /s00000.
static bool sdParseSession(const char* nm, uint32_t& n) {
    if (nm[0] != 's' || nm[1] < '0' || nm[1] > '9') return false;
    char* end = nullptr;
    n = strtoul(nm + 1, &end, 10);
    return *end == '\0';
}

// One walk of the root directory serving both consumers: the highest session
// index (next session number = highest + 1, monotonic per card, survives
// reaping and card swaps with no counter file) and the lowest index excluding
// the active session (the reaper's victim). 0 / UINT32_MAX when none exist.
static void sdScanSessions(uint32_t& lowest_other, uint32_t& highest) {
    lowest_other = UINT32_MAX;
    highest = 0;
    File root = SD.open("/");
    if (!root) return;
    for (File e = root.openNextFile(); e; e = root.openNextFile()) {
        uint32_t n;
        if (e.isDirectory() && sdParseSession(sdBasename(e.name()), n)) {
            if (n > highest) highest = n;
            if (n < lowest_other && n != g_sd.session) lowest_other = n;
        }
        e.close();
    }
    root.close();
}

// Open the stream's current part file (fresh — FILE_WRITE truncates) and reset
// its size accounting.
static bool sdOpenPart(SdStream& s) {
    char path[32];
    snprintf(path, sizeof path, s.path_fmt, (unsigned long)g_sd.session, s.part);
    s.file = SD.open(path, FILE_WRITE);
    if (!s.file) return false;
    if (s.asc_header) sdWriteAscHeader(s.file);
    s.part_bytes = 0;
    return true;
}

static bool sdStartSession() {
    uint32_t lowest, highest;
    sdScanSessions(lowest, highest);
    g_sd.session   = highest + 1;
    g_sd_raw.part  = 0;
    g_sd_json.part = 0;
    char dir[16];
    snprintf(dir, sizeof dir, "/s%05lu", (unsigned long)g_sd.session);
    if (!SD.mkdir(dir)) return false;
    g_sd_session_start_us = esp_timer_get_time();
    if (!sdOpenPart(g_sd_raw)) return false;
    if (!sdOpenPart(g_sd_json)) { g_sd_raw.file.close(); return false; }
    return true;
}

// Delete a session directory. Sessions hold only flat files (can_PP.asc /
// data_PP.jsonl), so remove them one at a time — re-opening the dir each pass
// avoids invalidating the FatFS iterator by deleting during a walk.
static bool sdRemoveSessionDir(const char* path) {
    for (;;) {
        File d = SD.open(path);
        if (!d) return false;
        File e = d.openNextFile();
        if (!e) { d.close(); break; }
        char child[80];
        snprintf(child, sizeof child, "%s/%s", path, sdBasename(e.name()));
        e.close();
        d.close();
        if (!SD.remove(child)) return false;
    }
    return SD.rmdir(path);
}

// Reap the oldest session dir, never the active one. Returns false when there's
// nothing else to delete.
static bool sdReapOldest() {
    uint32_t oldest, highest;
    sdScanSessions(oldest, highest);
    if (oldest == UINT32_MAX) return false;
    char path[16];
    snprintf(path, sizeof path, "/s%05lu", (unsigned long)oldest);
    return sdRemoveSessionDir(path);
}

// Refresh free-space figure and reap oldest sessions until we're back above the
// threshold (or only the active session remains).
static void sdUpdateFree() {
    uint64_t total = SD.totalBytes();
    uint64_t used  = SD.usedBytes();
    uint64_t freeb = total > used ? total - used : 0;
    while (freeb < SD_MIN_FREE_BYTES) {
        if (!sdReapOldest()) break;
        used  = SD.usedBytes();
        freeb = total > used ? total - used : 0;
    }
    g_sd.free_mb = (uint32_t)(freeb >> 20);
}

// Total bytes written this session, both streams. Writer task (core 0) owns it.
static uint64_t sd_total_bytes = 0;

// Give up logging for the rest of this boot. The CAN tap keeps running. `op` and
// the running byte count are kept for the /json status so a field failure is
// diagnosable. Self-deletes the calling task — only callable from the writer
// task; boot-time failures latch their error in sdInit() instead.
static void sdFail(const char* op) {
    g_sd.fail_op = op;
    g_sd.fail_kb = (uint32_t)(sd_total_bytes >> 10);
    g_sd_active  = false;
    g_sd.state   = "error";
    if (g_sd_raw.file)  g_sd_raw.file.close();
    if (g_sd_json.file) g_sd_json.file.close();
    xSemaphoreGive(g_sd_mutex);   // held by the writer's burst — free it or HTTP deadlocks
    vTaskDelete(nullptr);
}

// An SD call failed mid-session. Observed failure mode on the RejsaCAN: the card
// stops answering CMD13 (SEND_STATUS) under sustained write load — on two
// different cards and both Arduino cores — and a reboot always brings it back.
// So instead of latching "error" on the first bad write, remount and resume the
// same session with fresh part files (bounded attempts, linear backoff). The
// failed chunk is still in the caller's buffer and is re-written after we
// return; the rings keep absorbing frames while we're remounting, so a
// sub-second recovery usually loses nothing.
//
// A stream whose file was open when the failure hit moves to a fresh part — its
// on-card tail ends at the last fsync, possibly mid-line, so appending is
// unsafe. A stream whose file was already closed (a part-roll open that failed)
// retries the same, never-created part number instead of skipping one.
static void sdRecoverOrFail(const char* op) {
    if (g_sd_raw.file)  g_sd_raw.part++;
    if (g_sd_json.file) g_sd_json.part++;
    for (int attempt = 1; attempt <= SD_RECOVER_ATTEMPTS; attempt++) {
        g_sd_raw.file.close();
        g_sd_json.file.close();
        SD.end();
        vTaskDelay(pdMS_TO_TICKS(SD_RECOVER_DELAY_MS * attempt));
        if (!SD.begin(SD_CS_PIN)) continue;
        if (!sdOpenPart(g_sd_raw))  continue;
        if (!sdOpenPart(g_sd_json)) continue;
        g_sd.recoveries++;
        return;
    }
    sdFail(op);   // never returns
}

// Drain one stream: pull a chunk from its ring and write it. Batch: drain only
// once a full chunk is waiting, or when the flush deadline arrives (so trickle
// data still lands within ~1 s). Short write bursts leave the card idle between
// them, instead of the continuous 512-byte-at-a-time program cycle that
// provoked mid-write card lockups. If a write fails, the chunk is still in buf
// after the remount, so the retry re-lands it — no data lost.
static bool sdDrainStream(SdStream& s, bool flush_due, uint8_t* buf) {
    if (!flush_due && xStreamBufferBytesAvailable(s.sb) < SD_WRITE_CHUNK_BYTES)
        return false;
    size_t n = xStreamBufferReceive(s.sb, buf, SD_WRITE_CHUNK_BYTES, 0);
    if (!n) return false;
    if (s.file.write(buf, n) != n) {
        sdRecoverOrFail(s.write_op);
        if (s.file.write(buf, n) != n) sdFail(s.write_op);
    }
    s.part_bytes += n;
    sd_total_bytes += n;
    return true;
}

// Roll to the next part once the current one reaches SD_MAX_PART_BYTES, so no
// single file grows unbounded.
static void sdRollIfDue(SdStream& s) {
    if (s.part_bytes < SD_MAX_PART_BYTES) return;
    s.file.flush();
    s.file.close();
    s.part++;
    if (!sdOpenPart(s)) sdRecoverOrFail(s.open_op);
}

static void sdWriterTask(void*) {
    static uint8_t buf[SD_WRITE_CHUNK_BYTES];   // static — doesn't fit the task stack
    uint32_t last_flush = millis();
    uint32_t last_free  = millis() - SD_FREE_CHECK_MS;   // check on first idle

    for (;;) {
        bool flush_due = (millis() - last_flush >= SD_FLUSH_MS);

        xSemaphoreTake(g_sd_mutex, portMAX_DELAY);
        bool did = sdDrainStream(g_sd_raw, flush_due, buf);
        did     |= sdDrainStream(g_sd_json, flush_due, buf);
        g_sd.kb_written = (uint32_t)(sd_total_bytes >> 10);

        sdRollIfDue(g_sd_raw);
        sdRollIfDue(g_sd_json);

        if (flush_due) {
            last_flush = millis();
            g_sd_raw.file.flush();
            g_sd_json.file.flush();
        }
        if (millis() - last_free >= SD_FREE_CHECK_MS) {
            last_free = millis();
            sdUpdateFree();
        }
        xSemaphoreGive(g_sd_mutex);

        if (!did) vTaskDelay(pdMS_TO_TICKS(20));   // both rings empty — yield
    }
}

static bool sdInitRing(SdStream& s) {
    s.storage = (uint8_t*) heap_caps_malloc(s.ring_bytes + 1, MALLOC_CAP_SPIRAM);
    if (!s.storage) return false;
    s.sb = xStreamBufferCreateStatic(s.ring_bytes, 1, s.storage, &s.sb_struct);
    return s.sb != nullptr;
}

// Probe the card once and, only on success, start logging + spawn the writer.
// Called from setup(); leaves the feature dormant (g_sd_active stays false) on
// any failure so the firmware behaves exactly as a card-less tap. Boot failures
// latch state="error" with a fail_op right here — sdFail() self-deletes the
// calling task, so it must never run on the setup/loop task.
static void sdInit() {
    g_sd_mutex = xSemaphoreCreateMutex();
    if (!g_sd_mutex || !sdInitRing(g_sd_raw) || !sdInitRing(g_sd_json)) {
        g_sd.fail_op = "ring_alloc";
        g_sd.state   = "error";
        return;
    }

    SPI.begin(SD_SCK_PIN, SD_MISO_PIN, SD_MOSI_PIN, SD_CS_PIN);
    if (!SD.begin(SD_CS_PIN)) { g_sd.state = "no_card"; return; }
    if (!sdStartSession()) {
        g_sd.fail_op = "start_session";
        g_sd.state   = "error";
        SD.end();
        return;
    }

    g_sd.state = "logging";
    xTaskCreatePinnedToCore(sdWriterTask, "sdwriter", 8192, nullptr, 1, nullptr, 0);
    g_sd_active = true;   // arm the producer last, once everything is ready
}

#endif  // HAS_SD

// ── LED status indicator ──────────────────────────────────────────────────────
// Single-LED boards (Adafruit Feather S3 NeoPixel) colour-code state on one
// pixel:
//   Red blink     — CAN driver failed to initialize
//   Amber blink   — No Wi-Fi up at all (AP failed and STA not connected)
//   Dim white     — Alive, no CAN frames received recently
//   Green blink   — CAN frames arriving (toggles on bus activity)
//
// Dual-LED boards (RejsaCAN-ESP32-S3) split the state across two pins:
//   Yellow fast blink — CAN driver failed to initialize
//   Yellow slow blink — No Wi-Fi
//   Yellow off        — Network OK
//   Blue blink        — CAN frames arriving
//   Blue off          — No frames recently (green power LED still shows alive)
//
// On boards without any user LED (LilyGo T-2CAN), the calls are no-ops.

#define LED_BLINK_MS         50
#define LED_ACTIVE_MS        200
#define WARN_BLINK_FAST_MS   100   // CAN init failed
#define WARN_BLINK_SLOW_MS   500   // no WiFi

static uint32_t g_led_last_toggle = 0;
static bool     g_led_on = false;

static inline void ledInit() {
#if LED_IS_NEOPIXEL
    pinMode(LED_POWER_PIN, OUTPUT);
    digitalWrite(LED_POWER_PIN, HIGH);   // enable NeoPixel power rail
#elif LED_IS_DUAL_GPIO
    pinMode(WARN_LED_PIN, OUTPUT);
    pinMode(ACTIVITY_LED_PIN, OUTPUT);
    digitalWrite(WARN_LED_PIN, LOW);
    digitalWrite(ACTIVITY_LED_PIN, LOW);
#endif
}

static inline void ledWrite(uint8_t r, uint8_t g, uint8_t b) {
#if LED_IS_NEOPIXEL
  #if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
    rgbLedWrite(LED_PIN, r, g, b);
  #else
    neopixelWrite(LED_PIN, r, g, b);
  #endif
#else
    // Dual-GPIO and no-LED boards: updateLed() drives the pins directly (dual)
    // or there's nothing to drive (none).
    (void)r; (void)g; (void)b;
#endif
}

void updateLed() {
#if LED_IS_DUAL_GPIO
    uint32_t now = millis();

    // Yellow warning channel. Off when healthy; periods chosen so the two
    // failure modes are distinguishable at a glance.
    uint32_t warn_period_ms = 0;
    if (!g_can_initialized) {
        warn_period_ms = WARN_BLINK_FAST_MS;
    } else if (!g_ap_running && WiFi.status() != WL_CONNECTED) {
        warn_period_ms = WARN_BLINK_SLOW_MS;
    }
    static uint32_t warn_last_toggle = 0;
    static bool     warn_on = false;
    if (warn_period_ms > 0) {
        if (now - warn_last_toggle >= warn_period_ms) {
            warn_last_toggle = now;
            warn_on = !warn_on;
        }
        digitalWrite(WARN_LED_PIN, warn_on ? HIGH : LOW);
    } else {
        digitalWrite(WARN_LED_PIN, LOW);
        warn_on = false;
    }

    // Blue activity channel. The board's green power LED already signals
    // "alive", so blue stays off until bus traffic appears.
    bool active = (g_frames_rx > 0) && (now - g_last_frame_ms < LED_ACTIVE_MS);
    static uint32_t act_last_toggle = 0;
    static bool     act_on = false;
    if (active) {
        if (now - act_last_toggle >= LED_BLINK_MS) {
            act_last_toggle = now;
            act_on = !act_on;
        }
        digitalWrite(ACTIVITY_LED_PIN, act_on ? HIGH : LOW);
    } else {
        digitalWrite(ACTIVITY_LED_PIN, LOW);
        act_on = false;
    }
    return;
#else
    uint32_t now = millis();
    bool toggle = (now - g_led_last_toggle) >= LED_BLINK_MS;

    if (!g_can_initialized) {
        if (toggle) { g_led_last_toggle = now; g_led_on = !g_led_on; }
        ledWrite(g_led_on ? 32 : 0, 0, 0);
        return;
    }
    if (!g_ap_running && WiFi.status() != WL_CONNECTED) {
        if (toggle) { g_led_last_toggle = now; g_led_on = !g_led_on; }
        ledWrite(g_led_on ? 24 : 0, g_led_on ? 12 : 0, 0);
        return;
    }
    bool active = (g_frames_rx > 0) && (now - g_last_frame_ms < LED_ACTIVE_MS);
    if (!active) {
        ledWrite(4, 4, 4);
        g_led_on = false;
        g_led_last_toggle = now;
        return;
    }
    if (toggle) {
        g_led_last_toggle = now;
        g_led_on = !g_led_on;
    }
    ledWrite(0, g_led_on ? 32 : 0, 0);
#endif
}

#if defined(HAS_VIN_SENSE)
// Sample the 12 V input rail every ~500 ms. analogReadMilliVolts() applies
// the ADC's eFuse calibration; we oversample 16× to knock the LSB noise down.
// The divider scales the pin reading back up to the actual VCC.
static void updateVinSense() {
    static uint32_t last_ms = 0;
    uint32_t now = millis();
    if (now - last_ms < 500) return;
    last_ms = now;
    uint32_t sum_mv = 0;
    for (int i = 0; i < 16; i++) sum_mv += analogReadMilliVolts(VIN_SENSE_PIN);
    float pin_mv = sum_mv / 16.0f;
    g_vin_v = (pin_mv * VIN_DIVIDER_NUM / VIN_DIVIDER_DEN) / 1000.0f;
}
#endif

// ── CAN bus-off recovery ──────────────────────────────────────────────────────
// Sustained bus errors (shorted wiring, bitrate mismatch while transmitting)
// drive the TWAI controller bus-off, where it stays until software intervenes
// — without this, capture is dead until a power cycle. Poll the driver state
// and walk it back: BUS_OFF → initiate recovery (the controller waits out
// 128×11 recessive bits), which completes into STOPPED → start again.
//
// The MCP2515 (can1) deliberately has no equivalent: bus-off is driven by the
// *transmit* error counter, so a listen-only controller can never reach it,
// and in a -DCAN_ALLOW_TX build the MCP2515 — unlike the TWAI peripheral —
// recovers from bus-off automatically in hardware. Its error state is
// observable via tec/rec/eflg in the /json mcp2515 block.

static void canRecoveryTick() {
    static uint32_t last_ms = 0;
    uint32_t now = millis();
    if (!g_can_initialized || now - last_ms < 1000) return;
    last_ms = now;
    twai_status_info_t si;
    if (twai_get_status_info(&si) != ESP_OK) return;
    if (si.state == TWAI_STATE_BUS_OFF) {
        if (twai_initiate_recovery() == ESP_OK) g_can_recoveries++;
    } else if (si.state == TWAI_STATE_STOPPED) {
        twai_start();
    }
}

#if defined(AUTOSHUTDOWN)
// ── Energy-saving deep sleep ──────────────────────────────────────────────────
// A parked tractor (or bench setup) produces no CAN traffic, so there's
// nothing to log. Deep sleep + wake-on-CAN needs nothing board-specific: any
// board's CAN_RX_PIN can be an ext0 wake source (0–21 are RTC-capable on the
// S3, and every board map here picks one in that range).
//
// Wake source is CAN traffic only (ext0 on CAN_RX: the first dominant bit
// pulls the line low). There is deliberately no voltage-rise wake: on the
// RejsaCAN, the tractor's measured 12 V rail is ~12.4 V key-off / ~12.7 V
// key-on (issue #41 comments), both below the comparator's assumed on/off
// thresholds (~13.0/13.7 V for the genuine R4=91k board), so SENSE_V_DIG would
// not reliably fire even at key-on, let alone during a charge session. CAN
// wake covers both cases: ECUs broadcast at key-on, and the charger/BMS
// broadcast during charging.
//
// TENTATIVE: assumes gpio_hold_en() holds a non-RTC digital output through
// deep sleep on the S3, and that ext0 wakes with the transceiver left in
// normal mode (RS held LOW, not standby) — neither is bench-verified yet.
#define CAN_QUIET_SLEEP_MS   (10UL * 60UL * 1000UL)   // 10 min of silence

static void checkCanQuietSleep() {
    if (millis() - g_last_frame_ms < CAN_QUIET_SLEEP_MS) return;

    WiFi.mode(WIFI_OFF);
    BLEDevice::deinit(true);
    twai_stop();
    twai_driver_uninstall();

#if defined(BOARD_REJSACAN)
    // RejsaCAN-only: these pins are firmware-driven and float during deep
    // sleep unless held, which would cause real problems on this board —
    // CAN_RS floating drops the transceiver into slope-control mode (mangles
    // frames, including the one meant to wake us) and FORCE_ON floating risks
    // a spurious auto-shutdown edge. The other boards don't define these pins
    // (transceiver mode is fixed in hardware, no auto-shutdown circuit), so
    // there's nothing to hold there.
    digitalWrite(WARN_LED_PIN, LOW);
    digitalWrite(ACTIVITY_LED_PIN, LOW);
    gpio_hold_en(CAN_RS_PIN);
    gpio_hold_en(FORCE_ON_PIN);
    gpio_hold_en(WARN_LED_PIN);
    gpio_hold_en(ACTIVITY_LED_PIN);
    gpio_deep_sleep_hold_en();
#endif

    esp_sleep_enable_ext0_wakeup(CAN_RX_PIN, 0 /*wake when pulled LOW*/);
    esp_deep_sleep_start();
    // Never returns — waking from deep sleep is a full reboot back into setup().
}
#endif

// ── Helpers ───────────────────────────────────────────────────────────────────

static inline uint16_t be16(uint8_t hi, uint8_t lo) {
    return ((uint16_t)hi << 8) | lo;
}

static inline uint16_t le16(uint8_t lo, uint8_t hi) {
    return ((uint16_t)hi << 8) | lo;
}

static bool allZero(const uint8_t* d) {
    for (int i = 0; i < 8; i++) if (d[i]) return false;
    return true;
}

// ── CAN decoder ───────────────────────────────────────────────────────────────

void decodeCAN(uint32_t can_id, const uint8_t* raw, uint8_t len) {
    uint8_t d[8] = {};
    memcpy(d, raw, len < 8 ? len : 8);

    uint8_t  src = can_id & 0xFF;
    uint8_t  pf  = (can_id >> 16) & 0xFF;
    uint8_t  ps  = (can_id >> 8)  & 0xFF;
    uint16_t pgn = ((uint16_t)pf << 8) | (pf >= 0xF0 ? ps : 0);

    // Count any frame from a known source as decoded, before the per-PGN
    // decoders below — they often early-return on all-zero idle frames.
    if (src == SRC_BMS || src == SRC_BMS_CHGR_IF || src == SRC_CHARGER ||
        src == SRC_VEHICLE || src == SRC_MOTOR || src == SRC_DASH)
        g_frames_decoded++;

    if (src == SRC_BMS) {

        if (pgn >= PGN_CELL_FIRST && pgn <= PGN_CELL_LAST) {
            if (allZero(d)) return;
            int base = (pgn - PGN_CELL_FIRST) * 4;
            for (int slot = 0; slot < 4; slot++) {
                int idx = base + slot;
                if (idx >= NUM_CELLS) break;
                uint16_t mv = be16(d[2*slot], d[2*slot+1]);
                if (mv && mv != 0xFFFF)
                    g_cell_v[idx] = mv / 1000.0f;
            }

        } else if (pgn >= PGN_TEMP_FIRST && pgn <= PGN_TEMP_LAST) {
            if (allZero(d)) return;
            int base = (pgn - PGN_TEMP_FIRST) * 8;
            for (int slot = 0; slot < 8; slot++) {
                int idx = base + slot;
                if (idx >= NUM_TEMPS) break;
                if (d[slot] && d[slot] != 0xFF)
                    g_temp_c[idx] = (float)(d[slot] - TEMP_OFFSET_C);
            }

        } else if (pgn == PGN_F100) {
            if (allZero(d)) return;
            uint16_t raw_cur = be16(d[2], d[3]);
            // Sign convention: positive = charging, negative = discharging.
            float amps  = -((int32_t)raw_cur - PACK_CURRENT_BIAS_RAW) * PACK_CURRENT_LSB_A;
            // Pack voltage is one BE-16 field at 0.1 V/bit. The 60-84 V
            // operating window keeps d[0] at 0x02/0x03, which can make it
            // masquerade as a range-selector byte plus 8-bit voltage.
            float volts = be16(d[0], d[1]) * PACK_VOLTAGE_LSB_V;
            g_pack.voltage_v   = volts;
            g_pack.current_raw = raw_cur;
            g_pack.current_a   = amps;
            g_pack.power_w     = volts * amps;
            g_pack.soc_raw     = d[4];
            g_pack.soc_pct     = d[4] * 0.4f - 0.8f;
            if (isnan(g_session_soc_start_pct))
                g_session_soc_start_pct = g_pack.soc_pct;

            // Integrate power into session energy counters
            uint32_t now = millis();
            if (g_session_last_ms != 0) {
                uint32_t dt_ms = now - g_session_last_ms;
                float dt_s = dt_ms / 1000.0f;
                if (dt_s > 0 && dt_s < 5.0f) {   // sanity: skip bus-silent gaps
                    g_session_active_ms += dt_ms;
                    // power_w: positive = charging, negative = discharging
                    float wh = g_pack.power_w * dt_s / 3600.0f;
                    if (wh > 0) g_session_wh_charged += wh;
                    else        g_session_wh_drawn   += -wh;
                }
            }
            g_session_last_ms = now;

        } else if (pgn == PGN_F102) {
            if (allZero(d)) return;
            uint16_t max_mv = be16(d[0], d[1]);
            uint16_t min_mv = be16(d[2], d[3]);
            if (!max_mv || !min_mv) return;
            g_pack.cell_max_mv             = max_mv;
            g_pack.cell_min_mv             = min_mv;
            g_pack.cell_spread_mv          = max_mv - min_mv;
            g_pack.cell_max_n              = d[4];
            g_pack.cell_min_n              = d[5];
            g_pack.cell_spread_mv_reported = d[7];
            g_pack.v_estimate = 20.0f * (max_mv + min_mv) / 2.0f / 1000.0f;

        } else if (pgn == PGN_F104) {
            if (allZero(d) || d[0] == 0xFF || d[1] == 0xFF) return;
            g_pack.temp_max_c   = (int8_t)(d[0] - TEMP_OFFSET_C);
            g_pack.temp_min_c   = (int8_t)(d[1] - TEMP_OFFSET_C);
            g_pack.temp_max_n   = d[2];
            g_pack.temp_min_n   = d[3];
            g_pack.temp_spread_c = (int8_t)d[4];

        } else if (pgn == PGN_F106) {
            if (allZero(d)) return;
            g_bms_state.byte0 = d[0];
            g_bms_state.byte1 = d[1];
            g_bms_state.valid = true;

        } else if (pgn == PGN_F107) {
            if (allZero(d)) return;
            g_bms_limit.last_seen_ms = millis();
            g_bms_limit.discharge_a = be16(d[0], d[1]) * LIMIT_CURRENT_LSB_A;
            g_bms_limit.charge_a    = be16(d[2], d[3]) * LIMIT_CURRENT_LSB_A;
            g_bms_limit.charge_power_extra_w = be16(d[6], d[7]) * LIMIT_POWER_EXTRA_LSB_W;
            g_bms_limit.mode  = d[4];
            g_bms_limit.byte5 = d[5];
            g_bms_limit.valid = true;

        } else if (pgn == PGN_F108) {
            // F108 is a continuous bitmap broadcast: an all-zero frame means
            // "no faults" and must clear previously latched codes, so there
            // is deliberately no allZero() skip here.
            memcpy(g_bms_faults.bytes, d, 8);
            g_bms_faults.active_codes_mask = 0;
            for (int bi = 0; bi < 7; bi++) {
                for (int bit = 0; bit < 8; bit++) {
                    uint8_t code = FAULT_BYTES_0_TO_6[bi][bit];
                    if (code && ((d[bi] >> bit) & 1))
                        g_bms_faults.active_codes_mask |= (1ULL << (code - 100));
                }
            }
            for (int bit = 0; bit < 8; bit++) {
                uint8_t code = FAULT_BYTE7[bit];
                if (code && ((d[7] >> bit) & 1))
                    g_bms_faults.active_codes_mask |= (1ULL << (code - 100));
            }
            g_bms_faults.any_fault = g_bms_faults.active_codes_mask != 0;
        }

    } else if (src == SRC_VEHICLE && pgn == PGN_F100) {
        g_vc_state = d[0];

    } else if (src == SRC_MOTOR && pgn == PGN_FF21) {
        uint16_t rpm_raw  = le16(d[2], d[3]);
        int      rpm_mag  = (int)rpm_raw - RPM_BIAS;
        // Raw can jitter a bit below the zero-RPM bias at standstill; without
        // the clamp the uint16_t cast below would wrap a small negative to
        // ~65000 RPM (and a garbage speed).
        if (rpm_mag < 0) rpm_mag = 0;
        uint8_t  fnr      = d[7] & 0x0F;
        int8_t   dir      = (fnr == 0x4) ? 1 : (fnr == 0x8) ? -1 : 0;
        g_motor.rpm_magnitude      = (uint16_t)rpm_mag;
        g_motor.rpm_signed         = dir * rpm_mag;
        g_motor.direction          = dir;
        g_motor.range         = ((d[7] >> 4) & 0x0F) + 1;
        g_motor.torque_raw       = le16(d[0], d[1]);
        if (d[4]) g_motor.controller_temp_c = (int8_t)(d[4] - TEMP_OFFSET_C);
        if (d[5]) g_motor.motor_temp_c      = (int8_t)(d[5] - TEMP_OFFSET_C);
        g_motor.valid = true;
        g_motor.last_seen_ms = millis();

    } else if (src == SRC_DASH && pgn == PGN_FF21) {
        g_dash_alive = d[0];

    } else if (src == SRC_MOTOR && pgn == PGN_FECA) {
        uint32_t spn = d[2] | ((uint32_t)d[3] << 8)
                       | (((uint32_t)(d[4] >> 5) & 0x07) << 16);
        uint8_t fmi = d[4] & 0x1F;
        bool active = (spn || fmi);
        if (!d[0] && !d[1] && !active) {
            // Healthy idle — clear any previously latched DTC so the JSON
            // reports "presently inactive", not "last fault ever observed".
            g_dm1 = Dm1State();
            return;
        }
        g_dm1.lamp_byte0 = d[0];
        g_dm1.lamp_byte1 = d[1];
        g_dm1.dtc_spn    = spn;
        g_dm1.dtc_fmi    = fmi;
        g_dm1.dtc_cm     = (d[5] >> 7) & 0x01;
        g_dm1.dtc_oc     = d[5] & 0x7F;
        g_dm1.valid      = true;

    } else if (src == SRC_CHARGER && pgn == PGN_FF50) {
        if (allZero(d)) return;
        // Standard Elcon/TC charger status frame: BE-16 output voltage and
        // BE-16 output current (0.1/bit each), then a fault-flag byte
        // (CHGR_FLAG_*; 0x00 = actively delivering). Voltage reads the
        // charger's own output terminals — it equals pack V only while
        // delivering, so consumers gate any pack-V use on flags == 0.
        g_charger.v_raw     = be16(d[0], d[1]);
        g_charger.i_raw     = be16(d[2], d[3]);
        g_charger.flags     = d[4];
        g_charger.voltage_v = g_charger.v_raw * PACK_VOLTAGE_LSB_V;
        g_charger.current_a = g_charger.i_raw * CHARGER_I_LSB_A;
        g_charger.valid = true;

    } else if (src == SRC_BMS_CHGR_IF && pgn == PGN_PROP_0600) {
        uint16_t v_set = be16(d[0], d[1]);
        uint16_t i_set = be16(d[2], d[3]);
        g_chgr_cmd.enable = d[4];
        if (v_set || i_set) {
            g_chgr_cmd.voltage_v = v_set * 0.1f;
            g_chgr_cmd.current_a = i_set * 0.1f;
            g_chgr_cmd.v_raw     = v_set;
            g_chgr_cmd.i_raw     = i_set;
        } else {
            // Idle frame: V=0, I=0, enable=1 — clear the setpoint so the JSON
            // emits enable only, instead of freezing on the last active values.
            g_chgr_cmd.voltage_v = NAN;
            g_chgr_cmd.current_a = NAN;
            g_chgr_cmd.v_raw     = 0;
            g_chgr_cmd.i_raw     = 0;
        }
        g_chgr_cmd.valid = true;
    }
}

// ── JSON builder ──────────────────────────────────────────────────────────────

static void addFloat(JsonObject& obj, const char* key, float v, int decimals = 2) {
    if (!isnan(v)) {
        float factor = 1.0f;
        for (int i = 0; i < decimals; i++) factor *= 10.0f;
        obj[key] = roundf(v * factor) / factor;
    }
}

// `minimal` strips fields the HTML dashboard doesn't render — used to cut BLE
// payload size. The full set is still served at /json. Note the per-cell
// voltage/temperature arrays are NOT stripped: the cell-detail view renders
// them over BLE too.
String buildJson(bool pretty = true, bool minimal = false) {
    JsonDocument doc;

    doc["uptime"] = millis() / 1000.0;
#ifdef GIT_SHA
    doc["version"] = GIT_SHA;
#endif

    // CAN bus health
    auto can = doc["can"].to<JsonObject>();
    if (!g_can_initialized) {
        can["state"] = "not_initialized";
    } else {
        twai_status_info_t si;
        if (twai_get_status_info(&si) == ESP_OK) {
            switch (si.state) {
                case TWAI_STATE_STOPPED:    can["state"] = "stopped";    break;
                case TWAI_STATE_RUNNING:    can["state"] = "running";    break;
                case TWAI_STATE_BUS_OFF:    can["state"] = "bus_off";    break;
                case TWAI_STATE_RECOVERING: can["state"] = "recovering"; break;
                default:                    can["state"] = "unknown";    break;
            }
            if (!minimal) {
                can["tec"]        = si.tx_error_counter;
                can["rec"]        = si.rx_error_counter;
                can["rx_missed"]  = si.rx_missed_count;
                can["bus_errors"] = si.bus_error_count;
                can["bus_recoveries"] = g_can_recoveries;
            }
        }
    }
    can["frames_rx"]      = g_frames_rx;
    if (!minimal) can["mode"] = CAN_TX_ENABLED ? "normal" : "listen_only";
    if (!minimal) can["frames_decoded"] = g_frames_decoded;
    if (!minimal && g_socketcand_tx_dropped)
        can["socketcand_dropped"] = g_socketcand_tx_dropped;
    if (g_frames_rx > 0)
        can["last_frame_age_s"] = (millis() - g_last_frame_ms) / 1000.0;
#if defined(HAS_MCP2515)
    auto mcp = can["mcp2515"].to<JsonObject>();
    mcp["initialized"] = g_mcp_initialized;
    mcp["init_err"]    = g_mcp_init_err;
    mcp["frames_rx"]   = g_mcp_frames_rx;
    if (!minimal && g_mcp_initialized) {
        // Error-state visibility for can1, matching can0's tec/rec above.
        // Each accessor is one short SPI register read. eflg is the raw EFLG
        // register: bit 5 TXBO (bus-off), bits 4/3 TXEP/RXEP (error-passive),
        // bits 7/6 RX1OVR/RX0OVR (RX buffer overflow).
        mcp["tec"]  = g_mcp.transmitErrorCounter();
        mcp["rec"]  = g_mcp.receiveErrorCounter();
        mcp["eflg"] = g_mcp.errorFlagRegister();
    }
#endif

    // Pack
    auto pack = doc["pack"].to<JsonObject>();
    addFloat(pack, "voltage_v",    g_pack.voltage_v, 2);
    addFloat(pack, "current_a",    g_pack.current_a, 1);
    if (!minimal && g_pack.current_raw >= 0) pack["current_raw"] = g_pack.current_raw;
    addFloat(pack, "power_w",      g_pack.power_w,   1);
    if (!minimal && g_pack.soc_raw) pack["soc_raw"] = g_pack.soc_raw;
    addFloat(pack, "soc_pct",      g_pack.soc_pct,   1);
    if (!minimal) addFloat(pack, "v_estimate", g_pack.v_estimate, 3);
    auto cells_obj = pack["cells"].to<JsonObject>();
    if (g_pack.cell_max_mv >= 0)   cells_obj["max_mv"]    = g_pack.cell_max_mv;
    if (g_pack.cell_min_mv >= 0)   cells_obj["min_mv"]    = g_pack.cell_min_mv;
    if (g_pack.cell_spread_mv >= 0)cells_obj["spread_mv"] = g_pack.cell_spread_mv;
    if (g_pack.cell_max_n)         cells_obj["max_n"]     = g_pack.cell_max_n;
    if (g_pack.cell_min_n)         cells_obj["min_n"]     = g_pack.cell_min_n;
    if (!minimal && g_pack.cell_spread_mv_reported >= 0)
        cells_obj["spread_mv_reported"] = g_pack.cell_spread_mv_reported;
    auto temp = cells_obj["temp_summary"].to<JsonObject>();
    if (g_pack.temp_max_c != INT8_MIN) temp["max_c"]    = g_pack.temp_max_c;
    if (g_pack.temp_min_c != INT8_MIN) temp["min_c"]    = g_pack.temp_min_c;
    if (!minimal) {
        if (g_pack.temp_max_n)             temp["max_n"]    = g_pack.temp_max_n;
        if (g_pack.temp_min_n)             temp["min_n"]    = g_pack.temp_min_n;
        if (g_pack.temp_spread_c >= 0)     temp["spread_c"] = g_pack.temp_spread_c;
    }

    // Session energy summary.
    // Convention: positive = into pack (net charge), negative = out (net draw).
    auto sess = doc["session"].to<JsonObject>();
    sess["wh_drawn"]   = roundf(g_session_wh_drawn   * 10.0f) / 10.0f;
    sess["wh_charged"] = roundf(g_session_wh_charged * 10.0f) / 10.0f;
    sess["wh_net"]     = roundf((g_session_wh_charged - g_session_wh_drawn) * 10.0f) / 10.0f;
    sess["wh_capacity"] = PACK_CAPACITY_WH;
    if (!isnan(g_session_soc_start_pct))
        sess["soc_start_pct"] = roundf(g_session_soc_start_pct * 10.0f) / 10.0f;

    // Session-average net power. Positive = net charging, negative = net drawing.
    // Uses *active* time (sum of valid dt's), so bus-silent gaps don't dilute it.
    float avg_power_w = NAN;
    float active_hours = g_session_active_ms / 3600000.0f;
    if (active_hours > 0.01f) {                            // ≥ ~36 s of data
        avg_power_w = (g_session_wh_charged - g_session_wh_drawn) / active_hours;
        if (!minimal) {
            sess["avg_power_w"] = roundf(avg_power_w * 10.0f) / 10.0f;
            sess["active_s"]    = g_session_active_ms / 1000;
        }
    }

    if (!isnan(g_pack.soc_pct)) {
        float remaining = g_pack.soc_pct * PACK_CAPACITY_WH / 100.0f;
        sess["wh_remaining"] = roundf(remaining * 10.0f) / 10.0f;
        // ETAs use session-average power so they don't jump with instantaneous load
        if (!isnan(avg_power_w)) {
            if (avg_power_w < -50.0f) {
                // Net drawing — extrapolate to empty.
                sess["eta_to_zero_s"] = (uint32_t)(remaining / -avg_power_w * 3600.0f);
            } else if (avg_power_w > 50.0f) {
                // Net charging — extrapolate to full.
                float headroom = PACK_CAPACITY_WH - remaining;
                if (headroom > 0)
                    sess["eta_to_full_s"] = (uint32_t)(headroom / avg_power_w * 3600.0f);
            }
        }
    }

    // Per-cell arrays (20 voltages, 7 temperatures; null if not yet received).
    // Emitted in the minimal/BLE payload too so the cell-detail view works over
    // Bluetooth as well as WiFi; costs ~200 B (a couple more BLE chunks).
    {
        auto cells = cells_obj["voltages"].to<JsonArray>();
        for (int i = 0; i < NUM_CELLS; i++) {
            if (!isnan(g_cell_v[i]))
                cells.add(roundf(g_cell_v[i] * 1000.0f) / 1000.0f);
            else
                cells.add(nullptr);
        }
        auto temps = cells_obj["temp_readings"].to<JsonArray>();
        for (int i = 0; i < NUM_TEMPS; i++) {
            if (!isnan(g_temp_c[i]))
                temps.add((int)g_temp_c[i]);
            else
                temps.add(nullptr);
        }
    }

    // BMS state
    if (g_bms_state.valid) {
        // Raw F106 bitmaps; the dashboard decodes the individual bits. Sent
        // in the minimal (BLE) payload too — the dashboard's BMS State panel
        // reads only these two bytes.
        auto st = doc["bms"]["state"].to<JsonObject>();
        st["byte0"] = g_bms_state.byte0;
        st["byte1"] = g_bms_state.byte1;
    }

    // BMS current limits
    if (g_bms_limit.valid) {
        auto lim = doc["bms"]["limit"].to<JsonObject>();
        addFloat(lim, "discharge_a", g_bms_limit.discharge_a, 2);
        addFloat(lim, "charge_a",    g_bms_limit.charge_a,    2);
        addFloat(lim, "charge_power_extra_w", g_bms_limit.charge_power_extra_w, 0);
        if (!minimal) {
            lim["mode"]  = g_bms_limit.mode;
            lim["byte5"] = g_bms_limit.byte5;
        }
    }

    // Combined fault codes (BMS + Motor Controller)
    auto faults = doc["faults"].to<JsonObject>();
    auto bms_codes = faults["bms"].to<JsonArray>();
    if (g_bms_faults.any_fault) {
        for (int code = 100; code <= 145; code++) {
            if (g_bms_faults.active_codes_mask & (1ULL << (code - 100)))
                bms_codes.add(code);
        }
    }
    auto mc_codes = faults["mc"].to<JsonArray>();
    if (g_dm1.valid && g_dm1.dtc_spn != 0)
        mc_codes.add(g_dm1.dtc_spn);

    // Motor — always emit so motor.alive is the canonical tractor-on signal.
    // FF21CA broadcasts at ~85 Hz; the MC stops within ~1s of key-off. The
    // 500 ms window catches brief key cycles without false-positiving on a
    // single dropped frame. last_seen_ms==0 means FF21CA has never been
    // heard in this boot — explicitly false rather than relying on a
    // millis()-rollover-from-zero accident.
    {
        auto mot = doc["motor"].to<JsonObject>();
        mot["alive"] = g_motor.last_seen_ms != 0
                       && (millis() - g_motor.last_seen_ms) < 500;
        if (g_motor.valid) {
            if (!minimal) mot["rpm_signed"] = g_motor.rpm_signed;
            mot["rpm_magnitude"] = g_motor.rpm_magnitude;
            mot["direction"]     = g_motor.direction;
            mot["range"]    = g_motor.range;
            mot["torque_raw"] = g_motor.torque_raw;
            if (g_motor.controller_temp_c != INT8_MIN)
                mot["controller_temp_c"] = g_motor.controller_temp_c;
            if (g_motor.motor_temp_c != INT8_MIN)
                mot["motor_temp_c"] = g_motor.motor_temp_c;
        }
    }

    {
        uint32_t nowms = millis();
        bool mc_alive = g_motor.last_seen_ms != 0
                        && (nowms - g_motor.last_seen_ms) < 500;
        doc["tractor"] = mc_alive ? "on" : "off";
    }

#if defined(HAS_VIN_SENSE)
    if (!isnan(g_vin_v)) {
        doc["vin_v"] = roundf(g_vin_v * 10.0f) / 10.0f;
    }
#endif

#if defined(AUTOSHUTDOWN)
    switch (g_wake_cause) {
        case ESP_SLEEP_WAKEUP_EXT0:  doc["wake_cause"] = "can";      break;
        case ESP_SLEEP_WAKEUP_UNDEFINED: doc["wake_cause"] = "power_on"; break;
        default:                     doc["wake_cause"] = "other";    break;
    }
#endif

    // Charger
    if (g_charger.valid) {
        auto chg = doc["charger"].to<JsonObject>();
        chg["flags"] = g_charger.flags;
        if (!minimal) {
            chg["v_raw"]  = g_charger.v_raw;
            chg["i_raw"]  = g_charger.i_raw;
        }
        addFloat(chg, "voltage_v", g_charger.voltage_v, 2);
        addFloat(chg, "current_a", g_charger.current_a, 1);
    }

    // BMS→charger command
    if (g_chgr_cmd.valid) {
        auto cmd = doc["chgr_cmd"].to<JsonObject>();
        if (!minimal) cmd["enable"] = g_chgr_cmd.enable;
        addFloat(cmd, "voltage_v", g_chgr_cmd.voltage_v, 1);
        addFloat(cmd, "current_a", g_chgr_cmd.current_a, 1);
        if (!minimal && g_chgr_cmd.v_raw) cmd["v_raw"] = g_chgr_cmd.v_raw;
        if (!minimal && g_chgr_cmd.i_raw) cmd["i_raw"] = g_chgr_cmd.i_raw;
    }

    if (!minimal) {
        // Vehicle controller
        if (g_vc_state != 0xFF)
            doc["vc"]["state"] = g_vc_state;

        // Dashboard
        if (g_dash_alive != 0xFF)
            doc["dash"]["alive"] = g_dash_alive;

        // DM1 (raw FMI/OC/CM and lamp bytes — fault code is also in faults.mc)
        if (g_dm1.valid) {
            auto dm1 = doc["dm1"].to<JsonObject>();
            dm1["lamp_byte0"] = g_dm1.lamp_byte0;
            dm1["lamp_byte1"] = g_dm1.lamp_byte1;
            dm1["dtc_spn"]    = g_dm1.dtc_spn;
            dm1["dtc_fmi"]    = g_dm1.dtc_fmi;
            dm1["dtc_cm"]     = g_dm1.dtc_cm;
            dm1["dtc_oc"]     = g_dm1.dtc_oc;
        }
    }

#if defined(HAS_SD)
    // SD session-logging status. Always emitted (small, and the phone app mirrors
    // the dashboard) so both the web tile and BLE clients can show it.
    {
        auto sd = doc["sd"].to<JsonObject>();
        sd["state"] = g_sd.state;
        if (g_sd_active) {
            sd["session"]    = g_sd.session;
            sd["raw_part"]   = g_sd_raw.part;
            sd["json_part"]  = g_sd_json.part;
            sd["kb_written"] = g_sd.kb_written;
            sd["free_mb"]    = g_sd.free_mb;
        }
        if (g_sd_raw.dropped)  sd["raw_dropped"]  = g_sd_raw.dropped;
        if (g_sd_json.dropped) sd["json_dropped"] = g_sd_json.dropped;
        if (g_sd.recoveries)   sd["recoveries"]   = g_sd.recoveries;
        if (g_sd.fail_op[0]) {
            sd["fail_op"] = g_sd.fail_op;
            sd["fail_kb"] = g_sd.fail_kb;
        }
    }
#endif

    String out;
    if (pretty) serializeJsonPretty(doc, out);
    else        serializeJson(doc, out);
    return out;
}

// ── HTTP handlers ─────────────────────────────────────────────────────────────

void handleJson() {
    server.send(200, "application/json", buildJson());
}

// Names for the STA disconnect reasons that come up when a credentials build
// goes wrong: auth_fail / handshake_timeout usually mean a wrong password,
// no_ap_found a wrong SSID or out-of-range network. Rare codes stay numeric.
static String staDisconnectReasonName(uint8_t reason) {
    switch (reason) {
        case WIFI_REASON_UNSPECIFIED:                return "unspecified";
        case WIFI_REASON_AUTH_EXPIRE:                return "auth_expire";
        case WIFI_REASON_AUTH_LEAVE:                 return "auth_leave";
        case WIFI_REASON_ASSOC_EXPIRE:               return "assoc_expire";
        case WIFI_REASON_ASSOC_TOOMANY:              return "assoc_toomany";
        case WIFI_REASON_NOT_AUTHED:                 return "not_authed";
        case WIFI_REASON_NOT_ASSOCED:                return "not_assoced";
        case WIFI_REASON_ASSOC_LEAVE:                return "assoc_leave";
        case WIFI_REASON_MIC_FAILURE:                return "mic_failure";
        case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:     return "4way_handshake_timeout";
        case WIFI_REASON_GROUP_KEY_UPDATE_TIMEOUT:   return "group_key_update_timeout";
        case WIFI_REASON_802_1X_AUTH_FAILED:         return "802_1x_auth_failed";
        case WIFI_REASON_BEACON_TIMEOUT:             return "beacon_timeout";
        case WIFI_REASON_NO_AP_FOUND:                return "no_ap_found";
        case WIFI_REASON_AUTH_FAIL:                  return "auth_fail";
        case WIFI_REASON_ASSOC_FAIL:                 return "assoc_fail";
        case WIFI_REASON_HANDSHAKE_TIMEOUT:          return "handshake_timeout";
        case WIFI_REASON_CONNECTION_FAIL:            return "connection_fail";
        case WIFI_REASON_AP_TSF_RESET:               return "ap_tsf_reset";
        case WIFI_REASON_ROAMING:                    return "roaming";
        default:                                     return String(reason);
    }
}

// Build + WiFi diagnostics, deliberately separate from the /json telemetry:
// this reports what was baked into the binary and how the home-network join
// is going. The soft-AP is always up, so /config stays reachable at
// 192.168.4.1 even when the STA join failed — one request distinguishes
// "wrong password" from "wrong SSID" from "built with empty credentials".
void handleConfig() {
    JsonDocument doc;

    doc["uptime"] = millis() / 1000.0;
#ifdef GIT_SHA
    doc["version"] = GIT_SHA;
#endif
#if defined(BOARD_ADAFRUIT_FEATHER_S3)
    doc["board"] = "adafruit_feather_s3";
#elif defined(BOARD_LILYGO_T2CAN)
    doc["board"] = "lilygo_t2can";
#elif defined(BOARD_REJSACAN)
    doc["board"] = "rejsacan";
#endif
    doc["mdns"] = MDNS_NAME;

    auto features = doc["features"].to<JsonObject>();
    features["can_tx"]    = (bool)CAN_TX_ENABLED;
#if defined(HAS_MCP2515)
    features["mcp2515"]   = true;
#else
    features["mcp2515"]   = false;
#endif
#if defined(HAS_VIN_SENSE)
    features["vin_sense"] = true;
#else
    features["vin_sense"] = false;
#endif

    auto wifi = doc["wifi"].to<JsonObject>();

    auto sta = wifi["sta"].to<JsonObject>();
    const bool join_sta = (sizeof(WIFI_SSID) > 1);
    sta["ssid"]     = WIFI_SSID;                    // exactly what was compiled in
    sta["pass_set"] = (sizeof(WIFI_PASS) > 1);      // presence only, never the password
    sta["enabled"]  = join_sta;
    const bool sta_connected = join_sta && WiFi.status() == WL_CONNECTED;
    sta["status"] = !join_sta ? "disabled"
                  : sta_connected ? "connected" : "connecting";
    if (sta_connected) {
        sta["ip"]   = WiFi.localIP().toString();
        sta["rssi"] = WiFi.RSSI();
    }
    sta["disconnects"] = (uint32_t)g_sta_disconnects;
    if (g_sta_disconnects > 0)
        sta["last_disconnect_reason"] =
            staDisconnectReasonName(g_sta_last_disconnect_reason);

    auto ap = wifi["ap"].to<JsonObject>();
    ap["ssid"]    = AP_SSID;
    ap["running"] = g_ap_running;
    if (g_ap_running) {
        ap["ip"]      = WiFi.softAPIP().toString();
        ap["clients"] = WiFi.softAPgetStationNum();
    }

    String out;
    serializeJsonPretty(doc, out);
    server.send(200, "application/json", out);
}

void handleRoot() {
    // HTML lives in src/dashboard.html; embedded via board_build.embed_txtfiles.
    // Length excludes the trailing null byte that embed_txtfiles appends.
    size_t len = dashboard_html_end - dashboard_html_start - 1;
    server.send_P(200, "text/html", (PGM_P)dashboard_html_start, len);
}

void handleNotFound() {
    // The soft-AP's wildcard DNS lands every hostname here, including phone
    // connectivity probes (generate_204 / hotspot-detect.html). Answering
    // those with a redirect — not a 404 — makes the OS classify the AP as a
    // captive portal and auto-open the dashboard when someone joins.
    // localIP() is the board's address on whichever interface (AP or STA)
    // the request arrived on, so bench-network clients redirect sensibly too.
    server.sendHeader("Location",
                      "http://" + server.client().localIP().toString() + "/");
    server.send(302, "text/plain", "");
}

// ── SLCAN ─────────────────────────────────────────────────────────────────────
// Presents the CAN bus as an SLCAN device over USB CDC serial.
// python-can: interface='slcan', channel='/dev/cu.usbmodem...'
// Receive always works; transmit (t/T commands) is gated on -DCAN_ALLOW_TX and
// routes to can0 (the native TWAI controller) only — SLCAN has no channel
// concept, so it never reaches the MCP2515 (can1). A listen-only build answers
// every t/T with BELL.

static char   slcan_buf[32];
static uint8_t slcan_len = 0;
static bool   slcan_open = false;

void slcanSendFrame(const twai_message_t& msg) {
    if (!slcan_open) return;
    char line[32];
    // 'T' + 8 hex ID digits for 29-bit frames, 't' + 3 for 11-bit.
    int n = msg.extd
        ? snprintf(line, sizeof(line), "T%08" PRIX32 "%u",
                   msg.identifier, msg.data_length_code)
        : snprintf(line, sizeof(line), "t%03" PRIX32 "%u",
                   msg.identifier, msg.data_length_code);
    // Bound n so the two hex digits and the trailing '\r' always fit: if n
    // ever passed sizeof(line), the sizeof(line) - n below would underflow
    // and line[n++] would land out of bounds. Unreachable while loop()
    // clamps DLC to 8, but the formatter shouldn't lean on that.
    for (int i = 0; i < msg.data_length_code && n < (int)sizeof(line) - 3; i++)
        n += snprintf(line + n, sizeof(line) - n, "%02X", msg.data[i]);
    line[n++] = '\r';
    Serial.write((uint8_t*)line, n);
}

// Single hex digit -> 0..15, or -1 if not a hex character.
static int hexNibble(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

#if CAN_TX_ENABLED
// Inject a frame onto can0 (native TWAI). Shared by the SLCAN t/T handler and
// the socketcand channel-0 `< send >` so the two TX paths can't drift. Returns
// true if the driver queued the frame. Only compiled when TX is enabled — in a
// listen-only build there is no code that can reach the bus.
static bool canTransmit0(uint32_t id, bool extd, uint8_t dlc, const uint8_t* data) {
    twai_message_t tx = {};
    tx.identifier       = id;
    tx.extd             = extd ? 1 : 0;
    tx.data_length_code = dlc;
    memcpy(tx.data, data, dlc);
    return twai_transmit(&tx, pdMS_TO_TICKS(10)) == ESP_OK;
}
#endif

void slcanHandleCommand(const char* cmd) {
    switch (cmd[0]) {
        case 'O': slcan_open = true;  Serial.write('\r'); break;
        case 'C': slcan_open = false; Serial.write('\r'); break;
        case 'S': Serial.write('\r'); break;   // speed — fixed at 250k
        case 'V': Serial.print("V1013\r"); break;
        case 'N': Serial.print("NA000\r"); break;
        case 'F': Serial.print("F00\r");   break;
        // Transmit: t<iii><l><dd..> (11-bit) / T<iiiiiiii><l><dd..> (29-bit).
        // Reply CR on success, BELL (\a) on any parse/TX error — the Lawicel
        // convention python-can tolerates. Only functional when built with
        // -DCAN_ALLOW_TX; a listen-only build rejects every frame with BELL.
        case 't':
        case 'T': {
#if !CAN_TX_ENABLED
            Serial.write('\a');   // listen-only build: injection disabled
#else
            if (!slcan_open) { Serial.write('\a'); break; }
            bool extd = (cmd[0] == 'T');
            int idlen = extd ? 8 : 3;
            size_t len = strlen(cmd);
            if (len < (size_t)(1 + idlen + 1)) { Serial.write('\a'); break; }
            bool bad = false;
            uint32_t id = 0;
            for (int i = 0; i < idlen && !bad; i++) {
                int nib = hexNibble(cmd[1 + i]);
                if (nib < 0) bad = true; else id = (id << 4) | (uint32_t)nib;
            }
            int dlc = hexNibble(cmd[1 + idlen]);
            if (bad || dlc < 0 || dlc > 8) { Serial.write('\a'); break; }
            if (len < (size_t)(1 + idlen + 1 + dlc * 2)) { Serial.write('\a'); break; }
            id &= extd ? 0x1FFFFFFFUL : 0x7FFUL;
            uint8_t data[8] = {0};
            const char* dp = cmd + 1 + idlen + 1;
            for (int i = 0; i < dlc && !bad; i++) {
                int hi = hexNibble(dp[i * 2]), lo = hexNibble(dp[i * 2 + 1]);
                if (hi < 0 || lo < 0) bad = true; else data[i] = (uint8_t)((hi << 4) | lo);
            }
            if (bad) { Serial.write('\a'); break; }
            Serial.write(canTransmit0(id, extd, (uint8_t)dlc, data) ? '\r' : '\a');
#endif
            break;
        }
        default:  Serial.write('\r'); break;
    }
}

void slcanPoll() {
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\r' || c == '\n') {
            if (slcan_len > 0) {
                slcan_buf[slcan_len] = '\0';
                slcanHandleCommand(slcan_buf);
                slcan_len = 0;
            }
        } else if (slcan_len < sizeof(slcan_buf) - 1) {
            slcan_buf[slcan_len++] = c;
        }
    }
}

// ── socketcand ────────────────────────────────────────────────────────────────
// Streams raw CAN frames over WiFi using the socketcand ASCII protocol.
// python-can: interface='socketcand', host='tractor.local', port=28600,
//             channel='can0'
//
// One slot per channel — a client picks its bus with `< open canN >`. On the
// T-2CAN both buses are streamed (can0 = TWAI, can1 = MCP2515); on the
// Feather only can0 is available. Two clients can be connected at once on the
// T-2CAN, one per channel. A new connection that arrives while every slot is
// taken is rejected (the existing clients are kept).

#define SOCKETCAND_PORT 28600
// A client that connects but never completes the open/rawmode handshake would
// pin its slot forever; reclaim the slot after this long.
#define SOCKETCAND_HANDSHAKE_MS 10000

#if defined(HAS_MCP2515)
  #define SOCKETCAND_NUM_CHANNELS 2
#else
  #define SOCKETCAND_NUM_CHANNELS 1
#endif

enum SocketcandState {
    SC_DISCONNECTED,
    SC_WAITING_OPEN,
    SC_WAITING_RAWMODE,
    SC_RAWMODE,
};

struct SocketcandSlot {
    WiFiClient      client;
    SocketcandState state   = SC_DISCONNECTED;
    char            buf[64];
    uint8_t         len     = 0;
    int8_t          channel = -1;   // set when `< open canN >` is parsed
    uint32_t        opened_ms = 0;  // millis() when the TCP connection arrived
};

static WiFiServer     socketcand_server(SOCKETCAND_PORT);
static SocketcandSlot socketcand_slots[SOCKETCAND_NUM_CHANNELS];

static void socketcandCloseSlot(SocketcandSlot& slot) {
    slot.client.stop();
    slot.state   = SC_DISCONNECTED;
    slot.len     = 0;
    slot.channel = -1;
}

// True if the client's socket can take a write right now. WiFiClient::write
// has no non-blocking form — on a stalled client (phone out of range, no FIN)
// it select()s up to 1 s per call, which would stall CAN RX and every other
// service behind one dead peer. lwIP reports a socket writable only when at
// least TCP_SNDLOWAT (~half the send buffer) is free, so a whole frame line
// always fits once this returns true.
static bool socketcandWritable(WiFiClient& client) {
    int fd = client.fd();
    if (fd < 0) return false;
    fd_set wset;
    FD_ZERO(&wset);
    FD_SET(fd, &wset);
    struct timeval tv = {0, 0};
    return select(fd + 1, nullptr, &wset, nullptr, &tv) > 0;
}

void socketcandSendFrame(const twai_message_t& msg, int8_t channel) {
    uint32_t now_ms = millis();
    uint32_t secs   = now_ms / 1000;
    uint32_t usecs  = (now_ms % 1000) * 1000;
    // EFF flag (bit 31) marks the ID as 29-bit extended — python-can expects this.
    uint32_t id_wire = msg.identifier | (msg.extd ? 0x80000000UL : 0);
    char line[80];
    int n = snprintf(line, sizeof(line), "< frame %lX %lu.%06lu ",
                     (unsigned long)id_wire,
                     (unsigned long)secs, (unsigned long)usecs);
    for (int i = 0; i < msg.data_length_code && n < (int)sizeof(line) - 4; i++)
        n += snprintf(line + n, sizeof(line) - n, "%02X", msg.data[i]);
    n += snprintf(line + n, sizeof(line) - n, " >");
    for (auto& slot : socketcand_slots) {
        if (slot.state == SC_RAWMODE && slot.channel == channel) {
            if (socketcandWritable(slot.client))
                slot.client.write((const uint8_t*)line, n);
            else
                g_socketcand_tx_dropped++;
        }
    }
}

// Parse "< send <idhex> <dlc> <b0> <b1> ... >" (rawmode TX) and inject the frame
// onto this slot's CAN controller. We accept both extended-ID encodings seen in
// the wild: standard socketcand/python-can distinguishes 29-bit frames purely by
// id-field width (3 hex chars => 11-bit, otherwise extended, no flag bit), while
// some clients also OR the EFF flag into bit 31 — either marks the frame
// extended here. Byte tokens may be 1 or 2 hex digits; the dlc (0..8) reads the
// same in decimal or hex. Anything unparseable is dropped, like a real daemon.
static void socketcandHandleSend(SocketcandSlot& slot, const char* cmd) {
#if !CAN_TX_ENABLED
    (void)slot; (void)cmd;              // listen-only build: injection disabled
#else
    const char* p = cmd + 7;            // past "< send "
    char* end = nullptr;
    unsigned long id_val = strtoul(p, &end, 16);
    if (end == p) return;
    int id_len = (int)(end - p);
    p = end;
    long dlc = strtol(p, &end, 16);
    if (end == p || dlc < 0 || dlc > 8) return;
    p = end;
    uint8_t data[8] = {0};
    int got = 0;
    while (got < dlc) {
        long b = strtol(p, &end, 16);
        if (end == p) break;            // fewer tokens than dlc — leave zero-padded
        data[got++] = (uint8_t)(b & 0xFF);
        p = end;
    }
    bool extended = (id_val & 0x80000000UL) != 0 || id_len > 3;
    uint32_t id = extended ? (id_val & 0x1FFFFFFFUL) : (id_val & 0x7FFUL);

    if (slot.channel == 0) {
        canTransmit0(id, extended, (uint8_t)dlc, data);
    }
#if defined(HAS_MCP2515)
    else if (slot.channel == 1 && g_mcp_initialized) {
        CANMessage tx;
        tx.id  = id;
        tx.ext = extended;
        tx.rtr = false;
        tx.len = (uint8_t)dlc;
        memcpy(tx.data, data, (size_t)dlc);
        g_mcp.tryToSend(tx);
    }
#endif
#endif  // CAN_TX_ENABLED
}

static void socketcandHandleCommand(SocketcandSlot& slot, const char* cmd) {
    if (slot.state == SC_WAITING_OPEN && strncmp(cmd, "< open ", 7) == 0) {
        // Parse "< open canN >": after "< open " expect "canN" then space-or-'>'.
        const char* arg = cmd + 7;
        if (strncmp(arg, "can", 3) == 0 && arg[3] >= '0' && arg[3] <= '9'
                && (arg[4] == ' ' || arg[4] == '>')) {
            int ch = arg[3] - '0';
            if (ch < SOCKETCAND_NUM_CHANNELS) {
                slot.channel = (int8_t)ch;
                slot.state   = SC_WAITING_RAWMODE;
                slot.client.print("< ok >");
                return;
            }
        }
        slot.client.print("< error >");
    } else if (slot.state == SC_WAITING_RAWMODE && strcmp(cmd, "< rawmode >") == 0) {
        slot.client.print("< ok >");
        slot.state = SC_RAWMODE;
    } else if (slot.state == SC_RAWMODE && strncmp(cmd, "< send ", 7) == 0) {
        socketcandHandleSend(slot, cmd);
    }
    // bcmmode/isotpmode/echo/statistics are intentionally unsupported.
}

void socketcandPoll() {
    WiFiClient new_client = socketcand_server.accept();
    if (new_client) {
        int free_idx = -1;
        for (int i = 0; i < SOCKETCAND_NUM_CHANNELS; i++) {
            if (socketcand_slots[i].state == SC_DISCONNECTED) { free_idx = i; break; }
        }
        if (free_idx >= 0) {
            SocketcandSlot& slot = socketcand_slots[free_idx];
            slot.client  = new_client;
            slot.client.setNoDelay(true);
            slot.client.print("< hi >");
            slot.state   = SC_WAITING_OPEN;
            slot.len     = 0;
            slot.channel = -1;
            slot.opened_ms = millis();
        } else {
            new_client.stop();   // pool full — keep the existing clients
        }
    }
    for (auto& slot : socketcand_slots) {
        if (slot.state != SC_DISCONNECTED && !slot.client.connected()) {
            socketcandCloseSlot(slot);
        }
        if ((slot.state == SC_WAITING_OPEN || slot.state == SC_WAITING_RAWMODE)
                && millis() - slot.opened_ms > SOCKETCAND_HANDSHAKE_MS) {
            socketcandCloseSlot(slot);
        }
        while (slot.client && slot.client.available()) {
            char c = slot.client.read();
            if (c == '<') {
                slot.len = 0;
                slot.buf[slot.len++] = c;
            } else if (slot.len > 0 && slot.len < sizeof(slot.buf) - 1) {
                slot.buf[slot.len++] = c;
                if (c == '>') {
                    slot.buf[slot.len] = '\0';
                    socketcandHandleCommand(slot, slot.buf);
                    slot.len = 0;
                }
            }
        }
    }
}

// ── BLE (Nordic UART Service) ─────────────────────────────────────────────────
// Pushes a compact (minimal) JSON snapshot to a single BLE central — every
// BLE_PUSH_INTERVAL_MS while CAN frames are arriving, dropping to a slow
// BLE_HEARTBEAT_MS cadence on a quiet bus. The heartbeat exists because some
// fields change with time rather than with frames (uptime, last_frame_age_s,
// the tractor on/off flag): the client must still see those move after the
// bus goes silent. Framing on the wire is:
//
//     [u16 big-endian length] [length bytes of JSON]
//
// sent across N notifications of up to BLE_CHUNK_BYTES each. The Android
// client reassembles by counting bytes against the length prefix. Connection
// is unpaired — anyone in range with the NUS UUID can subscribe.
//
// MTU: we request 517 server-side; the actual MTU is whatever the client
// negotiates. BLE_CHUNK_BYTES is conservative so a default-MTU client (23)
// would still receive valid (if smaller) packets — but the Android app calls
// requestMtu(517) on connect.
//
// RX characteristic is exposed for future command/control (e.g. reset session
// counters); currently writes are accepted and ignored.

#define NUS_SVC_UUID  "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_TX_UUID   "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_RX_UUID   "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"

#define BLE_CHUNK_BYTES        180
#define BLE_PUSH_INTERVAL_MS   200    // push cadence while CAN frames are arriving
#define BLE_HEARTBEAT_MS       2000   // idle cadence — keeps time-derived fields fresh

#define BLE_INTER_CHUNK_DELAY  5      // min ms between chunk notifies (paced by bleTick)

static BLEServer*         g_ble_server = nullptr;
static BLECharacteristic* g_ble_tx     = nullptr;
static volatile bool      g_ble_connected  = false;
static volatile bool      g_ble_force_push = false;
static uint32_t           g_ble_last_push_ms = 0;
static uint32_t           g_ble_frames_at_push = 0;   // g_frames_rx at last push

class BleServerCb : public BLEServerCallbacks {
    // These callbacks run on the Bluedroid task, not loop() — they may only
    // touch the volatile flags, never heap-backed state the loop task owns.
    void onConnect(BLEServer*) override {
        g_ble_connected  = true;
        g_ble_force_push = true;   // full snapshot immediately on (re)connect
    }
    void onDisconnect(BLEServer* s) override {
        g_ble_connected = false;
        s->getAdvertising()->start();   // resume advertising for the next client
    }
};

void bleInit() {
    BLEDevice::init(MDNS_NAME);
    BLEDevice::setMTU(517);

    g_ble_server = BLEDevice::createServer();
    g_ble_server->setCallbacks(new BleServerCb());

    BLEService* svc = g_ble_server->createService(NUS_SVC_UUID);
    // NimBLE adds the 0x2902 CCC descriptor automatically for NOTIFY
    // characteristics; adding one manually is deprecated.
    g_ble_tx = svc->createCharacteristic(NUS_TX_UUID, BLECharacteristic::PROPERTY_NOTIFY);
    svc->createCharacteristic(NUS_RX_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
    svc->start();

    BLEAdvertising* adv = BLEDevice::getAdvertising();
    adv->addServiceUUID(NUS_SVC_UUID);
    adv->setScanResponse(true);
    BLEDevice::startAdvertising();
}

// In-flight framed payload, drained one chunk per bleTick() call so the loop
// never sleeps on BLE (the previous blocking send cost ~25 ms per push).
// 8 KB scratch is plenty: minimal JSON for this dashboard runs ~800 B
// (incl. the per-cell voltage/temperature arrays).
static uint8_t  g_ble_tx_buf[2 + 8192];
static size_t   g_ble_tx_len = 0;   // framed bytes queued; == off when idle
static size_t   g_ble_tx_off = 0;   // next byte to notify
static uint32_t g_ble_chunk_ms = 0; // millis() of the last chunk sent

static void bleQueueFramed(const String& payload) {
    size_t total = payload.length();
    if (total > 65535 || total + 2 > sizeof(g_ble_tx_buf)) return;
    g_ble_tx_buf[0] = (uint8_t)((total >> 8) & 0xFF);
    g_ble_tx_buf[1] = (uint8_t)(total & 0xFF);
    memcpy(g_ble_tx_buf + 2, payload.c_str(), total);
    g_ble_tx_len = 2 + total;
    g_ble_tx_off = 0;
}

void bleTick() {
    if (!g_ble_connected || !g_ble_tx) {
        g_ble_tx_len = g_ble_tx_off = 0;   // abort any in-flight frame
        return;
    }
    uint32_t now = millis();

    // Drain the in-flight frame first — one chunk per tick, keeping the
    // 5 ms inter-chunk spacing on the wire. No new payload is built until
    // the frame completes, so frames are never interleaved.
    if (g_ble_tx_off < g_ble_tx_len) {
        if (now - g_ble_chunk_ms < BLE_INTER_CHUNK_DELAY) return;
        size_t n = g_ble_tx_len - g_ble_tx_off;
        if (n > BLE_CHUNK_BYTES) n = BLE_CHUNK_BYTES;
        g_ble_tx->setValue(g_ble_tx_buf + g_ble_tx_off, n);
        g_ble_tx->notify();
        g_ble_tx_off += n;
        g_ble_chunk_ms = now;
        return;
    }

    // Adaptive cadence, keyed on frame arrival rather than a payload diff —
    // frames_rx is in the payload, so any received frame changes the JSON
    // and a diff could never suppress a push on an active bus anyway.
    bool bus_active = g_frames_rx != g_ble_frames_at_push;
    uint32_t interval = bus_active ? BLE_PUSH_INTERVAL_MS : BLE_HEARTBEAT_MS;
    if (!g_ble_force_push && now - g_ble_last_push_ms < interval) return;
    g_ble_force_push     = false;
    g_ble_last_push_ms   = now;
    g_ble_frames_at_push = g_frames_rx;
    bleQueueFramed(buildJson(false /*pretty*/, true /*minimal*/));
}

// ── Setup & loop ──────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);

    ledInit();
    ledWrite(4, 4, 4);   // dim white the moment firmware starts running

#if defined(AUTOSHUTDOWN)
    // A wake from deep sleep is a full reboot, so this runs on every wake too.
    // Capture the cause for /json before anything else touches the RTC state.
    g_wake_cause = esp_sleep_get_wakeup_cause();

#if defined(BOARD_REJSACAN)
    // Release the pins checkCanQuietSleep() held through the previous sleep
    // (no-op on a cold boot / power-on reset) before reconfiguring them below.
    gpio_hold_dis(CAN_RS_PIN);
    gpio_hold_dis(FORCE_ON_PIN);
    gpio_hold_dis(WARN_LED_PIN);
    gpio_hold_dis(ACTIVITY_LED_PIN);
    gpio_deep_sleep_hold_dis();
#endif
#endif

#if defined(BOARD_REJSACAN)
    // Drive the transceiver into high-speed normal mode (RS=LOW) and assert
    // the auto-shutdown override so the board stays on across vehicle/key
    // cycles. Both must happen before the TWAI driver starts — otherwise the
    // bus floats and the first frames after boot are lost.
    pinMode(CAN_RS_PIN, OUTPUT);
    digitalWrite(CAN_RS_PIN, LOW);
    pinMode(FORCE_ON_PIN, OUTPUT);
    digitalWrite(FORCE_ON_PIN, HIGH);
#endif

    for (int i = 0; i < NUM_CELLS; i++) g_cell_v[i] = NAN;
    for (int i = 0; i < NUM_TEMPS; i++) g_temp_c[i] = NAN;

    // CAN at 250 kbit/s (J1939 standard). Default rx_queue_len is 5, which
    // overflows whenever the loop stalls — serving the ~26 KB dashboard over
    // HTTP is the longest single handler. A saturated 250 kbit/s bus is
    // ~1800 frames/s, so 512 frames buffer ~280 ms of worst-case traffic
    // (longer at realistic bus load) for ~10 KB of DRAM.
    //
    // Bus mode is set by CAN_TX_ENABLED (see -DCAN_ALLOW_TX up top). Default is
    // LISTEN_ONLY: the controller never transmits — no ACKs, no error frames —
    // so it is electrically incapable of perturbing other nodes, and every TX
    // path (SLCAN injection, socketcand client->bus send) is dead at the
    // silicon level. -DCAN_ALLOW_TX selects NORMAL, which ACKs frames and arms
    // those TX paths.
#if CAN_TX_ENABLED
    const twai_mode_t kCanMode = TWAI_MODE_NORMAL;
#else
    const twai_mode_t kCanMode = TWAI_MODE_LISTEN_ONLY;
#endif
    twai_general_config_t can_cfg = TWAI_GENERAL_CONFIG_DEFAULT(
        CAN_TX_PIN, CAN_RX_PIN, kCanMode);
    can_cfg.rx_queue_len = 512;
    can_cfg.tx_queue_len = 32;
    twai_timing_config_t  tim_cfg = TWAI_TIMING_CONFIG_250KBITS();
    twai_filter_config_t  flt_cfg = TWAI_FILTER_CONFIG_ACCEPT_ALL();
    esp_err_t err = twai_driver_install(&can_cfg, &tim_cfg, &flt_cfg);
    if (err == ESP_OK) {
        err = twai_start();
        if (err == ESP_OK) g_can_initialized = true;
    }

    // Bring up the soft-AP first so the board is always reachable in the field
    // at 192.168.4.1 even if there's no home network in range. STA connect
    // happens in the background; we don't block boot waiting on it.
    //
    // Only enable the station when a home network is actually configured. The
    // AP and STA share one radio: if WIFI_SSID is empty the station would scan
    // every channel forever looking for a network that doesn't exist, which
    // makes the soft-AP beacon hop channels and drop out (it appears briefly
    // then vanishes and won't accept clients). Build with an empty WIFI_SSID
    // for a rock-solid AP-only setup; set it to join a bench network as before.
    // Record STA join outcomes for /config and the serial log. Registered
    // before begin() so even the very first failure is captured. The callback
    // runs on the WiFi event task; it only writes the volatile g_sta_* words.
    // Serial output is suppressed while an SLCAN session is open — the USB
    // serial port is the SLCAN channel, and a stray text line would corrupt
    // the frame stream a python-can client is parsing.
    WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
        if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
            g_sta_last_disconnect_reason = info.wifi_sta_disconnected.reason;
            g_sta_disconnects = g_sta_disconnects + 1;
            if (!slcan_open)
                Serial.printf("WiFi: STA disconnected, reason %u (%s)\r\n",
                              info.wifi_sta_disconnected.reason,
                              staDisconnectReasonName(
                                  info.wifi_sta_disconnected.reason).c_str());
        } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
            if (!slcan_open)
                Serial.printf("WiFi: STA connected, IP %s\r\n",
                              WiFi.localIP().toString().c_str());
        }
    });

    const bool join_sta = (sizeof(WIFI_SSID) > 1);
    WiFi.mode(join_sta ? WIFI_AP_STA : WIFI_AP);
    g_ap_running = WiFi.softAP(AP_SSID, AP_PASS);
    if (join_sta) WiFi.begin(WIFI_SSID, WIFI_PASS);

    if (join_sta)
        Serial.printf("WiFi: AP \"%s\" %s; STA joining \"%s\" (pass %u chars)\r\n",
                      AP_SSID, g_ap_running ? "up" : "FAILED",
                      WIFI_SSID, (unsigned)(sizeof(WIFI_PASS) - 1));
    else
        Serial.printf("WiFi: AP \"%s\" %s; STA disabled (no WIFI_SSID baked in)\r\n",
                      AP_SSID, g_ap_running ? "up" : "FAILED");

    // Wildcard DNS on the soft-AP: any hostname (tractor.local, tractor,
    // captive-portal probes, etc.) resolves to the board's AP IP. Needed
    // because phones generally don't do mDNS over an AP with no internet.
    if (g_ap_running) dns_server.start(53, "*", WiFi.softAPIP());

    MDNS.begin(MDNS_NAME);

    server.on("/",       handleRoot);
    server.on("/json",   handleJson);
    server.on("/config", handleConfig);
    server.onNotFound(handleNotFound);
    server.begin();
    MDNS.addService("http", "tcp", 80);

    socketcand_server.begin();
    socketcand_server.setNoDelay(true);
    MDNS.addService("socketcand", "tcp", SOCKETCAND_PORT);

#if defined(HAS_MCP2515)
    // Release the MCP2515 from hardware reset before any SPI traffic. RESET is
    // active-low; pulse it low then high and let the oscillator settle. Without
    // this the chip stays held in reset and begin() returns kNoMCP2515.
    pinMode(MCP2515_RST_PIN, OUTPUT);
    digitalWrite(MCP2515_RST_PIN, LOW);
    delay(10);
    digitalWrite(MCP2515_RST_PIN, HIGH);
    delay(10);

    // Do NOT pass the CS pin to SPI.begin — Arduino-ESP32 would attach it as
    // hardware-SS and fight the library's software-CS control. The ACAN2515
    // library drives CS itself (it calls pinMode(CS, OUTPUT) in begin()).
    SPI.begin(MCP2515_SCK_PIN, MCP2515_MISO_PIN, MCP2515_MOSI_PIN);

    // ACAN2515Settings(quartz_Hz, bitrate). begin() issues the MCP2515 reset,
    // configures bit timing, installs the ISR, and returns 0 on success (a
    // non-zero code is a bitmask of configuration errors — e.g. an impossible
    // bitrate for the given crystal). Default filters accept every frame, which
    // is what we want for a sniffer. Mode tracks CAN_TX_ENABLED so can1 gets the
    // same passive-tap guarantee as can0: ListenOnly by default, Normal only
    // under -DCAN_ALLOW_TX.
    ACAN2515Settings mcp_cfg(MCP2515_QUARTZ_HZ, 250UL * 1000UL);
#if !CAN_TX_ENABLED
    mcp_cfg.mRequestedMode = ACAN2515Settings::ListenOnlyMode;
#endif
    g_mcp_init_err    = g_mcp.begin(mcp_cfg, [] { g_mcp.isr(); });
    g_mcp_initialized = (g_mcp_init_err == 0);
#endif

#if defined(HAS_SD)
    // Probe the microSD once. On success this arms g_sd_active and spawns the
    // writer task; on no-card/failure it's a no-op and the firmware runs as a
    // pure tap. Must precede loop(), which is where the producer is gated.
    sdInit();
#endif

    bleInit();
}

void loop() {
    twai_message_t msg;
    while (twai_receive(&msg, 0) == ESP_OK) {
        // Classic CAN allows DLC 9–15 on the wire (still only 8 data bytes)
        // and the driver passes the raw value through (flagged
        // TWAI_MSG_FLAG_DLC_NON_COMP). Clamp before fan-out so the SLCAN /
        // socketcand formatters never index past msg.data[].
        if (msg.data_length_code > 8) msg.data_length_code = 8;
        g_frames_rx++;
        g_last_frame_ms = millis();
        // J1939 decode applies only to 29-bit frames, but the raw taps
        // forward 11-bit frames too — the TWAI channel may be tapped onto a
        // standard-ID bus (e.g. the BMS UDS port at 0x740/0x748).
        if (msg.extd)
            decodeCAN(msg.identifier, msg.data, msg.data_length_code);
        slcanSendFrame(msg);
        socketcandSendFrame(msg, /*channel=*/0);
#if defined(HAS_SD)
        if (g_sd_active) sdEnqueueRaw(msg);   // no-op enqueue when no card
#endif
    }
#if defined(HAS_MCP2515)
    if (g_mcp_initialized) {
        CANMessage frame;
        while (g_mcp.receive(frame)) {
            g_mcp_frames_rx++;
            // Counts as bus activity for the quiet-sleep timer too: the board
            // must not deep-sleep while CAN A still carries traffic (ext0 wake
            // only watches the TWAI RX pin, so a sleep here would be terminal
            // until CAN B traffic resumes).
            g_last_frame_ms = millis();
            twai_message_t fwd = {};
            fwd.identifier       = frame.id;
            fwd.extd             = frame.ext ? 1 : 0;
            fwd.data_length_code = frame.len <= 8 ? frame.len : 8;
            memcpy(fwd.data, frame.data, fwd.data_length_code);
            socketcandSendFrame(fwd, /*channel=*/1);
        }
    }
#endif
#if defined(HAS_SD)
    // Periodic decoded snapshot at SD_JSON_HZ (default 1 Hz), independent of any
    // client — buildJson() only runs on HTTP/BLE demand otherwise, so an
    // unattended session would log no JSON. Built here on the loop task, so the
    // decoded globals stay single-threaded; only the serialized line crosses to
    // the writer.
    if (g_sd_active) {
        static uint32_t last_json_ms = 0;
        uint32_t now = millis();
        if (now - last_json_ms >= 1000 / SD_JSON_HZ) {
            last_json_ms = now;
            sdEnqueueJson(buildJson(/*pretty=*/false, /*minimal=*/false));
        }
    }
#endif
    canRecoveryTick();
    slcanPoll();
    socketcandPoll();
    dns_server.processNextRequest();
    server.handleClient();
    bleTick();
    updateLed();
#if defined(HAS_VIN_SENSE)
    updateVinSense();
#endif
#if defined(AUTOSHUTDOWN)
    checkCanQuietSleep();
#endif
}
