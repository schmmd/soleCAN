# Kelly UART isolator PCB

A galvanic isolator dongle between the Kelly KLS7218M SM-4P port (floating,
traction-pack referenced) and the OBD-powered RejsaCAN. Electrical design
rationale, part choices, and behavior notes live in `../ISOLATOR.md`; this
directory only adds the board.

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
a meter on a physical board: the GND1-to-GND2 open check in
`../ISOLATOR.md` "Power-up check".
