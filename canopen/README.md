CANopen decode notes — Curtis 1238E motor controller (SA 0xCA)
================================================================

WHAT THIS IS
------------
The motor controller answers CANopen SDO on the main 250 kbit/s bus, in
parallel with its J1939 broadcasts. This file records what we can decode from
that CANopen dictionary using the captures taken 2026-09-24.

  CANopen node ID : 0x28 (40)   [distinct from the J1939 SA 0xCA]
  SDO request     : 0x628 (0x600 + node)   response: 0x5A8 (0x580 + node)
  Transfer        : expedited only (all objects <= 4 bytes; no segmented/strings)
  Vendor          : 0x1018:01 = 0x4349 = ASCII "CI" = Curtis Instruments (CONFIRMED)
  Model family    : Curtis E/SE series (CONFIRMED) — 0x3207 reads Motor RPM per
                    the E/SE map, while the F-series objects (0x3536 Motor Temp,
                    0x3538 Motor Torque, 0x34C1 Cap Volts) are ABSENT here.
  Access seen     : SDO read-only (upload). No writes attempted.

  NOT the Kelly (CONFIRMED). The Kelly KLS e-hydraulic pump controller is a
  separate box on a serial port, not on CAN. Node 0x28 tracks the TRACTION
  drivetrain: it follows the F/N/R lever, the R1/R2/R3 range switch, and ramps
  proportionally with the accelerator to the documented R3 cap of 2800 RPM.
  A hydraulic pump controller does none of those. Hydraulics appear here only
  indirectly, as pack current (see 0x33EF).

  DECODE REFERENCE: victronenergy/dbus-canopen-motordrive publishes named Curtis
  object indices. That map is how 0x3207 was identified and is the best lead for
  naming the remaining manufacturer objects.

CAPTURE PROVENANCE
------------------
  canopen.txt          static SDO dump, sub0 objects (1420).
                       *** RECONSTRUCTED from canopen_ts.csv's first idle sweep.
                       The original full dump (2257 rows incl. non-sub0 sub-
                       indices) was DESTROYED: these notes were first written to
                       "CANOPEN.txt", which on macOS's case-insensitive filesystem
                       IS "canopen.txt" — same inode. Non-sub0 entries (record /
                       PDO-mapping subindices such as 0x1018:01, 0x1600:01-08)
                       are NOT in the reconstruction; re-run canopen/canopen_dump.py
                       to restore them. Notes now live in CANOPEN-NOTES.md to
                       avoid the collision. ***
  canopen_session.csv  guided run: baseline / forward / reverse / neutral /
                       range_r1..r3 / throttle_quarter / throttle_full /
                       throttle_off / pto_on / pto_off   (~2 sweeps per stage)
  hyd_test.csv         hydraulics on vs off (~2 sweeps each) -- DELETED after
                       analysis; findings below are all that remains of it.
                       Re-capture with canopen/canopen_capture.py if needed.
  canopen_ts.csv       ~20 sweeps of idle/startup time-series

  Analysis: canopen/analyze_canopen_session.py (per-stage medians vs baseline,
  within-stage spread as the noise floor).

  *** CONFIDENCE CAVEAT: only ~1-2 sweeps per state. Big clean swings are
  trustworthy; small deltas (< a few x noise) are suggestive, not proven.
  Values are read as unsigned LE; those > 32767 are likely signed int16. ***

STATIC IDENTITY  (from canopen.txt)
-----------------------------------
  0x1009:00   "3079"  manufacturer hardware version (ASCII)          CONFIRMED
  0x100A:00   "3178"  manufacturer software version (ASCII)          CONFIRMED
  0x1017:00   25      producer heartbeat time (ms)                   CONFIRMED
  0x1018:01   0x4349  vendor ID "CI" (Curtis)                        CONFIRMED
  0x1018:04   15198   serial number                                  TENTATIVE
  Note: TPDO mappings (0x1A00..) are configured but no PDOs are transmitted
  (controller not in operational/SYNC mode) — telemetry is poll-only for now.

DECODED OBJECTS
---------------

0x33E6 — PACKED TRANSMISSION STATE (range + F/N/R)             CONFIRMED
  value = (range << 12) | (dir << 10)
    range: R1=0, R2=1, R3=2   (from range_r1/r2/r3 = 0x0000/0x1000/0x2000)
    dir  : N=0,  F=1,  R=2    (from N/F/R = +0x000/+0x400/+0x800)
  Cross-validated by two independent stages (direction sweep AND range sweep).
  This is the CANopen analog of the J1939 data[7] packed state (DOCUMENTATION.md),
  though the bit layout differs (dir here is 0/1/2, not the J1939 4/8).
    baseline/neutral 0x2000  forward 0x2400  reverse 0x2800
    R1 0x0000  R2 0x1000  R3 0x2000

0x3211 / 0x3521 (and near-twins 0x3216, 0x3402) — COMMANDED TORQUE  TENTATIVE
  Signed int16, full-scale ~±32767. Sign follows direction, magnitude follows
  throttle:  Forward no-throttle = +1, Reverse no-throttle = -1 (0xFFFF),
  1/4 throttle ~ +9100 (~28%), FULL throttle ~ +32670 (~100%), released -> 0/1.
  Reads as the controller's commanded torque/current demand (% of max effort).

NAMED OBJECTS — Victron curtis_e.c cross-reference
--------------------------------------------------
Source: victronenergy/dbus-canopen-motordrive, src/drivers/curtis_e.c
(readRoutine()). These are the Curtis E-series objects that driver polls, with
its scalings, checked against our staged captures:

  obj      name                   scaling        our reading           verdict
  0x3207   Motor RPM              sn16, 1:1      0/776/2792/0          CONFIRMED
  0x359E   Battery Current        sn16 * 0.1 A   0 / 2.75 A / 13.35 A  CONFIRMED
  0x320B   Motor Temperature      sn16 * 0.1 C   18.0 C                CONFIRMED
  0x322A   Controller Temperature sn16 * 0.1 C   19.0 -> 19.8 C        CONFIRMED
  0x306C   Swap_Two_Phases        bit 3 (&0b1000) 211 -> bit3=0 (off)  TENTATIVE
  0x324C   Capacitor_Voltage      u16 / 64  V    raw 4634 = 72.4 V     CONFIRMED

  0x324C: the Curtis E-manual names it Capacitor_Voltage (raw 0-12800 = 0-200 V,
  i.e. /64 — the same scale Victron uses). Raw 4634 (bytes 1a 12) / 64 = 72.4 V,
  matching the 72 V nominal pack (DOCUMENTATION.md: 73.0 V observed). An earlier
  revision of these notes claimed "463 V, needs /384": that was an analysis error
  (a x0.1 scale applied by mistake, then a raw back-derived from the wrong
  number). Victron's scaling was right all along.
  0x324D Keyswitch_Voltage uses a DIFFERENT scale: manual range 0-10500 =
  0.0-105.0 V, i.e. /100 (not /64). Raw 7233 / 100 = 72.3 V — matches the pack
  and the capacitor reading. CONFIRMED. (Lesson: each Curtis monitor variable
  carries its own scale; take it from the manual's raw-vs-display range pair.)

  Battery Current (0x359E) rising 0 -> 2.75 A -> 13.35 A with throttle and
  returning to 0 is consistent with spinning the motor unloaded in mechanical
  neutral. Controller temperature rising monotonically 19.0 -> 19.8 C across the
  session is independent corroboration that the scaling is right.

  SUPPLEMENTAL VALUE: Battery Current (0x359E) and Battery Voltage (0x324C) are
  NOT in the J1939 FF21CA broadcast — these are genuinely new quantities from the
  controller's own sense, and together give controller DC power draw. Motor RPM
  and both temperatures duplicate J1939 fields (useful as cross-validation).

0x3207 — MOTOR RPM                                              CONFIRMED
  0 at rest, 1/4 throttle 776, FULL throttle 2792, released -> 0.
  Triple-validated: (a) 0x3207 is the published Curtis E/SE-series "Motor RPM"
  index in victronenergy/dbus-canopen-motordrive; (b) the value is physically
  plausible; (c) the throttle test ran in R3, and 2792 lands on the documented
  R3 cap of 2800 RPM (DOCUMENTATION.md). Unlike J1939 FF21CA (magnitude only),
  this is the same quantity but poll-addressable.

0x3594 / 0x3595 / 0x35D1 / 0x35C8 — MOTOR RPM MIRRORS            TENTATIVE
  Same 0 -> ~776 -> ~2792 profile as 0x3207 (0x35C8 slightly higher: 952/2832,
  possibly commanded vs actual speed). Curtis mirrors values at several indices.
  CORRECTION: an earlier pass here mislabelled these as "motor current" — the
  RPM cross-reference shows they track speed, not current. We currently have
  NO confirmed motor-current object; 0x33EF (below) is the only current-like one.

0x35B7 (and 0x35AA/0x3591; also 0x3590/0x35FC/0x35FF) — INVERSE-WITH-LOAD  TENTATIVE
  Constant at rest (0x35B7 = 9192), DROPS under throttle (FULL ~ 2460),
  independent of direction/range/PTO. Candidate: capacitor/KSI voltage sag,
  or an available-current / max-available-torque headroom that shrinks as
  commanded torque rises.

0x33EF — LOAD / CURRENT (load-sensitive)                        TENTATIVE
  Responds to EVERY load path — throttle, hydraulics, PTO — so it is a
  pack/capacitor current-type reading, not a per-function flag.
  High byte tracks load: rest ~0x10, 1/4 throttle 0x28, FULL 0x8f,
  hydraulics-on 0xD6, PTO 0x36. Low byte pinned at 0x7d (possible second
  packed field). Values > 32767 suggest signed (bipolar charge/discharge).
  *** This is the object that proves hydraulics (a SEPARATE Kelly controller)
      is visible on the Curtis only INDIRECTLY, via shared-pack current. ***

0x3226 — SWITCHES (digital-input word, Sw_1..Sw_16)             CONFIRMED
  The manual lists every Sw_N monitor bit at 0x3226:00 ("Switches [Bit N-1]").
  Our values are bit patterns, consistent across the direction AND range stage
  sets (bit n <-> Sw_(n+1), pin numbers from the manual's monitor table):
      bit 6  Sw_7   pin 22   FORWARD  (set only in the forward stages)
      bit 7  Sw_8   pin 33   REVERSE  (set only in the reverse stage)
      bit 3  Sw_4   pin 10   range R1 (turtle)
      bit 4  Sw_5   pin 11   range R2
      bit 5  Sw_6   pin 12   range R3 (rabbit)
      bit 2  Sw_3   pin 9    always on  } enables / interlock-type inputs,
      bit 10 Sw_11  pin 4    always on  } (Driver 3 input) — not exercised
    e.g. neutral+R3 = 0x424 (bits 2,5,10); forward+R3 = 0x464 (+bit 6);
         reverse+R3 = 0x4A4 (+bit 7); neutral+R1 = 0x40C (bits 2,3,10).
  0x3224 = the low byte (Sw_1-8) and 0x3225 = the high byte (Sw_9-16) of the
  same word (0x3225 = 4 = bit 2 = Sw_11, matching bit 10 above).

  This is the hardware answer to "how does the F/N/R lever reach the bus": the
  lever and range switch close discrete switch inputs on the Curtis 35-pin
  connector (pins 22/33 and 10/11/12); the controller latches them here, and
  the OEM VCL program derives the packed 0x33E6 state and the J1939 data[7]
  nibbles from them.

HYDRAULICS ON/OFF  (hyd_test.csv, deleted)                     TENTATIVE
  16 objects moved beyond noise; dominant is 0x33EF (see above). The Kelly
  hydraulic pump has no direct Curtis object — its footprint is the extra
  pack current it draws, seen via 0x33EF and its 0x33Ex/0x33Fx neighbours.

OFFICIAL OBJECT NAMES — Curtis E-series manual (OS 30)
--------------------------------------------------------
Source: Curtis "1232E/34E/36E/38E & 1232SE/34SE/36SE Enhanced AC Controllers"
manual, OS 30 (p/n 53134). Every parameter and monitor variable is printed
with its CAN object index + subindex, e.g.
      Max Speed          100 - 8000 rpm
      Max_Speed_SpdM     100 - 8000
      0x3011 0x00
so the manual IS the object dictionary. Parsed into:
  canopen/curtis_e_od_map.csv 308 indices -> VCL name / display name / ranges
                             (display_name is best-effort; vcl_name is reliable)
  canopen_named.csv          all 1420 of our sub0 objects joined to that map:
                             284 carry an official Curtis name (20%).

Scaling rule: each monitor variable has its own scale, read off the manual's
raw-range vs display-range pair. Confirmed against our data:
  0x3207 Motor_RPM               1:1 rpm, signed (sign = direction)
  0x320B Motor_Temperature       /10 C     18.0 C
  0x322A Controller_Temperature  /10 C     19.0-19.8 C
  0x324C Capacitor_Voltage       /64 V     72.4 V   (0-12800 = 0-200 V)
  0x324D Keyswitch_Voltage       /100 V    72.3 V   (0-10500 = 0-105 V)
  0x3209 Current_RMS             /10 A     3.0 -> 24.0 -> 34.4 A with throttle
                                            (motor RMS current, unloaded spin)
  0x3208 Modulation_Depth        0-1182    9 -> 145 -> 693 with throttle
  0x3206 Frequency               ~2 x rpm  (electrical speed; 4-pole motor)
  0x3211 Mapped_Throttle         +-32767 = +-100 %   (was "torque cmd" here)
  0x3216 Throttle_Command        +-32767 = +-100 %
  0x3308 BDI_Percentage          0-100 %   37 %  (Curtis's own SOC estimate —
                                            compare with the BMS-shown SOC)
  0x3559 Max_Speed_Controller_Limit  8000  constant; the controller ceiling,
                                            NOT the range cap
  0x3581/0x35F3/0x3604/0x3605    cutbacks (motor-temp / ctrl-temp / over-V /
                                 under-V); 4096 = 100 % = no cutback
  0x3223 Main_State              5         main-contactor state machine
  0x3892 EMBrakeState            2
  0x3160 Master_Timer            the monotonic counter excluded below — now
                                 confirmed as the OS master timer
  0x35D1/0x35D2 MotorspeedA/B    encoder phase A/B speed (the "RPM mirrors")
  0x306C Swap_Two_Phases         (Victron reads bit 3 as swap-direction)
  0x3011 Max_Speed_SpdM = 2800   the stock Curtis Max Speed (speed mode)
  0x3021 Max_Speed_TrqM = 4000   Max Speed (torque mode)

Our earlier behavioral labels were right in kind but the official names are
sharper: the "commanded torque" cluster is Mapped_Throttle / Throttle_Command
(a throttle %, not torque), and the "current" cluster was really RPM/encoder
speed. Motor current proper is 0x3209 Current_RMS.

STOCK CONFIG SNAPSHOT (static params, named by the manual)      CONFIRMED
  0x3010 Control_Mode_Select      1      = Speed Mode (0 speed-express, 2 torque)
  0x3011 Max_Speed_SpdM           2800 rpm  (the R3 cap is the stock max; VCL
                                            lowers it for R1/R2 via 0x3103-08)
  0x3012 Kp_SpdM / 0x3015 Ki_SpdM 2458 / 300
  0x305B Drive_Current_Limit      16384  = 50 % of controller rated current
  0x305C Regen_Current_Limit      32767  = 100 %
  0x305D Brake_Current_Limit      32767  = 100 %
  0x3000 Throttle_Type            2      0x300A Brake_Type 2
  0x3001..0x3008 Forward/Reverse Deadband/Map/Max/Offset (throttle shaping)
  Full list with values: canopen_named.csv (filter vcl_name != "").

UNNAMED BLOCK = OEM (Solectrac) VCL VARIABLES                     TENTATIVE
  0x33E6 (packed range+dir), 0x33EF (load), 0x3521 (throttle copy), 0x35B7
  cluster, and the 0x3103-0x3108 speed table are NOT in the Curtis manual. The
  manual documents VCL RAM variables User1-User120 / AutoUser1-300 but does not
  publish their CAN indices, so these are almost certainly variables of the
  Solectrac VCL application program running on the controller. That fits: the
  per-range speed table and the tractor-specific packed state are OEM logic,
  not stock Curtis parameters. Decoding them stays behavioral.

WRITING PARAMETERS (from the manual's CAN section)
  SDO writes ARE supported and take effect in RAM immediately. They are
  VOLATILE across a key-cycle unless CAN_EE_Writes_Enabled (0x332F:00) is set
  non-zero first, which makes every subsequent write hit EEPROM at once. Manual
  CAUTION: do not leave 0x332F non-zero during normal operation (EEPROM wear).
  Practical upshot: with 0x332F = 0, an experimental SDO write is self-reverting
  on the next key cycle — the safer way to test anything. Nothing has been
  written to this controller so far.

SPEED-LIMIT PARAMETER BLOCK (0x3103-0x3108)                    TENTATIVE
------------------------------------------------------------------------
  0x3103 = 2800   0x3104 = 2240      R3 (rabbit)  forward / reverse
  0x3105 = 2000   0x3106 = 1600      R1 (turtle)  forward / reverse
  0x3107 = 2500   0x3108 = 2000      R2           forward / reverse

  All three documented RPM caps (2000/2500/2800, DOCUMENTATION.md) are present
  here as STATIC parameters, each paired with exactly 80% of itself. That 0.8
  ratio independently corroborates the "controller-side reverse-effort limiter"
  already documented for the J1939 decode.

  These do NOT change when the range switch moves: all three caps are stored
  simultaneously and the switch merely SELECTS among them. That is why a
  diff-across-range-stages analysis finds only 0x33E6 (the selector position)
  and never the limits themselves. Duplicates of the same constants also appear
  at 0x3011/0x3016/0x306E/0x3077/0x3089/0x309E.

  OPEN: 0x3011 and 0x306E read 2800 at baseline but 2240 during the range
  stages — candidates for the ACTIVE/effective limit (reflecting current
  range+direction) rather than a stored constant. Needs a targeted test.

  NOTE FOR WRITES: this block is the obvious target if anyone ever wants to
  change the speed caps — and therefore also the most safety-relevant. Treat
  as read-only until a deliberate, separately-reasoned decision says otherwise.

EXCLUDED / ARTIFACTS
--------------------
  0x35C6   FALSE positive: signed value dithering around 0 (+24 -> -24),
           only looked large when read unsigned (0xFFE8). Not a real signal.
  0x3160   ~6.5M monotonic counter (hour-meter / accumulator). Drifts across
           the whole session regardless of action — a time artifact, not state.
  Idle-noise objects (0x3334, 0x3564-0x3569, 0x3573, 0x3588, 0x3601 ...):
           wiggle at rest; treat only changes exceeding their resting spread.
  Sub-index 0x05 of EVERY object returns a constant 0 (broken-dictionary
           quirk) — excluded from all analysis.

NEXT STEPS TO RAISE CONFIDENCE
------------------------------
  - More sweeps per state (>= 10) to firm up the small movers and get signedness.
  - Drive under real mechanical load (not just neutral spin) to separate the
    actual-current (0x35C8) from commanded-torque (0x32xx) objects.
  - Cross-map against the Victron dbus-canopen-motordrive Curtis object list.
  - If the controller can be put in operational/SYNC mode, PDOs would give the
    live telemetry passively at high rate instead of SDO polling.
