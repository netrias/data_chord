# Security: keep uv pinned so dependency age enforcement stays stable in container builds.
FROM ghcr.io/astral-sh/uv:0.13.4 AS uv

FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm AS base

# DLA-4845-1: refresh OS trust and reject stale CA packages.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge '20250419~deb12u2' \
    && rm -rf /var/lib/apt/lists/*
# uv owns build isolation; the application does not need the base image's
# installers, whose vendored trust bundles may lag behind system certificates.
RUN /usr/local/bin/python -I - <<'PYTHON'
import importlib.util
import shutil
import sys
import sysconfig
from pathlib import Path

packages = Path(sysconfig.get_path("purelib"))
for path in [packages / "pip", *packages.glob("pip-*.dist-info"), Path(sysconfig.get_path("stdlib")) / "ensurepip"]:
    shutil.rmtree(path, ignore_errors=True)
for name in ("pip", "pip3", f"pip{sys.version_info.major}.{sys.version_info.minor}"):
    (Path(sysconfig.get_path("scripts")) / name).unlink(missing_ok=True)
assert importlib.util.find_spec("pip") is None
assert importlib.util.find_spec("ensurepip") is None
PYTHON

FROM base AS builder

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM base AS runtime

ENV DATA_CHORD_UPLOAD_DIR="/tmp/data-chord/uploads" \
    FORWARDED_ALLOW_IPS="*" \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY backend /app/backend
COPY src /app/src
COPY pyproject.toml README.md /app/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
