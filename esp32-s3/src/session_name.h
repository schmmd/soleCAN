// SD-card session directory naming — pure string logic, no Arduino deps so it
// can be unit-tested on the host (see session_name_test.cpp).
//
// Session folders are "/sNNNNN", optionally suffixed with the start-of-session
// pack SOC as "-socSS" (e.g. "/s00001-soc42") to make them identifiable when the
// device has no accurate wall-clock time. The suffix is appended by a rename
// once the first BMS SOC frame is decoded; folders whose session never saw a
// SOC reading stay plain "/sNNNNN".
#pragma once

#include <cstdint>
#include <cstdio>
#include <cstdlib>

// Parse a session directory basename: 's' followed by digits, then either the
// end of the string or a '-' suffix. Sets `n` to the session number. Rejects
// anything else (a user's "stuff/" folder, "saved") so the reaper never jams on
// a nonexistent session. Tolerating the '-' suffix is essential: without it the
// reaper and next-session numbering would stop seeing SOC-suffixed folders.
static inline bool sessionParse(const char* nm, uint32_t& n) {
    if (nm[0] != 's' || nm[1] < '0' || nm[1] > '9') return false;
    char* end = nullptr;
    n = strtoul(nm + 1, &end, 10);
    return *end == '\0' || *end == '-';
}

// Plain "/sNNNNN" directory path for a session number.
static inline void sessionDirName(char* out, size_t cap, uint32_t session) {
    snprintf(out, cap, "/s%05lu", (unsigned long)session);
}

// Round a pack SOC percentage to the integer suffix value, clamped 0..100.
// Returns -1 for NaN (SOC not known) — meaning "no suffix".
static inline int socToSuffix(float pct) {
    if (pct != pct) return -1;              // NaN
    int v = (int)(pct + 0.5f);              // round to nearest
    if (v < 0)   v = 0;
    if (v > 100) v = 100;
    return v;
}

// Directory path with the SOC suffix, e.g. "/s00001-soc42". A soc < 0 (unknown)
// falls back to the plain name.
static inline void sessionDirNameSoc(char* out, size_t cap, uint32_t session,
                                     int soc) {
    if (soc < 0) sessionDirName(out, cap, session);
    else snprintf(out, cap, "/s%05lu-soc%02d", (unsigned long)session, soc);
}
