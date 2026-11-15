# Runner for the replay study: Debian bookworm with build tools, git,
# Python 3.12 and uv. Other Python versions are fetched by uv on demand.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm
ENV UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8
