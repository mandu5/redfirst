# Runner for the replay study: Debian trixie (glibc 2.41, so manylinux_2_39
# aarch64 wheels install) with build tools, git, Python 3.12 and a recent uv.
# Other Python versions are fetched by uv on demand.
# v1 used ghcr.io/astral-sh/uv:python3.12-bookworm (glibc 2.36, uv 0.9.30).
FROM ghcr.io/astral-sh/uv:python3.12-trixie@sha256:a65858371a89e4e543d2018d2edc33b463b7ae3541a66f144e97ed431ebc8fb0
# Shared libraries that common Python packages load at import time: Qt and
# OpenCV (GL, EGL, xkb, fontconfig, dbus), OpenMP, libsndfile, libmagic,
# ffmpeg and graphviz binaries. Qt runs headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libegl1 libglib2.0-0t64 libxkbcommon0 libxkbcommon-x11-0 \
        libfontconfig1 libdbus-1-3 libgomp1 libsndfile1 libmagic1 \
        ffmpeg graphviz \
    && rm -rf /var/lib/apt/lists/*
ENV UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    QT_QPA_PLATFORM=offscreen \
    MPLBACKEND=Agg \
    LANG=C.UTF-8
