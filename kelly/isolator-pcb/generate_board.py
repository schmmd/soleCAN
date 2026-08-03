#!/usr/bin/env python3
"""Generate the Kelly UART galvanic-isolator PCB (see ../isolator.txt).

Emits isolator.kicad_pcb (KiCad s-expression, v7 format — KiCad 8/9/10 open
and upgrade it transparently). Fab outputs are produced from it with
kicad-cli; see README.md in this directory.

Topology (from isolator.txt, board PLANNED). Rows are in physical pin order,
per ADuM1201 datasheet Rev. L Figure 5 -- note GND1 is pin 4, at the BOTTOM
of side 1, not pin 2. Side 1 is NOT the mirror of side 2:

  KELLY SIDE (floating, 5 V)     | barrier |   BOARD SIDE (chassis, 3.3 V)
  RED  12V -> 78L05 -> 5V -> VDD1|1       8|VDD2 <- 3V3
  BLU  Kelly Rx <------------ VOA|2       7|VIA  <- GPIO48 (TX)
  GRN  Kelly Tx ------------- VIB|3       6|VOB  -> GPIO47 (RX)
  BLK  V-  ------------------GND1|4       5|GND2 <- GND

No copper crosses the vertical isolation gap except the ADuM1201 itself.
Wire connections are 3.6 mm round pads with 1.3 mm drills (hookup wire),
one column per side. NOTE: Kelly-side pad order top->bottom is
RED, BLU, GRN, BLK (blue/green swapped vs. the harness order) so the two
signal traces reach the ADuM pins without crossing; pads are silk-labeled.
"""

import re
import uuid


# ---------------------------------------------------------------- constants
OX, OY = 100.0, 50.0          # board origin in KiCad sheet coords
W, H = 50.0, 32.0             # board outline, mm

TRACE_SIG = 0.5
TRACE_PWR = 0.6
VIA_SIZE, VIA_DRILL = 0.8, 0.4

WIRE_PAD = 3.6                # round wire pad diameter
WIRE_DRILL = 1.3

# net table: number -> name. Signal nets use the ADuM1201 pin names so the
# direction is unambiguous: VIB = Kelly Tx into the chip, VOA = out to Kelly
# Rx (both floating); VIA = GPIO48 into the chip, VOB = out to GPIO47 (both
# chassis side). Colors / GPIO numbers appear only on silk.
NETS = {
    1: "+12V_K", 2: "GND1", 3: "+5V_K", 4: "VIB", 5: "VOA",
    6: "+3V3", 7: "GND2", 8: "VOB", 9: "VIA", 10: "LED_K",
}

# ------------------------------------------------------------ key positions
# Kelly-side wire pads (left edge column); VOA = blue wire, VIB = green
P_RED = (5.5, 8.0)
P_VOA = (5.5, 13.0)
P_VIB = (5.5, 18.0)
P_BLK = (5.5, 23.0)
# Board-side wire pads (right edge column); VIA = GPIO48, VOB = GPIO47
P_3V3 = (44.5, 8.0)
P_VIA = (44.5, 13.0)
P_VOB = (44.5, 18.0)
P_GND = (44.5, 23.0)

# ADuM1201 SOIC-8 pinout, datasheet Rev. L Figure 5. This dict is the
# authority; U1 below is checked against it so a mis-assigned pin can't reach
# fab again. Side 1 runs VDD1/VOA/VIB/GND1 top-to-bottom -- it is NOT the
# mirror image of side 2, which is the trap that shipped rev A.
ADUM1201 = {1: "VDD1", 2: "VOA", 3: "VIB", 4: "GND1",
            5: "GND2", 6: "VOB", 7: "VIA", 8: "VDD2"}
# which ADuM1201 pin function each board net is supposed to land on
NET_FUNC = {3: "VDD1", 5: "VOA", 4: "VIB", 2: "GND1",
            7: "GND2", 8: "VOB", 9: "VIA", 6: "VDD2"}

# ADuM1201 SOIC-8 at board center; pin columns at x = 25 +/- 2.7
UX, UY = 25.0, 13.0
PIN_DY = [-1.905, -0.635, 0.635, 1.905]
U1_L = 22.3   # pins 1-4 (Kelly/side-1) column
U1_R = 27.7   # pins 5-8 (board/side-2) column
U1 = {  # pin -> (x, y, net)
    1: (U1_L, UY + PIN_DY[0], 3),   # VDD1 <- 5V
    2: (U1_L, UY + PIN_DY[1], 5),   # VOA  -> Kelly Rx (BLU)
    3: (U1_L, UY + PIN_DY[2], 4),   # VIB  <- Kelly Tx (GRN)
    4: (U1_L, UY + PIN_DY[3], 2),   # GND1
    5: (U1_R, UY + PIN_DY[3], 7),   # GND2
    6: (U1_R, UY + PIN_DY[2], 8),   # VOB  -> GPIO47
    7: (U1_R, UY + PIN_DY[1], 9),   # VIA  <- GPIO48
    8: (U1_R, UY + PIN_DY[0], 6),   # VDD2 <- 3V3
}

for _pin, (_x, _y, _net) in U1.items():
    assert NET_FUNC[_net] == ADUM1201[_pin], (
        f"U1 pin {_pin} routes net {NETS[_net]} ({NET_FUNC[_net]}) but the "
        f"ADuM1201 has {ADUM1201[_pin]} on pin {_pin} (datasheet Fig. 5)")

# 78L05 TO-92, inline 2.54 mm, pins left->right: 1 OUT, 2 GND, 3 IN
U2_OUT = (9.5, 5.5)
U2_GND = (12.04, 5.5)
U2_IN = (14.58, 5.5)
# C1 330n across IN/GND, C2 100n across 5V/GND1, C3 100n across 3V3/GND2
C1_A, C1_B = (14.58, 9.5), (12.04, 9.5)        # A=12V(IN) B=GND1
C2_A, C2_B = (19.5, 8.0), (19.5, 10.54)        # A=+5V    B=GND1
C3_A, C3_B = (31.0, 8.0), (31.0, 10.54)        # A=+3V3   B=GND2
# R1 1k (horizontal, 7.62 mm) + D1 LED across the Kelly 5 V rail,
# along the bottom edge below the wire-pad label corridor
R1_A, R1_B = (21.0, 26.5), (13.38, 26.5)       # A=+5V    B=LED_K
D1_A, D1_K = (11.5, 26.5), (8.96, 26.5)        # anode=LED_K cathode=GND1

# vias where the bottom-layer signal runs surface to reach the SOIC pads
# (pins 2 and 3). x is 20.6 so the GND1 run down to pin 4 can pass at x=19.5
# with ~0.45 mm to the via annulus.
VIA_VOA = (20.6, 12.365)
VIA_VIB = (20.6, 13.635)

# Corner holes: zip-tie strain relief / M3 or #4 screw mounting, 3.2 NPTH
MOUNT_HOLES = [(3.5, 3.5), (3.5, 28.5), (46.5, 3.5), (46.5, 28.5)]


# tstamps are emitted as a placeholder and filled in at the end (see stamp())
UUID_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "solecan.isolator")
UUID_MARK = "@@TSTAMP@@"


def ts():
    return f"(tstamp {UUID_MARK})"


def stamp(text):
    """Replace tstamp placeholders with UUIDs derived from the line each one
    sits on, so regenerating is byte-identical and a layout edit churns only
    the UUIDs of the elements it actually touched -- rather than every UUID
    after the edit, which is what a running counter would do. Lines that are
    identical (e.g. the same fp_line in two copies of a footprint) are
    disambiguated by occurrence count."""
    seen = {}
    out_lines = []
    for line in text.split("\n"):
        original = line
        for i in range(original.count(UUID_MARK)):
            key = f"{original}#{i}"
            seen[key] = seen.get(key, 0) + 1
            u = uuid.uuid5(UUID_NS, f"{key}#{seen[key]}")
            line = line.replace(UUID_MARK, str(u), 1)
        out_lines.append(line)
    return "\n".join(out_lines)


def at(p, extra=""):
    return f"(at {OX + p[0]:.3f} {OY + p[1]:.3f}{extra})"


body = []


# ------------------------------------------------------------------ helpers
def seg(a, b, net, layer="F.Cu", width=TRACE_SIG):
    body.append(
        f'(segment (start {OX+a[0]:.3f} {OY+a[1]:.3f}) '
        f'(end {OX+b[0]:.3f} {OY+b[1]:.3f}) (width {width}) '
        f'(layer "{layer}") (net {net}) {ts()})'
    )


def route(pts, net, layer="F.Cu", width=TRACE_SIG):
    for a, b in zip(pts, pts[1:]):
        seg(a, b, net, layer, width)


def via(p, net):
    body.append(
        f'(via {at(p)} (size {VIA_SIZE}) (drill {VIA_DRILL}) '
        f'(layers "F.Cu" "B.Cu") (net {net}) {ts()})'
    )


def text(s, p, size=1.0, rot=0, justify=None, layer="F.SilkS"):
    j = f" (justify {justify})" if justify else ""
    r = f" {rot}" if rot else ""
    body.append(
        f'(gr_text "{s}" {at(p, r)} (layer "{layer}") {ts()} '
        f'(effects (font (size {size} {size}) (thickness {size*0.16:.2f})){j}))'
    )


def gr_line(a, b, layer="Edge.Cuts", width=0.12):
    body.append(
        f'(gr_line (start {OX+a[0]:.3f} {OY+a[1]:.3f}) '
        f'(end {OX+b[0]:.3f} {OY+b[1]:.3f}) '
        f'(stroke (width {width}) (type solid)) (layer "{layer}") {ts()})'
    )


def fp_open(name, ref, p, ref_off=(0, -2.6), ref_hide=False, smd=False):
    hide = " hide" if ref_hide else ""
    return [
        f'(footprint "solecan:{name}" (layer "F.Cu") {ts()} {at(p)}',
        f'  (attr {"smd" if smd else "through_hole"})',
        f'  (fp_text reference "{ref}" (at {ref_off[0]} {ref_off[1]}) '
        f'(layer "F.SilkS"){hide} {ts()} '
        f'(effects (font (size 0.9 0.9) (thickness 0.15))))',
        f'  (fp_text value "" (at 0 0) (layer "F.Fab") hide {ts()} '
        f'(effects (font (size 0.9 0.9) (thickness 0.15))))',
    ]


def tht_pad(num, dp, net, size, drill, shape="circle"):
    n = f' (net {net} "{NETS[net]}")' if net else ""
    return (
        f'  (pad "{num}" thru_hole {shape} (at {dp[0]:.3f} {dp[1]:.3f}) '
        f'(size {size} {size}) (drill {drill}) '
        f'(layers "*.Cu" "*.Mask"){n} {ts()})'
    )


def fp_line(a, b, layer="F.SilkS", width=0.12):
    return (
        f'  (fp_line (start {a[0]:.3f} {a[1]:.3f}) (end {b[0]:.3f} {b[1]:.3f}) '
        f'(stroke (width {width}) (type solid)) (layer "{layer}") {ts()})'
    )


def fp_rect(x0, y0, x1, y1, layer="F.SilkS"):
    return [fp_line((x0, y0), (x1, y0), layer), fp_line((x1, y0), (x1, y1), layer),
            fp_line((x1, y1), (x0, y1), layer), fp_line((x0, y1), (x0, y0), layer)]


# ------------------------------------------------------------- wire pad fps
def wire_pad(ref, p, net):
    fp = fp_open("WirePad_3.6mm", ref, p, ref_hide=True)
    fp.append(tht_pad(1, (0, 0), net, WIRE_PAD, WIRE_DRILL))
    fp.append(")")
    body.extend(fp)


wire_pad("J1", P_RED, 1)
wire_pad("J2", P_VOA, 5)
wire_pad("J3", P_VIB, 4)
wire_pad("J4", P_BLK, 2)
wire_pad("J5", P_3V3, 6)
wire_pad("J6", P_VIA, 9)
wire_pad("J7", P_VOB, 8)
wire_pad("J8", P_GND, 7)

# --------------------------------------------------------------- U1 SOIC-8
fp = fp_open("SOIC-8_ADuM1201", "U1", (UX, UY), ref_off=(0, -4.0), smd=True)
for pin, (x, y, net) in U1.items():
    fp.append(
        f'  (pad "{pin}" smd rect (at {x-UX:.3f} {y-UY:.3f}) (size 1.6 0.65) '
        f'(layers "F.Cu" "F.Paste" "F.Mask") (net {net} "{NETS[net]}") {ts()})'
    )
fp.extend(fp_rect(-1.95, -2.45, 1.95, 2.45))
fp.append(fp_line((-1.95, -1.6), (-1.1, -2.45)))          # pin-1 chamfer mark
fp.append(")")
body.extend(fp)

# --------------------------------------------------------------- U2 78L05
fp = fp_open("TO-92_78L05", "U2", (12.04, 5.5), ref_off=(-4.6, 0))
# 1.1 mm drills take both a TO-92 78L05 and a TO-220 L7805CV (whose
# IN/OUT are mirrored -- follow the O G I silk letters, not the outline)
fp.append(tht_pad(1, (-2.54, 0), 3, 2.0, 1.1))    # OUT
fp.append(tht_pad(2, (0, 0), 2, 2.0, 1.1))        # GND
fp.append(tht_pad(3, (2.54, 0), 1, 2.0, 1.1))     # IN
# TO-92 body outline: flat side facing board bottom (+y), pins 1-3 L->R
fp.append(fp_line((-2.3, 1.4), (2.3, 1.4)))
fp.append(fp_line((-2.3, 1.4), (-2.3, 0.2)))
fp.append(fp_line((2.3, 1.4), (2.3, 0.2)))
fp.append(fp_line((-2.3, 0.2), (2.3, 0.2)))
fp.append(")")
body.extend(fp)
for lbl, x in (("O", 9.5), ("G", 12.04), ("I", 14.58)):
    text(lbl, (x, 3.6), size=0.7)

# ----------------------------------------------------------------- caps
def cap(ref, a, b, net_a, net_b, ref_off):
    cx, cy = (a[0]+b[0])/2, (a[1]+b[1])/2
    fp = fp_open("C_disc_2.54", ref, (cx, cy), ref_off=ref_off)
    fp.append(tht_pad(1, (a[0]-cx, a[1]-cy), net_a, 1.7, 0.8))
    fp.append(tht_pad(2, (b[0]-cx, b[1]-cy), net_b, 1.7, 0.8))
    horiz = abs(a[0]-b[0]) > abs(a[1]-b[1])
    if horiz:
        fp.extend(fp_rect(-0.9, -1.0, 0.9, 1.0))
    else:
        fp.extend(fp_rect(-1.0, -0.9, 1.0, 0.9))
    fp.append(")")
    body.extend(fp)


cap("C1", C1_A, C1_B, 1, 2, (-2.9, 0))      # 330n on 12V in
cap("C2", C2_A, C2_B, 3, 2, (2.2, 0))       # 100n on 5V
cap("C3", C3_A, C3_B, 6, 7, (-2.2, 0))      # 100n on 3V3
text("330n", (13.3, 11.8), size=0.7)
text("100n", (17.6, 9.3), size=0.7, rot=90)
text("100n", (32.9, 9.3), size=0.7, rot=90)

# ------------------------------------------------------------ R1 + D1 LED
R1_CX = (R1_A[0] + R1_B[0]) / 2
fp = fp_open("R_axial_7.62", "R1", (R1_CX, 26.5), ref_off=(0, -2.2))
fp.append(tht_pad(1, (R1_A[0]-R1_CX, 0), 3, 1.7, 0.8))
fp.append(tht_pad(2, (R1_B[0]-R1_CX, 0), 10, 1.7, 0.8))
fp.extend(fp_rect(-2.5, -0.8, 2.5, 0.8))
fp.append(")")
body.extend(fp)
text("1k", (R1_CX, 28.4), size=0.7)

D1_CX = (D1_A[0] + D1_K[0]) / 2
fp = fp_open("LED_5mm", "D1", (D1_CX, 26.5), ref_off=(-3.55, 0))
fp.append(tht_pad(1, (D1_A[0]-D1_CX, 0), 10, 1.8, 0.9))   # anode
fp.append(tht_pad(2, (D1_K[0]-D1_CX, 0), 2, 1.8, 0.9, shape="rect"))  # cathode
fp.append(fp_line((-2.05, -1.0), (-2.05, 1.0)))          # cathode-side bar
fp.append(")")
body.extend(fp)
text("5V ON", (D1_CX, 29.2), size=0.7)

# ------------------------------------------------------------ zip-tie holes
for i, p in enumerate(MOUNT_HOLES, 1):
    fp = fp_open("Hole_3.2_NPTH", f"H{i}", p, ref_hide=True)
    fp.append(
        f'  (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) '
        f'(layers "*.Cu" "*.Mask") {ts()})'
    )
    fp.append(")")
    body.extend(fp)

# ------------------------------------------------------------------ routing
# +12V: RED pad -> U2 IN (bottom layer, under the GND1 spine)
route([P_RED, (12.5, 8.0), (14.58, 6.8), U2_IN], 1, "B.Cu", TRACE_PWR)
# +5V: U2 OUT -> C2A -> U1 VDD1 (top)
route([U2_OUT, (9.5, 4.0), (19.5, 4.0), C2_A, (22.3, 8.0), U1[1][:2]],
      3, "F.Cu", TRACE_PWR)
# +5V spur to R1: bottom layer, down the Kelly edge of the isolation gap
route([C2_A, (22.6, 9.0), (22.6, 26.5), R1_A], 3, "B.Cu", TRACE_SIG)
# LED_K: R1 -> D1 anode
route([R1_B, D1_A], 10, "F.Cu", TRACE_SIG)
# D1 cathode -> BLK (GND1)
route([D1_K, (6.5, 25.5), P_BLK], 2, "F.Cu", TRACE_SIG)
# GND1 spine (top): BLK -> C1B -> U2 GND, spur east to C2B.
# The spur runs at y=13.5, not 11.5 where it hugged C1: it sat 0.90 mm below
# C1_A (+12V) and 0.75 mm below the cap body, leaving nothing to get tweezers
# into. Now 2.90 mm and 2.75 mm.
# Two clearances at C1 that this does NOT change, both floors rather than
# choices: C1's own pads are 0.84 mm apart (2.54 mm pitch, 1.7 mm pads), and
# C1_A sits 1.39 mm from the GND1 spine running up x=12.04 to U2. The
# tightest copper at C1 is neither -- it is 0.35 mm from C1_B to the +12V run
# on B.Cu at y=8.0, which is an input-side trace, not this ground line.
route([P_BLK, (12.04, 23.0), C1_B, U2_GND], 2, "F.Cu", TRACE_PWR)
route([(12.04, 13.5), (19.5, 13.5), C2_B], 2, "F.Cu", TRACE_SIG)
# GND1 to pin 4: tee off the C2B spur at (19.5, 13.5) and drop down the west
# side of the via column, then in from below. Runs left of the pin-2/3 stubs
# so nothing crosses. C2's return to pin 4 is ~10 mm via this tee -- longer
# than ideal for a bypass cap, but the link is 19200 Bd with ~10 ns edges.
route([(19.5, 13.5), (19.5, 16.8), (21.4, 16.8), U1[4][:2]],
      2, "F.Cu", TRACE_SIG)
# C1A -> U2 IN (12V)
route([C1_A, U2_IN], 1, "F.Cu", TRACE_PWR)
# VOA (chip out -> Kelly Rx, blue): bottom to via, top stub into pin 2
route([P_VOA, (18.3, 12.365), VIA_VOA], 5, "B.Cu", TRACE_SIG)
via(VIA_VOA, 5)
route([VIA_VOA, U1[2][:2]], 5, "F.Cu", TRACE_SIG)
# VIB (Kelly Tx -> chip in, green): bottom to via, top stub into pin 3
route([P_VIB, (18.3, 13.635), VIA_VIB], 4, "B.Cu", TRACE_SIG)
via(VIA_VIB, 4)
route([VIA_VIB, U1[3][:2]], 4, "F.Cu", TRACE_SIG)
# +3V3: pad -> C3A -> VDD2 (pin 8)
route([P_3V3, C3_A, (27.7, 9.5), U1[8][:2]], 6, "F.Cu", TRACE_PWR)
# VIA (GPIO48 -> chip in): pad -> pin 7
route([P_VIA, (29.5, 12.365), U1[7][:2]], 9, "F.Cu", TRACE_SIG)
# VOB (chip out -> GPIO47): pad -> pin 6
route([P_VOB, (29.5, 13.635), U1[6][:2]], 8, "F.Cu", TRACE_SIG)
# GND2: pad -> GND2 (pin 5) on top; C3B joins on bottom
route([P_GND, (29.5, 14.905), U1[5][:2]], 7, "F.Cu", TRACE_PWR)
route([C3_B, (31.0, 20.0), P_GND], 7, "B.Cu", TRACE_SIG)

# --------------------------------------------------------------- silkscreen
for x in (23.6, 26.4):
    gr_line((x, 1.0), (x, 31.0), layer="F.SilkS", width=0.2)
    gr_line((x, 1.0), (x, 31.0), layer="B.SilkS", width=0.2)
text("ISOLATION", (25.0, 24.5), size=0.9, rot=90)
text("KELLY", (11.5, 1.8), size=1.1)
text("ESP32", (37.5, 1.8), size=1.1)
text("UART ISOLATOR", (37.5, 28.6), size=0.9)
text("ADuM1201", (37.5, 30.4), size=0.9)

pad_lbl = [
    (P_RED, "12V (RED)"), (P_VOA, "VOA (BLU)"), (P_VIB, "VIB (GRN)"),
    (P_BLK, "GND1 (BLK)"),
]
for p, s in pad_lbl:
    text(s, (p[0] + 3.0, p[1]), size=0.8, justify="left")
for p, s in [(P_3V3, "3V3"), (P_VIA, "(48) VIA"), (P_VOB, "(47) VOB"),
             (P_GND, "GND2")]:
    text(s, (p[0] - 3.0, p[1]), size=0.8, justify="right")

# --------------------------------------------------------------- edge cuts
gr_line((0, 0), (W, 0))
gr_line((W, 0), (W, H))
gr_line((W, H), (0, H))
gr_line((0, H), (0, 0))

# ------------------------------------------------------------------- emit
LAYERS = """  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (34 "B.Paste" user)
    (35 "F.Paste" user)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (38 "B.Mask" user)
    (39 "F.Mask" user)
    (40 "Dwgs.User" user "User.Drawings")
    (41 "Cmts.User" user "User.Comments")
    (44 "Edge.Cuts" user)
    (48 "B.Fab" user)
    (49 "F.Fab" user)
  )"""

nets_sexp = "\n".join(f'  (net {n} "{name}")' for n, name in NETS.items())

out = f"""(kicad_pcb (version 20221018) (generator solecan_isolator)
  (general (thickness 1.6))
  (paper "A4")
{LAYERS}
  (setup
    (pad_to_mask_clearance 0.05)
  )
  (net 0 "")
{nets_sexp}
""" + "\n".join(body) + "\n)\n"

out = stamp(out)
assert UUID_MARK not in out, "unfilled tstamp placeholder"
_ids = re.findall(r"\(tstamp ([0-9a-f-]+)\)", out)
assert len(_ids) == len(set(_ids)), "duplicate tstamp UUIDs -- stamp() collided"


# ------------------------------------------------------------- validation
# These parse the emitted board rather than the builder variables, so a bug
# in the emit path can't slip past them either.

# 1. Package-shape cross-check on ADUM1201. 8-pin digital isolators put the
#    two supplies at the top corners and the two grounds at the bottom
#    corners, signals in between. That is a different statement than "Figure
#    5 says X", so it catches a mistyped transcription: rev A believed GND1
#    was pin 2 and fails here.
assert {p for p, f in ADUM1201.items() if f.startswith("VDD")} == {1, 8}, ADUM1201
assert {p for p, f in ADUM1201.items() if f.startswith("GND")} == {4, 5}, ADUM1201
assert {p for p, f in ADUM1201.items()
        if f[0] == "V" and f[1] in "IO"} == {2, 3, 6, 7}, ADUM1201

# 2. Isolation barrier. The board's entire purpose is that no copper crosses
#    between the two SOIC pad columns; until now that was only asserted in
#    prose in README.md. Measure it from the emitted geometry instead.
SIDE1 = {1, 2, 3, 4, 5, 10}    # nets referenced to Kelly V- (floating)
SIDE2 = {6, 7, 8, 9}           # nets referenced to chassis ground
assert SIDE1 | SIDE2 == set(NETS), "net not assigned to an isolation domain"


def copper():
    """Yield (net, x_min, x_max) in board mm for every piece of copper."""
    fp_org = None
    for line in out.split("\n"):
        if line.startswith("(footprint"):
            m = re.search(r"\(at ([\d.]+) ([\d.]+)\)", line)
            fp_org = (float(m.group(1)) - OX, float(m.group(2)) - OY)
        m = re.match(r"\(segment \(start ([\d.]+) [\d.]+\) \(end ([\d.]+) "
                     r"[\d.]+\) \(width ([\d.]+)\).*\(net (\d+)\)", line)
        if m:
            a, b, w, n = (float(m[1]) - OX, float(m[2]) - OX,
                          float(m[3]), int(m[4]))
            yield n, min(a, b) - w / 2, max(a, b) + w / 2
        m = re.match(r"\(via \(at ([\d.]+) [\d.]+\) \(size ([\d.]+)\)"
                     r".*\(net (\d+)\)", line)
        if m:
            x, s, n = float(m[1]) - OX, float(m[2]), int(m[3])
            yield n, x - s / 2, x + s / 2
        m = re.match(r'\s*\(pad "\S*" \S+ \S+ \(at (-?[\d.]+) (-?[\d.]+)\) '
                     r'\(size ([\d.]+) [\d.]+\).*\(net (\d+) ', line)
        if m:
            x = fp_org[0] + float(m[1])
            yield int(m[4]), x - float(m[3]) / 2, x + float(m[3]) / 2


s1_max = max(hi for n, lo, hi in copper() if n in SIDE1)
s2_min = min(lo for n, lo, hi in copper() if n in SIDE2)
GAP = s2_min - s1_max
assert GAP >= 3.5, (
    f"isolation gap is {GAP:.2f} mm -- side 1 copper reaches x={s1_max:.2f}, "
    f"side 2 starts at x={s2_min:.2f}")

# 3. Nothing dangling: every net must land on at least two pads, or it is
#    wired to exactly one thing and does nothing.
_pad_nets = re.findall(r'\(pad "\S*" .*\(net (\d+) ', out)
for _n in NETS:
    assert _pad_nets.count(str(_n)) >= 2, f"net {NETS[_n]} reaches < 2 pads"

import pathlib
outfile = pathlib.Path(__file__).parent / "isolator.kicad_pcb"
outfile.write_text(out)
print(f"wrote {outfile} ({len(_ids)} tstamps)")
