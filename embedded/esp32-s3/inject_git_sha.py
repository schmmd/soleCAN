"""Inject the source git SHA as a compile-time define.

Resolution order:
  1. `GIT_SHA` env var (the path used by the Docker build, which has no .git).
  2. `git rev-parse --short HEAD` against the project's parent repo (native pio).
  3. "unknown" — never fails the build.

If the working tree is dirty (only checked when running git locally) a "-dirty"
suffix is appended so a flashed board can be distinguished from a clean build.
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


env.Append(CPPDEFINES=[("GIT_SHA", env.StringifyMacro(_resolve_sha()))])  # noqa: F821
