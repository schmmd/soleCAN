// Short CANopen poll table for -DCANOPEN_FAST (see canopen/README.md,
// "STATIONARY SWITCH SWEEP"): the seat-limp / max-speed / flag objects, polled
// at ~15 Hz instead of once per 8 s. 0x1000 stays first so the capture tools'
// sweep-boundary detection works unchanged. Hand-maintained.
#pragma once
#include <stdint.h>
static const uint16_t kCanopenObjects[] = {
    0x1000,           // sweep marker (Device Type, constant)
    0x3011, 0x306E, 0x3593, 0x3840, 0x33D1,   // Max_Speed_SpdM + mirrors
    0x3213,           // Throttle_Multiplier
    0x3228,           // OEM flag word
    0x35AA, 0x35B7,   // headroom / limit cluster
    0x322B,           // Interlock / System_Flags1
    0x3226,           // Switches
    0x33E6,           // packed range+dir
    0x33EF,           // rotor angle? (wraps while turning)
    0x3216,           // Throttle_Command
    0x3207,           // Motor_RPM
};
