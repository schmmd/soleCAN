# Reproducible build environment for the Solectrac ESP32-S3 CAN-monitor firmware.
#
# The build context is the repo ROOT, because the firmware embeds the canonical
# dashboard.html that lives at the repo root (shared with the Android app).
# From the repo root:
#
#   docker build -f embedded/esp32-s3/Dockerfile -t solectrac-fw .
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

# Pin PlatformIO Core so builds are reproducible. --root-user-action=ignore:
# root is the norm inside a single-purpose build image, so pip's root warning
# is noise here.
RUN pip install --no-cache-dir --root-user-action=ignore "platformio==6.1.19"

WORKDIR /project

# Copy the firmware project (board defs, platformio.ini, src/).
COPY embedded/esp32-s3/ ./

# The canonical dashboard.html lives at the repo root and is gitignored
# inside the firmware tree. Place it where board_build.embed_txtfiles
# expects it (copy_dashboard.py treats a pre-placed file as authoritative).
COPY dashboard.html ./src/dashboard.html

# All of these are optional and embedded at compile time; leave them empty to
# keep the firmware's built-in defaults. WIFI_SSID / WIFI_PASS are the home
# network the board also joins for bench use — empty means AP-only (the stable
# field default; the board still broadcasts its own hotspot). AP_SSID / AP_PASS
# / MDNS_NAME override that hotspot and the mDNS hostname. Override any of these
# via --build-arg, e.g. `set -a; source embedded/esp32-s3/.env; set +a`
# then --build-arg AP_PASS="$AP_PASS".
ARG WIFI_SSID=
ARG WIFI_PASS=
ARG AP_SSID=
ARG AP_PASS=
ARG MDNS_NAME=
# Host-resolved git SHA (the build context omits .git). Pass with
# --build-arg GIT_SHA=$(git rev-parse --short HEAD); falls back to "unknown".
ARG GIT_SHA=unknown
ENV WIFI_SSID=${WIFI_SSID}
ENV WIFI_PASS=${WIFI_PASS}
ENV AP_SSID=${AP_SSID}
ENV AP_PASS=${AP_PASS}
ENV MDNS_NAME=${MDNS_NAME}
ENV GIT_SHA=${GIT_SHA}

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

# Default action: copy the four images needed to flash the board to a mounted
# /out volume on the host. boot_app0.bin lives in the framework package (it
# initializes the OTA-data partition); the others are build outputs. These are
# the same images, at the same offsets, that `pio run -t upload` writes.
CMD ["sh", "-c", "set -e; mkdir -p /out; cp -v .pio/build/rejsacan/firmware.bin .pio/build/rejsacan/bootloader.bin .pio/build/rejsacan/partitions.bin /out/; cp -v \"$(find /root/.platformio -name boot_app0.bin | head -1)\" /out/; echo 'Artifacts copied to /out — flash with esptool (see README)'"]
