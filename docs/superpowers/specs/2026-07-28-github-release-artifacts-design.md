# GitHub Release artifacts for firmware and Android — design

**Date:** 2026-07-28
**Target:** `.github/workflows/release.yml` (new), `esp32-s3/Dockerfile`, `esp32-s3/README.md`, `android/README.md`
**Status:** approved design, pre-implementation

## Goal

Publish a GitHub Release, on tag push, carrying two installable binaries:

- an **ESP32-S3 firmware image that can be flashed from a browser** — a single
  merged `.bin` written at offset 0, plus an ESP Web Tools `manifest.json`;
- the **Android dashboard APK**.

Today both Dockerfiles produce artifacts locally, but nothing packages them for
distribution, and the firmware ships as four separate images at four offsets —
usable only from the `esptool` command line, not from a browser flasher.

## Background

`esp32-s3/Dockerfile` builds the `rejsacan` env and copies four images to `/out`:
`bootloader.bin`, `partitions.bin`, `boot_app0.bin`, `firmware.bin`. The README
documents flashing them at the standard Arduino-ESP32 offsets:

| Offset | Image |
|---|---|
| `0x0` | `bootloader.bin` |
| `0x8000` | `partitions.bin` |
| `0xe000` | `boot_app0.bin` |
| `0x10000` | `firmware.bin` |

`android/Dockerfile` builds `app-debug.apk` via `gradle :app:assembleDebug`.

The firmware has **no OTA endpoint** — there is no `Update`/`/update` handler in
`src/main.cpp`. So "upload via the web UI" means a *browser-based serial
flasher* (ESP Web Tools, esp.huhn.me, Adafruit ESPTool) over WebSerial, not an
upload to the device's own dashboard. Browser flashers want one file at offset
0, which is what `esptool merge_bin` produces.

`.github/workflows/docker-builds.yml` already builds both images on every push
and PR as a build-only check, with GHA cache scopes `firmware` and `android`.

## Decisions

These were settled during brainstorming and constrain everything below:

- **CI-driven, not a local script.** Releases are cut by pushing a tag. There is
  no `bin/release.sh`; the only new invocation path is the workflow.
- **`rejsacan` only.** The other two envs (`lilygo_t2can`,
  `adafruit_feather_s3`) are not released. `esp32-s3/Dockerfile` keeps its
  hardcoded `pio run -e rejsacan`; no board parameterization.
- **No WiFi secrets.** The release firmware builds with *no* WiFi build-args, so
  it carries the stock AP-only defaults — the stable field default. `GIT_SHA` is
  the only build-arg passed.
- **Debug-signed APK.** Ship `app-debug.apk` as the existing Dockerfile builds
  it. No keystore, no signing secrets.
- **Merged bin + manifest, no hosted installer page.** No GitHub Pages job.

## Architecture

### Where the merge happens

The merge happens **inside `esp32-s3/Dockerfile`**, not in the workflow and not
in a helper script. The workflow only runs the existing image and collects
`/out`.

Rationale: the four flash offsets are a protocol fact about the board, and they
must live in exactly one place. Merging in the workflow would duplicate them
outside the image and make the merged binary a CI-only artifact that cannot be
reproduced or debugged locally. With the merge in the Dockerfile,
`docker run --rm -v "$PWD/out:/out" solectrac-fw` on a developer machine yields
the byte-identical web-flashable image that CI ships. This is the same
single-source discipline the repo already applies to `dashboard.html`.

### `esp32-s3/Dockerfile` changes

1. Install a **pinned** esptool alongside PlatformIO:
   `pip install --no-cache-dir "esptool==4.8.1"`. Pinning is load-bearing —
   esptool 5.x renamed the subcommand from `merge_bin` to `merge-bin`, so an
   unpinned install would break the build on the next major release.

2. After `RUN pio run -e rejsacan`, add a merge step:

   ```
   esptool.py --chip esp32s3 merge_bin -o .pio/build/rejsacan/firmware-merged.bin \
       --flash_mode keep --flash_freq keep --flash_size keep \
       0x0     .pio/build/rejsacan/bootloader.bin \
       0x8000  .pio/build/rejsacan/partitions.bin \
       0xe000  "$(find /root/.platformio -name boot_app0.bin | head -1)" \
       0x10000 .pio/build/rejsacan/firmware.bin
   ```

   `keep` for all three flash parameters inherits the mode, frequency, and size
   already baked into the bootloader header by the build. Hardcoding
   `dio`/`80m` here would silently produce an image that does not boot if the
   `esp32s3_flash_16MB` board definition or the env's `qio_opi` memory type ever
   changes. `keep` cannot drift.

3. Extend the `CMD` copy list with `firmware-merged.bin`, so `/out` gains a
   fifth file. The existing four are unchanged — the documented `esptool
   write_flash` path keeps working exactly as it does today.

### `.github/workflows/release.yml`

Triggers:

- `push` on tags matching `v*` — publishes a normal (non-draft) release;
- `workflow_dispatch` — builds everything and publishes a **draft** release, so
  the pipeline can be rehearsed without announcing anything.

Version string, computed once in the `release` job and used for every asset
name and for `manifest.json`:

- tag push → the tag name, e.g. `v1.2.0`;
- dispatch → `dev-<short-sha>`.

Three jobs:

| Job | Does |
|---|---|
| `firmware` | checkout → buildx → build `esp32-s3/Dockerfile` (context `.`, `--build-arg GIT_SHA=<short sha>`, cache scope `firmware`) → `docker run` to extract `/out` → upload workflow artifact |
| `android` | checkout → buildx → build `android/Dockerfile` (context `.`, `--build-arg GIT_SHA=<short sha>`, cache scope `android`) → `docker run` to extract `/out` → upload workflow artifact |
| `release` | `needs: [firmware, android]` → download both artifacts → rename to final asset names → zip the raw esptool set → write `manifest.json` → `gh release create` |

`firmware` and `android` run in parallel: one image pulls the Xtensa toolchain
and the other the whole Android SDK, and the two have no relationship. Both
reuse the cache scopes `docker-builds.yml` already populates, so tag builds
usually hit warm caches.

`docker-builds.yml` is **not modified**. It stays the per-PR build check.

### Release assets

With `$VERSION` as defined above:

| Asset | Purpose |
|---|---|
| `solecan-firmware-rejsacan-$VERSION-merged.bin` | browser flasher, write at offset 0 |
| `manifest.json` | ESP Web Tools |
| `solecan-firmware-rejsacan-$VERSION-esptool.zip` | the four raw images, for the documented `esptool write_flash` path |
| `solecan-android-$VERSION-debug.apk` | sideload |

`manifest.json`:

```json
{
  "name": "Solectrac CAN Monitor (RejsaCAN-ESP32-S3)",
  "version": "$VERSION",
  "new_install_prompt_erase": true,
  "builds": [
    {
      "chipFamily": "ESP32-S3",
      "parts": [
        { "path": "solecan-firmware-rejsacan-$VERSION-merged.bin", "offset": 0 }
      ]
    }
  ]
}
```

The `path` is a bare filename. Both files land in the same
`releases/download/<tag>/` directory, so ESP Web Tools resolves it relative to
the manifest URL. The manifest is generated in the `release` job from the same
`$VERSION` variable that names the binary, so the two cannot disagree.

`new_install_prompt_erase: true` offers a full-chip erase on first install.
That matters because the firmware persists runtime STA WiFi credentials in
NVS; a user reflashing a board handed to them by someone else should be able to
clear them.

## Error handling

- Any Docker build failure fails its job; `release` never runs, so no partial
  release is published.
- The `release` job asserts every expected input file exists before calling
  `gh release create`, and fails loudly if one is missing — a silently
  half-populated release is worse than no release.
- `gh release create` runs once with all four assets, so the release either
  appears complete or does not appear.
- Re-tagging an existing version is not special-cased: `gh release create`
  fails on a duplicate tag, which is the correct outcome.

## Testing

The repo has no unit tests and nothing else runs in CI; verification is by
inspection of real artifacts.

1. **Local, before pushing anything:** build the firmware image and extract
   `/out`. Confirm `firmware-merged.bin` exists and that
   `esptool image_info` on it reports segments at `0x0`, `0x8000`, `0xe000`, and
   `0x10000`. Confirm the merged file's size is consistent with the four parts
   (padded to the `0x10000` offset plus the firmware image).
2. **Confirm the flash parameters survived the merge:** `esptool image_info` on
   `firmware-merged.bin` must report the same flash mode / frequency / size as
   `image_info` on the standalone `bootloader.bin`. This is the check that
   `--flash_mode keep` did what it claims.
3. **Confirm no regression:** the original four images are still copied to
   `/out` and are byte-identical to a pre-change build.
4. **Pipeline rehearsal:** run the workflow via `workflow_dispatch`, confirm a
   draft release appears with all four assets and that `manifest.json`'s `path`
   matches the uploaded binary's name.
5. **Hardware confirmation:** flash the merged binary to a RejsaCAN board from a
   browser flasher at offset 0 and confirm the board boots, serves the
   dashboard, and reports the expected `GIT_SHA`.

Steps 1–4 are mandatory before the change is called done. Step 5 needs bench
hardware and is the acceptance gate before the first real tag.

## Documentation

- `esp32-s3/README.md` — add a "Flash from a browser" section covering the
  merged binary at offset 0, placed alongside the existing `esptool
  write_flash` instructions, and note the fifth file now appearing in `/out`.
- `android/README.md` — note that release APKs are published on the Releases
  page and are debug-signed.
- `CLAUDE.md` — no change needed; the release workflow is not a mental-model
  fact about the two CAN buses.

## Out of scope

- Any board other than `rejsacan`.
- A GitHub Pages installer page with a one-click `<esp-web-install-button>`.
- Release-signed APKs and the keystore secrets they require.
- OTA update support in the firmware.
- Publishing the Python tooling.
