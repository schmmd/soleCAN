# GitHub Release Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a GitHub Release on tag push carrying a browser-flashable merged ESP32-S3 firmware image and the Android debug APK.

**Architecture:** `esp32-s3/Dockerfile` gains a pinned `esptool` and a `merge_bin` step, so the four flash images are combined into one `firmware-merged.bin` writable at offset 0 — inside the image, so the offsets live in one place and the artifact is reproducible on a laptop. A new `.github/workflows/release.yml` builds both existing Dockerfiles in parallel jobs, extracts `/out` from each, and a third job renames the two files and calls `gh release create`.

**Tech Stack:** Docker, PlatformIO 6.1.19, esptool 4.8.1, GitHub Actions (`docker/build-push-action@v6`, `actions/upload-artifact@v4`, `gh` CLI).

**Spec:** `docs/superpowers/specs/2026-07-28-github-release-artifacts-design.md`

## Global Constraints

- **Board scope: `rejsacan` only.** Do not parameterize the Dockerfile's `pio run -e rejsacan`. `lilygo_t2can` and `adafruit_feather_s3` are not released.
- **No WiFi secrets.** The release firmware build passes `GIT_SHA` and nothing else. Never add `WIFI_SSID`, `WIFI_PASS`, `AP_SSID`, `AP_PASS`, or `MDNS_NAME` to the workflow, and never add a GitHub secret for them. The release ships the stock AP-only defaults.
- **Debug-signed APK.** Ship `app-debug.apk` as `android/Dockerfile` already builds it. No keystore, no signing config, no secrets.
- **Two release assets, exactly.** `solecan-firmware-rejsacan-$VERSION-merged.bin` and `solecan-android-$VERSION-debug.apk`. Do **not** add a `manifest.json` or a zip of the raw images — both were explicitly considered and cut in the spec.
- **esptool is pinned to `4.8.1`.** This is load-bearing: esptool 5.x renamed the subcommand `merge_bin` → `merge-bin`. Invoke it as `python -m esptool` (stable across versions), not `esptool.py`.
- **Flash parameters must be `keep`.** `--flash_mode keep --flash_freq keep --flash_size keep`. The `rejsacan` env uses `qio_opi` memory type; hardcoding `dio`/`80m` produces an image that does not boot.
- **The four existing images stay in `/out`.** The merge is additive. The documented four-image `esptool write_flash` path in `esp32-s3/README.md` must keep working unchanged.
- **`.github/workflows/docker-builds.yml` is not modified.** It remains the per-PR build check. The new workflow reuses its GHA cache scopes (`firmware`, `android`).
- **No unit-test harness exists in this repo.** Verification is command output — `cmp`, `esptool image_info`, and a `workflow_dispatch` draft run. Do not invent a test framework.

---

### Task 1: Merge the flash images inside the firmware Docker build

**Files:**
- Modify: `esp32-s3/Dockerfile` — add the esptool pin to the existing `pip install` line, add a merge `RUN` after `RUN pio run -e rejsacan`, extend the `CMD` copy list.
- Modify: `esp32-s3/README.md` — the "Building with Docker" section (~line 250-290): update the `docker run` output list and add a "Flash from a browser" subsection.

**Interfaces:**
- Produces: `/out/firmware-merged.bin` inside the built image — a single flash image written at offset `0x0`. Task 2 consumes this exact filename.
- Unchanged: `/out/{bootloader,partitions,boot_app0,firmware}.bin`.

- [ ] **Step 1: Pin esptool alongside PlatformIO**

In `esp32-s3/Dockerfile`, find this block:

```dockerfile
# Pin PlatformIO Core so builds are reproducible. --root-user-action=ignore:
# root is the norm inside a single-purpose build image, so pip's root warning
# is noise here.
RUN pip install --no-cache-dir --root-user-action=ignore "platformio==6.1.19"
```

Replace the `RUN` line (keep the comment above it) with:

```dockerfile
# esptool is pinned separately from the copy PlatformIO vendors, because the
# merge step below calls it directly. The pin is load-bearing: esptool 5.x
# renamed the `merge_bin` subcommand to `merge-bin`, so an unpinned install
# would break this build on their next major release.
RUN pip install --no-cache-dir --root-user-action=ignore \
        "platformio==6.1.19" \
        "esptool==4.8.1"
```

- [ ] **Step 2: Add the merge step after the build**

Find this block near the end of `esp32-s3/Dockerfile`:

```dockerfile
# Build for the RejsaCAN-ESP32-S3. This downloads the pioarduino espressif32
# platform and Xtensa toolchain on first run and caches them in the image layer.
RUN pio run -e rejsacan
```

Immediately **after** that `RUN`, add:

```dockerfile
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
```

- [ ] **Step 3: Add the merged image to the CMD copy list**

The current final line of `esp32-s3/Dockerfile` is:

```dockerfile
CMD ["sh", "-c", "set -e; mkdir -p /out; cp -v .pio/build/rejsacan/firmware.bin .pio/build/rejsacan/bootloader.bin .pio/build/rejsacan/partitions.bin /out/; cp -v \"$(find /root/.platformio -name boot_app0.bin | head -1)\" /out/; echo 'Artifacts copied to /out — flash with esptool (see README)'"]
```

Replace it with (note the added `firmware-merged.bin` and the updated echo — the rest is unchanged):

```dockerfile
CMD ["sh", "-c", "set -e; mkdir -p /out; cp -v .pio/build/rejsacan/firmware.bin .pio/build/rejsacan/bootloader.bin .pio/build/rejsacan/partitions.bin .pio/build/rejsacan/firmware-merged.bin /out/; cp -v \"$(find /root/.platformio -name boot_app0.bin | head -1)\" /out/; echo 'Artifacts copied to /out — flash firmware-merged.bin at 0x0, or the four images at their offsets (see README)'"]
```

Also update the comment block directly above the CMD. It currently begins:

```dockerfile
# Default action: copy the four images needed to flash the board to a mounted
# /out volume on the host. boot_app0.bin lives in the framework package (it
```

Change the first sentence to:

```dockerfile
# Default action: copy the flash images to a mounted /out volume on the host —
# the four images at their individual offsets, plus firmware-merged.bin, which
# is all four combined into a single image written at offset 0.
# boot_app0.bin lives in the framework package (it
```

- [ ] **Step 4: Build the image**

Run from the repo root:

```bash
docker build -f esp32-s3/Dockerfile \
    --build-arg GIT_SHA=$(git rev-parse --short HEAD) -t solectrac-fw .
```

Expected: build succeeds. First run downloads the Xtensa toolchain and takes several minutes.

- [ ] **Step 5: Extract and confirm all five images appear**

```bash
rm -rf out && docker run --rm -v "$PWD/out:/out" solectrac-fw && ls -l out/
```

Expected: `out/` contains `bootloader.bin`, `partitions.bin`, `boot_app0.bin`, `firmware.bin`, **and** `firmware-merged.bin`. The merged file should be roughly `0x10000` (65536) bytes plus the size of `firmware.bin`.

- [ ] **Step 6: Verify each part landed at its offset**

This is the real correctness check — it proves the merge placed every image at the offset the bootloader expects. `cmp FILE1 FILE2 SKIP1 SKIP2` compares starting at the given byte offsets; `-n` limits how many bytes.

```bash
cd out
cmp -n $(wc -c < bootloader.bin)  firmware-merged.bin bootloader.bin  0     0 && echo "bootloader  @0x0     OK"
cmp -n $(wc -c < partitions.bin)  firmware-merged.bin partitions.bin  32768 0 && echo "partitions  @0x8000  OK"
cmp -n $(wc -c < boot_app0.bin)   firmware-merged.bin boot_app0.bin   57344 0 && echo "boot_app0   @0xe000  OK"
cmp -n $(wc -c < firmware.bin)    firmware-merged.bin firmware.bin    65536 0 && echo "firmware    @0x10000 OK"
cd ..
```

Expected: all four `OK` lines print, no `differ:` output.

> **Note on the spec:** the spec's testing section says to confirm this with `esptool image_info`. That is not quite right — `image_info` only decodes the *first* image in a file, so on the merged binary it reports the bootloader and nothing else. The `cmp` checks above are what actually verify all four offsets. `image_info` is still the right tool for the flash-parameter check in the next step.

- [ ] **Step 7: Verify `--flash_mode keep` preserved the flash parameters**

```bash
python -m esptool image_info out/bootloader.bin | grep -i -E "flash (size|freq|mode)"
python -m esptool image_info out/firmware-merged.bin | grep -i -E "flash (size|freq|mode)"
```

Expected: both commands report identical flash mode, frequency, and size. If they differ, `keep` did not work and the merged image will not boot — stop and investigate before continuing.

> If `python -m esptool` isn't available on the host, run these inside the image instead: `docker run --rm solectrac-fw sh -c "python -m esptool image_info .pio/build/rejsacan/firmware-merged.bin"`.

- [ ] **Step 8: Update `esp32-s3/README.md`**

In the "Building with Docker" section, find:

```markdown
```bash
docker run --rm -v "$PWD/out:/out" solectrac-fw
# -> out/{bootloader,partitions,boot_app0,firmware}.bin
```
```

Replace the comment line so it reads:

```markdown
```bash
docker run --rm -v "$PWD/out:/out" solectrac-fw
# -> out/{bootloader,partitions,boot_app0,firmware}.bin
# -> out/firmware-merged.bin   (all four combined, write at 0x0)
```
```

Then, immediately **after** the existing `esptool ... write_flash` code block and its BOOT-0 tip, add this new subsection:

```markdown
### Flash from a browser

`firmware-merged.bin` is the same four images combined into one, so it can be
written in a single shot at offset `0x0`. That is the form a browser-based
flasher wants — no toolchain install, just a USB cable:

1. Open [esp.huhn.me](https://esp.huhn.me) or the
   [Adafruit ESPTool](https://adafruit.github.io/Adafruit_WebSerial_ESPTool/)
   in **desktop Chrome or Edge** (WebSerial is not available in Safari,
   Firefox, or any mobile browser).
2. Click **Connect** and pick the board's USB serial port.
3. Load `firmware-merged.bin` at address `0x0` and flash.

If the browser can't connect, hold **BOOT-0**, tap **RST**, release **BOOT-0**
to force download mode, then retry.

The merged image also works from the command line, and is simpler than the
four-image invocation above:

```bash
~/.venvs/pio/bin/esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX \
    --baud 921600 write_flash 0x0 out/firmware-merged.bin
```

Pre-built merged images are attached to every
[GitHub Release](../../releases), so flashing a board needs no build at all.
```

- [ ] **Step 9: Commit**

```bash
git add esp32-s3/Dockerfile esp32-s3/README.md
git commit -m "Emit a merged flash image from the firmware Docker build

esptool merge_bin combines the four images into one writable at 0x0, which
is what browser flashers need. Merging inside the Dockerfile keeps the flash
offsets in a single place and makes the artifact reproducible locally rather
than CI-only. The four individual images are still emitted unchanged."
```

---

### Task 2: Tag-triggered release workflow

**Files:**
- Create: `.github/workflows/release.yml`
- Modify: `android/README.md` — the "Reproducible Docker build" section (~line 43).

**Interfaces:**
- Consumes from Task 1: `/out/firmware-merged.bin` produced by the `solectrac-fw` image's default CMD.
- Consumes (existing): `/out/app-debug.apk` produced by the `solectrac-android` image's default CMD.
- Produces: a GitHub Release with exactly two assets, `solecan-firmware-rejsacan-$VERSION-merged.bin` and `solecan-android-$VERSION-debug.apk`.

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/release.yml` with exactly this content:

```yaml
name: Release

# Cut a release by pushing a tag:  git tag v1.0.0 && git push origin v1.0.0
#
# workflow_dispatch runs the identical pipeline but publishes a *draft*, so the
# whole thing can be rehearsed without announcing anything. Delete the draft
# afterwards.
#
# Deliberately passes no WiFi build-args: released firmware ships the stock
# AP-only defaults (the board still broadcasts its own hotspot). Do not add
# secrets here.

on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:

# gh release create needs write access to the repo's releases.
permissions:
  contents: write

concurrency:
  group: release-${{ github.ref }}
  cancel-in-progress: false

jobs:
  firmware:
    name: ESP32 firmware
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3

      - name: Resolve short SHA
        id: sha
        run: echo "short=$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"

      # load: true is required — unlike docker-builds.yml, which only proves
      # the image builds, this job has to `docker run` it afterwards, so the
      # image must land in the local daemon rather than only in the cache.
      - name: Build firmware image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: esp32-s3/Dockerfile
          push: false
          load: true
          tags: solectrac-fw:release
          build-args: |
            GIT_SHA=${{ steps.sha.outputs.short }}
          cache-from: type=gha,scope=firmware
          cache-to: type=gha,scope=firmware,mode=max

      - name: Extract flash images
        run: |
          set -euo pipefail
          mkdir -p out
          docker run --rm -v "$PWD/out:/out" solectrac-fw:release
          # Guard the whole contract, not just the file we upload: a missing
          # individual image means the documented four-image flashing path
          # regressed, and that should fail the release too.
          for f in bootloader.bin partitions.bin boot_app0.bin firmware.bin firmware-merged.bin; do
            test -s "out/$f" || { echo "missing or empty: out/$f" >&2; exit 1; }
          done
          ls -l out/

      - uses: actions/upload-artifact@v4
        with:
          name: firmware
          path: out/firmware-merged.bin
          if-no-files-found: error

  android:
    name: Android app
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3

      - name: Resolve short SHA
        id: sha
        run: echo "short=$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"

      - name: Build Android image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: android/Dockerfile
          push: false
          load: true
          tags: solectrac-android:release
          build-args: |
            GIT_SHA=${{ steps.sha.outputs.short }}
          cache-from: type=gha,scope=android
          cache-to: type=gha,scope=android,mode=max

      - name: Extract APK
        run: |
          set -euo pipefail
          mkdir -p out
          docker run --rm -v "$PWD/out:/out" solectrac-android:release
          test -s out/app-debug.apk || { echo "missing or empty: out/app-debug.apk" >&2; exit 1; }
          ls -l out/

      - uses: actions/upload-artifact@v4
        with:
          name: android
          path: out/app-debug.apk
          if-no-files-found: error

  release:
    name: Publish release
    needs: [firmware, android]
    runs-on: ubuntu-latest
    steps:
      # Both artifacts land in the same directory; their filenames don't collide.
      - uses: actions/download-artifact@v4
        with:
          name: firmware
          path: dist
      - uses: actions/download-artifact@v4
        with:
          name: android
          path: dist

      - name: Resolve version
        id: v
        run: |
          set -euo pipefail
          if [ "${{ github.event_name }}" = "push" ]; then
            # Tag push: the tag is the version, and it already exists.
            echo "version=${{ github.ref_name }}" >> "$GITHUB_OUTPUT"
            echo "extra=" >> "$GITHUB_OUTPUT"
          else
            # Rehearsal: no tag exists, so --target tells gh which commit to
            # tag if the draft is ever published.
            echo "version=dev-$(echo '${{ github.sha }}' | cut -c1-7)" >> "$GITHUB_OUTPUT"
            echo "extra=--draft --target ${{ github.sha }}" >> "$GITHUB_OUTPUT"
          fi

      - name: Name the assets
        run: |
          set -euo pipefail
          V='${{ steps.v.outputs.version }}'
          test -s dist/firmware-merged.bin
          test -s dist/app-debug.apk
          mv dist/firmware-merged.bin "dist/solecan-firmware-rejsacan-$V-merged.bin"
          mv dist/app-debug.apk       "dist/solecan-android-$V-debug.apk"
          ls -l dist/

      # One gh call with both assets: the release either appears complete or
      # does not appear.
      - name: Create the release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          V='${{ steps.v.outputs.version }}'
          gh release create "$V" ${{ steps.v.outputs.extra }} \
            --repo "$GITHUB_REPOSITORY" \
            --title "$V" \
            --generate-notes \
            "dist/solecan-firmware-rejsacan-$V-merged.bin" \
            "dist/solecan-android-$V-debug.apk"
```

- [ ] **Step 2: Check the YAML parses**

```bash
python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml')); print('YAML OK')"
```

Expected: `YAML OK`.

- [ ] **Step 3: Update `android/README.md`**

Find this paragraph in the "Reproducible Docker build" section:

```markdown
The Docker build only produces the debug APK — release would need a
signing config that isn't checked in.
```

Replace it with:

```markdown
The Docker build only produces the debug APK — release would need a
signing config that isn't checked in. The APKs attached to
[GitHub Releases](../../releases) are this same debug-signed build, so
sideloading one needs no build and no keystore.
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml android/README.md
git commit -m "Publish firmware and APK to GitHub Releases on tag push

Two parallel Docker build jobs feed a third that names the assets and
calls gh release create. workflow_dispatch runs the same pipeline into a
draft so it can be rehearsed. No WiFi build-args: released firmware ships
the stock AP-only defaults."
```

- [ ] **Step 5: Push the branch and rehearse the pipeline**

Push the branch, then trigger the workflow manually against it:

```bash
git push -u origin HEAD
gh workflow run release.yml --ref "$(git rev-parse --abbrev-ref HEAD)"
gh run watch
```

Expected: all three jobs succeed.

> `workflow_dispatch` on a branch only works once `release.yml` exists on that branch — which it now does. If `gh workflow run` reports the workflow doesn't exist, confirm the push landed.

- [ ] **Step 6: Inspect the draft release**

```bash
gh release list --limit 5
gh release view "dev-$(git rev-parse --short=7 HEAD)"
```

Expected: a **draft** release with exactly two assets, named
`solecan-firmware-rejsacan-dev-<sha>-merged.bin` and
`solecan-android-dev-<sha>-debug.apk`. No `manifest.json`, no zip.

- [ ] **Step 7: Confirm the downloaded firmware is a real merged image**

Download the published asset and check it is structurally what it claims to be — a bootloader image at offset 0, the same size class as the local build:

```bash
V="dev-$(git rev-parse --short=7 HEAD)"
gh release download "$V" --pattern '*-merged.bin' --dir /tmp/relcheck
ls -l /tmp/relcheck/ out/firmware-merged.bin
python -m esptool image_info "/tmp/relcheck/solecan-firmware-rejsacan-$V-merged.bin" | head -20
```

Expected: the downloaded file is non-trivial (over 1 MB) and within a few hundred bytes of the local `out/firmware-merged.bin` from Task 1, and `image_info` parses it and reports flash mode/size matching Task 1 Step 7.

> Don't `cmp` it against the local build expecting a byte-for-byte match — Arduino-ESP32 builds are not bit-reproducible across machines (embedded build timestamps and toolchain paths differ), so a mismatch here would be normal and would tell you nothing. The offset-level correctness was already proven in Task 1 Step 6; this step only confirms the pipeline shipped the right *kind* of file under the right name.

- [ ] **Step 8: Delete the rehearsal draft**

```bash
gh release delete "dev-$(git rev-parse --short=7 HEAD)" --yes
```

Expected: the draft is gone. Drafts don't create tags, so nothing is left behind.

---

## Acceptance gate before the first real tag

Everything above can be done at a desk. This one needs the bench, and per the
spec it gates the first real release:

- [ ] Flash `firmware-merged.bin` to a RejsaCAN-ESP32-S3 from desktop Chrome via
      [esp.huhn.me](https://esp.huhn.me) at offset `0x0`.
- [ ] Confirm the board boots, broadcasts its AP, and serves the dashboard.
- [ ] Confirm `/json` reports the expected `version` (the short SHA passed as
      `GIT_SHA`).

Once that passes, cut the release:

```bash
git tag v1.0.0 && git push origin v1.0.0
```
