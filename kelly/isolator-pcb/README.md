# Kelly UART isolator PCB

Status: **PLANNED — rev B, not yet fabbed.** Rev A was fabbed and is
**scrapped**; see [Rev A erratum](#rev-a-erratum) before soldering anything to
a board you already have. PCB realization of `../isolator.txt` — a galvanic
isolator dongle between the Kelly KLS7218M SM-4P port (floating, traction-pack
referenced) and the OBD-powered RejsaCAN. Electrical design rationale, part
choices, and behavior notes live in `../isolator.txt`; this directory only
adds the board.

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

## Board layout

Vertical isolation gap down the middle (marked on silk, both sides). The only
thing crossing it is the ADuM1201 itself — no copper on any layer between the
two SOIC pad columns (~3.8 mm gap, the same barrier the SOIC-8 package
provides between its own pin rows).

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

- U1 — ADuM1201 (any speed grade), **SOIC-8 soldered directly**; the
  CJMCU-1201 breakout from the build plan is not needed on this board.
  TI ISO7721 (same package) does *not* drop in — different pinout; check
  before substituting.
- U2 — 78L05, TO-92. **Flat side faces the bottom edge — the D1 "5V ON" LED
  row** — matching the silk outline; that puts V_OUT in the `O` hole. Note the
  ST L78L datasheet's TO-92 figure is a *bottom* view (its caption says so),
  so reading it as a front view flips IN and OUT: in backwards you get ~9.7 V
  on the 5 V rail, back-fed through the pass device. Silk `O G I` marks
  OUT/GND/IN and is authoritative — the holes are +5V_K, GND1, +12V_K left to
  right. The 1.1 mm drills also take a TO-220 **L7805CV**, whose
  IN/OUT pins are mirrored vs. the 78L05 — insert it facing the opposite
  way and match the legs to the `O G I` letters. Bend it flat toward the
  board edge if the box lid is tight.
- C1 — 330 nF ceramic, 2.54 mm pitch (12 V in).
- C2, C3 — 100 nF ceramic, 2.54 mm pitch (5 V and 3.3 V rails).
- R1 — 1 kΩ axial, D1 — 5 mm LED ("5V ON" / Kelly-awake indicator; square
  pad = cathode).
