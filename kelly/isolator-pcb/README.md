# Kelly UART isolator PCB

PCB realization of `../isolator.txt` — a galvanic isolator dongle between the
Kelly KLS7218M SM-4P port (floating, traction-pack referenced) and the
OBD-powered RejsaCAN. Electrical design rationale, part choices, and behavior
notes live in `../isolator.txt`; this directory only adds the board.

## Files

- `generate_board.py` — writes `isolator.kicad_pcb` from scratch. The script
  is the source of truth; edit it and re-run rather than editing the board in
  KiCad (or fork the `.kicad_pcb` if you prefer the GUI from here on).
  Output is deterministic — tstamp UUIDs are derived from the element each
  one belongs to, so re-running with no changes produces a byte-identical
  file and a real edit shows up in the diff as itself, not as 187 churned
  UUIDs.
- `isolator.kicad_pcb` — generated KiCad board (opens in KiCad 7+).
- `gerbers/`, `isolator-gerbers.zip` — fab outputs (RS-274X + Excellon),
  exported with KiCad 10 `kicad-cli`. Upload the zip as-is to JLCPCB/PCBWay/
  OSH Park. 2-layer, 50 x 32 mm, 1.6 mm, any finish; no special options.
- `render_top.png` / `render_bottom.png` — 3D renders for a quick look.

Regenerate everything:

```bash
python3 generate_board.py
KICLI=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
$KICLI pcb drc --severity-error isolator.kicad_pcb
$KICLI pcb export gerbers --layers F.Cu,B.Cu,F.Mask,B.Mask,F.SilkS,B.SilkS,Edge.Cuts -o gerbers/ isolator.kicad_pcb
$KICLI pcb export drill --format excellon --excellon-units mm --generate-map --map-format gerberx2 -o gerbers/ isolator.kicad_pcb
(cd gerbers && zip -q ../isolator-gerbers.zip isolator-*.gb* isolator-*.gt* isolator.drl)
$KICLI pcb render --side top    --zoom 1.0 --width 1400 --height 950 --quality high -o render_top.png    isolator.kicad_pcb
$KICLI pcb render --side bottom --zoom 1.0 --width 1400 --height 950 --quality high -o render_bottom.png isolator.kicad_pcb
```

## Rev A erratum

**Rev A has U1 side 1 mis-pinned — GND1 on pin 2, VOA on 3, VIB on 4. Do not
populate it.** The pin table assumed side 1 mirrored side 2; the ADuM1201 puts
GND1 on **pin 4**, at the bottom. Side 2 was correct. No chip orientation
recovers it, and populating shorts V<sub>OA</sub> (an output) to GND1 on
power-up. Scrap the board.

DRC passed rev A because the same wrong table generated both the pads and the
netlist, so there was nothing to compare against. Rev B adds an assert in
`generate_board.py` checking every pad's net against `ADUM1201`, a literal
transcription of the datasheet pinout — the check that was missing.

## Generate-time checks

`generate_board.py` asserts these on every run, against the emitted board
rather than its own variables:

- **U1 pads match the ADuM1201 pinout** (`ADUM1201`, transcribed from
  datasheet Rev. L Fig. 5).
- **Package shape**: supplies on pins 1/8, grounds on 4/5, signals on
  2/3/6/7. This is a *different* statement than "Fig. 5 says X", so it still
  fires if the transcription itself is wrong — which is the hole rev A fell
  through. Rev A's pinout fails it.
- **Isolation barrier**: no copper of a Kelly-domain net may reach the
  chassis-domain side or vice versa. Measures the gap and requires ≥ 3.5 mm;
  currently 3.80 mm.
- **Nothing dangling**: every net lands on at least two pads.

What none of them catch: a wrong fact that is wrong everywhere. If the
datasheet transcription and the package-shape rule were both wrong in the
same direction, the board would still be wrong. The defence against that is
the bring-up continuity check below, done against a physical board.

## Board layout

Vertical isolation gap down the middle (marked on silk, both sides). The only
thing crossing it is the ADuM1201 itself — no copper on any layer between the
two SOIC pad columns (3.80 mm gap, asserted at generate time, the same
barrier the SOIC-8 package provides between its own pin rows).

## Bring-up (before soldering U1)

With the board bare, meter on continuity. Each wire pad should reach exactly
the SOIC pad named below, and the two domains must be open to each other:

| from wire pad | to U1 pad | |
|---------------|-----------|---|
| `12V (RED)`   | U2 `I` leg | via C1 |
| `VOA (BLU)`   | pin 2 (upper-middle, Kelly side) | |
| `VIB (GRN)`   | pin 3 (lower-middle, Kelly side) | |
| `GND1 (BLK)`  | pin 4 (bottom, Kelly side) | |
| `3V3`         | pin 8 (top, ESP32 side) | |
| `(48) VIA`    | pin 7 | |
| `(47) VOB`    | pin 6 | |
| `GND2`        | pin 5 (bottom, ESP32 side) | |

Then confirm **GND1 → GND2 is open** (infinite), and that no Kelly-side pad
reads continuous to any ESP32-side pad. That is the isolation, measured.

Wire connections are round through-hole solder pads, 3.6 mm diameter with a
1.3 mm drill (takes up to ~16 AWG; hookup wire tins into it easily), one
column per side.

## Enclosure

The four 3.2 mm corner holes (43.0 x 25.0 mm pattern) are zip-tie
strain-relief points and take M3 or #4 mounting screws. No off-the-shelf box
has molded posts on this pattern; mount with stick-on standoffs, VHB tape, or
neutral-cure silicone in any ~100 x 60 mm ABS project box (e.g. Hammond
1591XXA-class). If a screw-onto-posts fit is ever wanted, regenerate with the
outline at 72 x 32 mm and holes at 65.0 x 25.0 mm (W and MOUNT_HOLES in
`generate_board.py`, plus the four `P_*` right-column pads and right-side
silk moved out by 22 mm) to match the Hammond 1591XXA's molded posts.

A verified off-the-shelf combination, both DigiKey (prices/stock 2026-08-30):

| Qty | Part | DigiKey | Mfr | $ ea | stock |
|-----|------|---------|-----|------|-------|
| 1 | BOX PC TRN CLEAR 3.94"L x 1.97"W | [HM957-ND](https://www.digikey.com/en/products/detail/hammond-manufacturing/1591ATCL/1090768) | Hammond 1591ATCL | 9.83 | 106 |
| 2 | GROMMET 0.250" RUBBER BLACK | [36-740-ND](https://www.digikey.com/en/products/detail/keystone-electronics/740/631422) | Keystone 740 | 0.32 | 36.8k |

The 1591ATCL is 100 x 50 x 24.8 mm outside, so the 50 x 32 mm board drops in
with room to spare, and the clear lid means the D1 "5V ON" LED is readable
without opening anything or drilling a bezel hole. It is the 1591**A**, not
the 1591**XX**A named above — it has no molded posts anywhere near the
43 x 25 mm hole pattern, so this is still a stick-on-standoff or VHB mount.

One grommet per cable entry (Kelly side, RejsaCAN side), each in a 3/8"
(9.53 mm) hole drilled in an end panel — the grommet's 0.250" bore passes a
4-conductor cable comfortably. They are sized for 2.36 mm panel stock against
the 1591's ~2 mm walls: they seat, just snugly.

Note the enclosure costs **$10.47**, about 4x the $2.73 of board components.
A drilled ABS box from the drawer works exactly as well electrically; the
clear lid and the LED are the only argument for this one.

**Kelly-side pad order (top to bottom) is RED, BLU, GRN, BLK** — blue and
green are swapped relative to the SM-4P harness order in `../isolator.txt` so
the two signal traces reach the ADuM1201 pins without crossing. Follow the
silk labels, not the harness order:

Signal pads and nets are named after the ADuM1201 pins they connect to, so
the direction is always readable from the chip's own convention (VI* = into
the chip, VO* = out of the chip); wire colors and GPIO numbers are silk
descriptors only:

| silk        | wire  | function                                   |
|-------------|-------|--------------------------------------------|
| `12V (RED)` | red   | SM-4P pin 1, ~12 V in → 78L05              |
| `VOA (BLU)` | blue  | chip out → Kelly Rx (driven at 5 V)        |
| `VIB (GRN)` | green | Kelly Tx (5 V-TTL) → chip in               |
| `GND1 (BLK)`| black | Kelly V- (floating)                        |

ESP32 (RejsaCAN) side — silk header `ESP32` — top to bottom: `3V3`,
`(48) VIA` (GPIO48 → chip in),
`(47) VOB` (chip out → GPIO47), `GND2` (chassis ground). The GND1/GND2 numbering
matches the ADuM1201 pins: the two grounds are separate domains and must
never be tied together.

## BOM (matches ../isolator.txt except the breakout)

- U1 — ADuM1201 (any speed grade), **SOIC-8 soldered directly**. Cheaper verified drop-ins are under "Isolator options" in
  `../isolator.txt`; **2Pai Semi π122U31** (~$0.26) is the current pick.
  Check that table before substituting — the sibling part numbers
  (π121U31, NSi8222, CA-IS3721) are mirrored rather than drop-in, and TI
  ISO7721 has a different pinout again.
- U2 — 78L05, TO-92. **Flat side faces the bottom edge — the D1 "5V ON" LED
  row** — matching the silk outline; that puts V_OUT in the `O` hole. Note the
  ST L78L datasheet's TO-92 figure is a *bottom* view (its caption says so),
  so reading it as a front view flips IN and OUT: in backwards you get ~9.7 V
  on the 5 V rail, back-fed through the pass device. Silk `O G I` marks
  OUT/GND/IN and is authoritative — the holes are +5V_K, GND1, +12V_K left to
  right. The 1.1 mm drills also take a TO-220 **LM7805 / L7805CV**, whose
  IN/OUT pins are mirrored vs. the 78L05 — insert it facing the opposite
  way and match the legs to the `O G I` letters. Bend it flat toward the
  board edge if the box lid is tight.
- C1 — 330 nF ceramic, 2.54 mm pitch (12 V in).
- C2, C3 — 100 nF ceramic, 2.54 mm pitch (5 V and 3.3 V rails).
- R1 — 1 kΩ axial, D1 — 5 mm LED ("5V ON" / Kelly-awake indicator; square
  pad = cathode).

## DigiKey shopping list

One board's worth, single-order from DigiKey. Qty-1 prices and stock read off
the DigiKey product pages 2026-08-30; re-check before ordering. Board parts
total **$2.73**, so ~$8.49 ground shipping dominates — order spares of the
sub-dollar parts. Adding the box and grommets from "Enclosure" below brings
the order to **$13.20**.

| Ref | Qty | Part | DigiKey | $ ea | stock |
|-----|-----|------|---------|------|-------|
| U1  | 1 | NSi8221N1-DSPR (NOVOSENSE, SOIC-8) | [22188706](https://www.digikey.com/en/products/detail/novosense/NSI8221N1-DSPR/22188706) | 1.08 | 108 |
| U2  | 1 | MC78L05ACPG (onsemi, TO-92) | [921041](https://www.digikey.com/en/products/detail/onsemi/MC78L05ACPG/921041) | 0.34 | 10.7k |
| C1  | 1 | 0.33 µF 50 V X7R radial, 2.50 mm pitch — TDK FA14X7R1H334KNU06 | [5865807](https://www.digikey.com/en/products/detail/tdk-corporation/FA14X7R1H334KNU06/5865807) | 0.40 | 3.2k |
| C2,C3 | 2 | 0.1 µF 50 V X7R radial, 2.50 mm pitch — Vishay K104K15X7RF5TL2 | [286538](https://www.digikey.com/en/products/detail/vishay-beyschlag-draloric-bc-components/K104K15X7RF5TL2/286538) | 0.29 | 220k |
| R1  | 1 | 1 kΩ 1/4 W axial — Stackpole CF14JT1K00 | [1741314](https://www.digikey.com/en/products/detail/stackpole-electronics-inc/CF14JT1K00/1741314) | 0.10 | 599k |
| D1  | 1 | 5 mm green LED — Kingbright WP7113SGC | [1747672](https://www.digikey.com/en/products/detail/kingbright/WP7113SGC/1747672) | 0.23 | 54k |

**U1 stock is thin** — 108 pieces, and DigiKey is not accepting backorders on
it ("temporarily constrained supply"). Buy two while they are there.

**U1 substitution:** `NSi8221N1` is the 1-fwd/1-rev, ADuM1201-pin-order part —
the one this board needs. `NSi8220`/`NSi8222` are **not** drop-ins (see the
channel-arrangement table in `../isolator.txt`). The genuine
[ADuM1201ARZ](https://www.digikey.com/en/products/detail/analog-devices-inc/ADUM1201ARZ/964322)
also fits at ~$4.82 if you want the part the docs are written around.

**C1 traps, both of them:** most 0.33 µF radial ceramics are 5.08 mm lead
spacing and will not sit in the 2.54 mm holes — filter DigiKey on *Lead
Spacing = 0.098" (2.50mm)*. And of the parts that do fit, price varies 6x for
no electrical reason: KEMET's X7R C320C334K5R5TA is **$2.26**, more than the
isolator chip. The TDK part above is the same 0.33 µF/50 V/X7R at $0.40.
(KEMET's Z5U C320C334M5U5TA is $0.58, but Z5U is a much worse dielectric —
take the TDK.)

### Kelly-side SM-4P connector

The Kelly port is JST **SM series**, 2.5 mm pitch (sold aftermarket as
"SM 2.5").

**Buy these from Amazon, not DigiKey.** Genuine JST housings ship empty and
the crimp contacts are separate line items — and the socket contact that goes
into the plug housing, `SHF-001T-0.8BS`, is **tape-and-reel only at DigiKey,
5000-piece minimum** (~$144). There is no cut-tape option, so the DigiKey
route cannot supply a complete plug for a one-off build.

[SM 2.5 male/female plugs with 22 AWG pre-crimped silicone leads, 2–6 pin
kit](https://www.amazon.com/dp/B0CLV48D6V) is the part previously bought for
this build and confirmed to fit the Kelly port. Solder its flying leads
straight into the board's through-hole pads — no crimper, no housing
assembly, and cheaper than the genuine parts even before shipping.

For reference, the genuine parts if you ever do want them (prices/stock
2026-08-30):

| Part | DigiKey | $ | note |
|------|---------|---|------|
| SMP-04V-BC — 4-pos plug housing | [7230521](https://www.digikey.com/en/products/detail/jst-sales-america-inc/SMP-04V-BC/7230521) | 0.16 | takes SHF sockets |
| SMR-04V-B — 4-pos receptacle housing | [2137107](https://www.digikey.com/en/products/detail/jst-sales-america-inc/SMR-04V-B/2137107) | 0.18 | takes SYM pins |
| SYM-001T-P0.6(N) — pin contact, 22–28 AWG | [1465026](https://www.digikey.com/en/products/detail/jst-sales-america-inc/SYM-001T-P0-6-N/1465026) | 0.10 | → into SMR, cut tape, MOQ 1 |
| SHF-001T-0.8BS — socket contact, 22–28 AWG | [527351](https://www.digikey.com/en/products/detail/jst-sales-america-inc/SHF-001T-0-8BS/527351) | 0.029 | → into SMP, **reel only, MOQ 5000** |

Wire per `../isolator.txt` "Cabling": twist green+black and blue+red, keep the
Kelly run short, label both ends.
