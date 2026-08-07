# Reproducible build environment for the Solectrac ESP32-S3 CAN-monitor firmware.
#
# The build context is the repo ROOT, because the firmware embeds the canonical
# dashboard.html that lives at the repo root (shared with the Android app).
# From the repo root:
#
#   docker build -f esp32-s3/Dockerfile -t solectrac-fw .
#
# Then extract the build artifacts onto the host so you can flash from macOS —
# Docker Desktop on macOS cannot reach the board's USB serial port, so flashing
# happens on the host:
#
#   docker run --rm -v "$PWD/out:/out" solectrac-fw
#
# Pinned to Python 3.13: PlatformIO 6.1.x segfaults during package
# post-install on Python 3.14, which is why the native Homebrew install fails.
FROM python:3.13-slim

# git + ca-certificates are needed by PlatformIO to fetch the platform,
# toolchain, and framework packages.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# esptool is pinned separately from the copy PlatformIO vendors, because the
# merge step below calls it directly. The pin exists so that CLI surface and
# merged-image output can't shift out from under this build unnoticed —
# esptool 5.x renamed the `merge_bin` subcommand to `merge-bin` and several
# flags to hyphenated forms, and while 5.3.1's Group.get_command currently
# accepts the old underscore names as deprecated aliases (so that rename
# alone would not break this build today), relying on an undocumented
# compatibility shim staying in place across major releases is exactly the
# kind of drift a pin is meant to prevent.
RUN pip install --no-cache-dir --root-user-action=ignore \
        "platformio==6.1.19" \
        "esptool==4.8.1"

WORKDIR /project

# Copy the firmware project (board defs, platformio.ini, src/).
COPY esp32-s3/ ./

# The canonical dashboard.html lives at the repo root and is gitignored
# inside the firmware tree. Place it where board_build.embed_txtfiles
# expects it (copy_dashboard.py treats a pre-placed file as authoritative).
COPY dashboard.html ./src/dashboard.html

# All of these are optional and embedded at compile time; leave them empty to
# keep the firmware's built-in defaults. WIFI_SSID / WIFI_PASS are the home
# network the board also joins for bench use — empty means AP-only (the stable
# field default; the board still broadcasts its own hotspot). AP_SSID / AP_PASS
# / MDNS_NAME override that hotspot and the mDNS hostname. Override any of these
# via --build-arg, e.g. `set -a; source esp32-s3/.env; set +a`
# then --build-arg AP_PASS="$AP_PASS".
ARG WIFI_SSID=
ARG WIFI_PASS=
ARG AP_SSID=
ARG AP_PASS=
ARG MDNS_NAME=
# Host-resolved git SHA (the build context omits .git). Pass with
# --build-arg GIT_SHA=$(git rev-parse --short HEAD); falls back to "unknown".
ARG GIT_SHA=unknown
# Release tag, only on tagged builds (CI passes the tag; empty otherwise). Shown
# alongside the SHA when set. Pass --build-arg GIT_VERSION=$(git describe --tags --exact-match 2>/dev/null).
ARG GIT_VERSION=
ENV WIFI_SSID=${WIFI_SSID}
ENV WIFI_PASS=${WIFI_PASS}
ENV AP_SSID=${AP_SSID}
ENV AP_PASS=${AP_PASS}
ENV MDNS_NAME=${MDNS_NAME}
ENV GIT_SHA=${GIT_SHA}
ENV GIT_VERSION=${GIT_VERSION}

# Optional: pass --build-arg CAN_ALLOW_TX=1 to bring both CAN controllers up in
# NORMAL mode so they ACK frames and can transmit (SLCAN t/T injection over USB,
# socketcand `< send >` over WiFi). Empty (default) builds listen-only: a
# hardware-enforced passive tap that never drives the bus. PlatformIO appends
# PLATFORMIO_BUILD_FLAGS to build_flags; an empty value is a no-op, so the
# default image stays a passive tap.
ARG CAN_ALLOW_TX=
ENV PLATFORMIO_BUILD_FLAGS=${CAN_ALLOW_TX:+-DCAN_ALLOW_TX}

# Build for the RejsaCAN-ESP32-S3. This downloads the pioarduino espressif32
# platform and Xtensa toolchain on first run and caches them in the image layer.
RUN pio run -e rejsacan

# Combine the four flash images into one that can be written at offset 0.
# This is what a browser flasher (ESP Web Tools, esp.huhn.me, Adafruit
# ESPTool) needs — they take a single file and an offset, not four. It also
# works from the esptool CLI: `write_flash 0x0 firmware-merged.bin`.
#
# --flash_mode/freq/size keep: inherit whatever the build baked into the
# bootloader header. The rejsacan env uses qio_opi memory type; hardcoding
# dio/80m here would silently produce an image that does not boot if the
# board definition ever changes. `keep` cannot drift.
#
# The offsets are the standard Arduino-ESP32 ones, the same four this image's
# CMD emits and that esp32-s3/README.md documents for manual flashing.
RUN BOOT_APP0="$(find /root/.platformio -name boot_app0.bin | head -1)" \
    && python -m esptool --chip esp32s3 merge_bin \
        -o .pio/build/rejsacan/firmware-merged.bin \
        --flash_mode keep --flash_freq keep --flash_size keep \
        0x0     .pio/build/rejsacan/bootloader.bin \
        0x8000  .pio/build/rejsacan/partitions.bin \
        0xe000  "$BOOT_APP0" \
        0x10000 .pio/build/rejsacan/firmware.bin

# Default action: copy the flash images to a mounted /out volume on the host —
# the four images at their individual offsets, plus firmware-merged.bin, which
# is all four combined into a single image written at offset 0.
# boot_app0.bin lives in the framework package (it
# initializes the OTA-data partition); the others are build outputs. These are
# the same images, at the same offsets, that `pio run -t upload` writes.
CMD ["sh", "-c", "set -e; mkdir -p /out; cp -v .pio/build/rejsacan/firmware.bin .pio/build/rejsacan/bootloader.bin .pio/build/rejsacan/partitions.bin .pio/build/rejsacan/firmware-merged.bin /out/; cp -v \"$(find /root/.platformio -name boot_app0.bin | head -1)\" /out/; echo 'Artifacts copied to /out — flash firmware-merged.bin at 0x0, or the four images at their offsets (see README)'"]
