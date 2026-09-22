// Host unit test for session_name.h. No framework: compile and run.
//   c++ -std=c++17 -Wall esp32-s3/session_name_test.cpp -o /tmp/sn && /tmp/sn
#include "src/session_name.h"
#include <cassert>
#include <cstring>
#include <cstdio>
#include <cmath>

int main() {
    char buf[24];
    uint32_t n;

    // --- sessionParse: plain and suffixed both yield the number ---
    assert(sessionParse("s00001", n)    && n == 1);
    assert(sessionParse("s00042-42", n) && n == 42);
    assert(sessionParse("s99999-0", n)  && n == 99999);
    assert(sessionParse("s7-anything", n) && n == 7);   // lenient after '-'

    // --- sessionParse rejects non-session dirs ---
    assert(!sessionParse("stuff", n));      // 's' + non-digit
    assert(!sessionParse("saved", n));
    assert(!sessionParse("s001x", n));      // trailing junk without '-'
    assert(!sessionParse("data", n));

    // --- sessionDirName ---
    sessionDirName(buf, sizeof buf, 1);
    assert(strcmp(buf, "/s00001") == 0);
    sessionDirName(buf, sizeof buf, 12345);
    assert(strcmp(buf, "/s12345") == 0);

    // --- socToSuffix: round, clamp, NaN ---
    assert(socToSuffix(42.0f) == 42);
    assert(socToSuffix(42.4f) == 42);
    assert(socToSuffix(42.6f) == 43);       // rounds up
    assert(socToSuffix(-0.8f) == 0);        // scaling floor clamps to 0
    assert(socToSuffix(100.4f) == 100);     // clamps to 100
    assert(socToSuffix(255.0f) == 100);
    assert(socToSuffix(0.0f) == 0);
    assert(socToSuffix(NAN) == -1);         // unknown -> no suffix

    // --- sessionDirNameSoc: suffix, zero-pad, unknown fallback ---
    sessionDirNameSoc(buf, sizeof buf, 1, 42);
    assert(strcmp(buf, "/s00001-42") == 0);
    sessionDirNameSoc(buf, sizeof buf, 1, 5);
    assert(strcmp(buf, "/s00001-05") == 0); // two-digit pad
    sessionDirNameSoc(buf, sizeof buf, 1, 100);
    assert(strcmp(buf, "/s00001-100") == 0);
    sessionDirNameSoc(buf, sizeof buf, 1, -1);
    assert(strcmp(buf, "/s00001") == 0);    // unknown -> plain

    // --- round trip: a suffixed name parses back to its number ---
    sessionDirNameSoc(buf, sizeof buf, 42, socToSuffix(42.4f));
    assert(sessionParse(buf + 1, n) && n == 42);   // +1 skips leading '/'

    printf("session_name_test: all assertions passed\n");
    return 0;
}
