# syntax=docker/dockerfile:1.7
# Security: keep uv pinned so dependency age enforcement stays stable in container builds.
FROM ghcr.io/astral-sh/uv:0.12.3 AS uv

FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm AS builder

ARG DATA_CHORD_INCLUDE_LOCAL_INFERENCE=false

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=secret,id=github_token \
    github_token="$(cat /run/secrets/github_token)" \
    && git config --global url."https://x-access-token:${github_token}@github.com/".insteadOf "https://github.com/" \
    && case "$DATA_CHORD_INCLUDE_LOCAL_INFERENCE" in \
        true) uv sync --frozen --no-dev --extra local-inference ;; \
        false) uv sync --frozen --no-dev ;; \
        *) echo "DATA_CHORD_INCLUDE_LOCAL_INFERENCE must be true or false" >&2; exit 2 ;; \
    esac \
    && git config --global --unset-all url."https://x-access-token:${github_token}@github.com/".insteadOf

FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm AS runtime

ENV DATA_CHORD_UPLOAD_DIR="/tmp/data-chord/uploads" \
    DATA_CHORD_DATA_DIR="/data" \
    FORWARDED_ALLOW_IPS="*" \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir /data \
    && chown appuser:appuser /data

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY backend /app/backend
COPY config /app/config
COPY demo /app/demo
COPY scripts /app/scripts
COPY src /app/src
COPY pyproject.toml README.md /app/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
