"""Copy the repo-root dashboard.html into src/ before the firmware build.

The canonical dashboard HTML lives at the repo root (../../dashboard.html
relative to this PlatformIO project). The Android build copies it into the
APK's assets/ the same way. Keeping the firmware copy out of git avoids the
symlink-vs-real-file drift that bit us repeatedly.

If the shared source isn't present (the Docker build does not copy the repo
root into the image) but a src/dashboard.html is already in place, trust that
the Dockerfile pre-placed it and continue.
"""
import shutil
from pathlib import Path

Import("env")  # noqa: F821 — provided by PlatformIO

_proj = Path(env["PROJECT_DIR"])  # noqa: F821
_shared = _proj.parent.parent / "dashboard.html"
_dest = _proj / "src" / "dashboard.html"

if _shared.exists():
    if not _dest.exists() or _shared.read_bytes() != _dest.read_bytes():
        _dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_shared, _dest)
elif not _dest.exists():
    raise SystemExit(
        f"copy_dashboard.py: no shared dashboard at {_shared} and no "
        f"pre-placed {_dest}; cannot build firmware."
    )
