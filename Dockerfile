# Reproducible runtime environment for the host-side Python tooling
# (solecan-analyze.py and solecan-stream.py). Installs the base dependency
# set from pyproject.toml — no extras — which is enough for both the
# offline analyzer and the live/replay TUI/web dashboard.
#
#   docker build -t solectrac-py .
#
# Example use (offline decode of captures mounted from the host):
#
#   docker run --rm -v "$PWD/captures:/data" solectrac-py \
#       python solecan-analyze.py -o /data/out /data/session.asc
#
FROM python:3.14-slim

WORKDIR /project

# Install the base dependency set (python-can, pyserial, rich) from
# pyproject.toml's [project].dependencies. The `ble` and `canalyst` extras
# are intentionally omitted — neither script needs them.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

# Only the files the two scripts actually need. dashboard.html is required
# by solecan-stream.py when run with --ui web (it serves the file).
COPY solecan-analyze.py solecan-stream.py solecan_proto.py dashboard.html ./

# Smoke-check that both scripts parse and that every top-level `import` /
# `from … import …` actually resolves against the installed deps. Failing
# here turns dependency drift or syntax regressions into a docker build
# failure, which is exactly what CI wants to see.
RUN python -m py_compile solecan_proto.py solecan-analyze.py solecan-stream.py \
    && python solecan-analyze.py --help > /dev/null \
    && python solecan-stream.py --help > /dev/null

CMD ["python", "solecan-stream.py", "--help"]
