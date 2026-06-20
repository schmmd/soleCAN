"""Optionally override WiFi identity strings at build time from env vars.

If any of AP_SSID / AP_PASS / MDNS_NAME are set in the environment (e.g. by
sourcing .env), inject them as compile-time defines. When they are unset this
is a no-op, so the firmware falls back to the defaults compiled into
src/main.cpp and the default build is unchanged (byte-identical).
"""
import os

Import("env")  # noqa: F821 — provided by PlatformIO

for name in ("AP_SSID", "AP_PASS", "MDNS_NAME"):
    value = os.environ.get(name)
    if value:
        env.Append(CPPDEFINES=[(name, env.StringifyMacro(value))])  # noqa: F821
