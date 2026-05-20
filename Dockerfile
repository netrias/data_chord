# Security: keep uv pinned so dependency age enforcement stays stable in container builds.
FROM ghcr.io/astral-sh/uv:0.10.0 AS uv

FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm AS builder

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

FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
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

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
