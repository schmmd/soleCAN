#!/usr/bin/env python3
"""
UDAN BMS SecurityAccess Level 1 seed-to-key calculator (YJC variant).

Reconstructed from iBMSUpper.exe v3.1.6 by disassembling main._Cfunc_CalculateKey
→ C function at 0xa4bca0 → real algorithm at 0xa4bb90 (Win32 PE32 x86).

Algorithm:
  1. Nibble-shuffle the 4 seed bytes:
       buf[0] = low(S0)  | high(S3)
       buf[1] = low(S1)  | high(S2)
       buf[2] = high(S1) | low(S2)
       buf[3] = high(S0) | low(S3)
  2. CRC-16-CCITT (poly 0x1021, MSB-first, no reflect) over buf,
     init = 0x13F8.  → CRC_A
  3. Bit-mask shuffle the same 4 ORIGINAL seed bytes:
       buf[0] = (S0 & 0x3C) | (S3 & 0xC3)
       buf[1] = (S1 & 0x3C) | (S2 & 0xC3)
       buf[2] = (S2 & 0x3C) | (S1 & 0xC3)
       buf[3] = (S3 & 0x3C) | (S0 & 0xC3)
     (i.e. each pair {0,3} and {1,2} swaps the middle 4 bits while keeping
      the outer 4 bits.)
  4. CRC-16-CCITT same polynomial, init = 0x76ED, over the new buf. → CRC_B
  5. Key u32 = pack big-endian:
       byte0 (MSB) = CRC_A & 0xFF       (low byte of CRC_A)
       byte1       = CRC_B & 0xFF       (low byte of CRC_B)
       byte2       = (CRC_A >> 8) & 0xFF
       byte3       = (CRC_B >> 8) & 0xFF
"""
import struct, sys

def crc16_ccitt(data: bytes, init: int) -> int:
    crc = init & 0xFFFF
    for b in data:
        idx = ((crc >> 8) ^ b) & 0xFF
        # Table-driven equivalent of:
        #   for _ in range(8): crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
        # but the assembly used a 256-entry word LUT; we do it inline.
        crc = ((crc << 8) & 0xFFFF) ^ _ccitt_step(idx)
    return crc & 0xFFFF

def _ccitt_step(idx: int) -> int:
    v = idx << 8
    for _ in range(8):
        v = ((v << 1) ^ 0x1021) if (v & 0x8000) else (v << 1)
    return v & 0xFFFF

def shuffle_nibbles(s: bytes) -> bytes:
    S0, S1, S2, S3 = s
    return bytes([
        (S0 & 0x0F) | (S3 & 0xF0),
        (S1 & 0x0F) | (S2 & 0xF0),
        (S1 & 0xF0) | (S2 & 0x0F),
        (S0 & 0xF0) | (S3 & 0x0F),
    ])

def shuffle_bits(s: bytes) -> bytes:
    S0, S1, S2, S3 = s
    return bytes([
        (S0 & 0x3C) | (S3 & 0xC3),
        (S1 & 0x3C) | (S2 & 0xC3),
        (S2 & 0x3C) | (S1 & 0xC3),
        (S3 & 0x3C) | (S0 & 0xC3),
    ])

def calc_key(seed: bytes) -> bytes:
    assert len(seed) == 4, "seed must be 4 bytes"
    buf_a = shuffle_nibbles(seed)
    crc_a = crc16_ccitt(buf_a, init=0x13F8)
    buf_b = shuffle_bits(seed)
    crc_b = crc16_ccitt(buf_b, init=0x76ED)
    key_u32 = ((crc_a & 0xFF) << 24) | ((crc_b & 0xFF) << 16) | (((crc_a >> 8) & 0xFF) << 8) | ((crc_b >> 8) & 0xFF)
    return struct.pack(">I", key_u32)

# Captured pairs from BMS.md
PAIRS = [
    ("0D 4A F9 74", "38 20 62 9F"),
    ("66 80 20 47", "92 0F 02 BA"),
    ("9C 43 69 8E", "9A 00 4F 4E"),
    ("2A 4B 8D D2", "3B 87 BE E1"),
    ("2A 64 C4 19", "16 37 31 4D"),
    ("09 E6 16 7A", "17 26 91 AD"),
    ("9F 9C 21 C7", "E1 42 86 06"),
    ("DD F5 53 1B", "13 1F 61 69"),
    ("F8 FD 0C 44", "B1 3E 9A 09"),
]

def hx(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))

def main():
    if len(sys.argv) >= 2 and sys.argv[1] != "--verify":
        seed = hx(sys.argv[1])
        print(calc_key(seed).hex(" ").upper())
        return
    ok = 0
    for s_str, k_str in PAIRS:
        seed = hx(s_str)
        expected = hx(k_str)
        got = calc_key(seed)
        match = "OK " if got == expected else "FAIL"
        if got == expected:
            ok += 1
        print(f"  seed={s_str}  expected={k_str}  got={got.hex(' ').upper()}  {match}")
    print(f"\n{ok}/{len(PAIRS)} pairs match")
    sys.exit(0 if ok == len(PAIRS) else 1)

if __name__ == "__main__":
    main()
