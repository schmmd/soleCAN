"""Inject the source git SHA (and release tag, if any) as compile-time defines.

GIT_SHA — always present. Resolution order:
  1. `GIT_SHA` env var (the path used by the Docker build, which has no .git).
  2. `git rev-parse --short HEAD` against the project's parent repo (native pio).
  3. "unknown" — never fails the build.
If the working tree is dirty (only checked when running git locally) a "-dirty"
suffix is appended so a flashed board can be distinguished from a clean build.

GIT_VERSION — the release tag, non-empty only when this commit *is* a tagged
release (so a normal build shows just the SHA, a release build shows both):
  1. `GIT_VERSION` env var (CI passes the tag on tag builds; may be empty).
  2. `git describe --tags --exact-match` (native) — empty when HEAD isn't a tag.
  3. "" — no release version.
"""
import os
import subprocess
from pathlib import Path

Import("env")  # noqa: F821 — provided by PlatformIO


def _resolve_sha() -> str:
    env_sha = os.environ.get("GIT_SHA")
    if env_sha:
        return env_sha
    repo = Path(env["PROJECT_DIR"]).parent.parent  # noqa: F821
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    dirty = subprocess.call(
        ["git", "-C", str(repo), "diff", "--quiet"],
        stderr=subprocess.DEVNULL,
    ) != 0
    return sha + ("-dirty" if dirty else "")


def _resolve_version() -> str:
    env_ver = os.environ.get("GIT_VERSION")
    if env_ver is not None:
        return env_ver.strip()  # CI/Docker: the tag on release builds, else ""
    repo = Path(env["PROJECT_DIR"]).parent.parent  # noqa: F821
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "describe", "--tags", "--exact-match"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""  # not on a tag (or no git) — no release version


env.Append(CPPDEFINES=[  # noqa: F821
    ("GIT_SHA", env.StringifyMacro(_resolve_sha())),          # noqa: F821
    ("GIT_VERSION", env.StringifyMacro(_resolve_version())),  # noqa: F821
])
