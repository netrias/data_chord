#!/usr/bin/env bash
# Quick dev launcher for backend (FastAPI) + frontend (Vite).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

info() { printf '\033[1;34m[info]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[fail]\033[0m %s\n' "$*"; exit 1; }

command -v uv >/dev/null 2>&1 || fail "uv not found; install via https://astral.sh/uv"
command -v npm >/dev/null 2>&1 || fail "npm not found; install Node.js >= 18"

if [ ! -d .venv ]; then
  info "Creating Python environment via uv sync (first run may take a moment)..."
  uv sync --frozen
else
  info "Syncing Python dependencies (cached)..."
  uv sync --frozen >/dev/null
fi

if [ ! -d frontend/node_modules ]; then
  info "Installing frontend dependencies (npm install)..."
  npm --prefix frontend install
else
  warn "frontend/node_modules exists; skipping npm install (run manually if deps change)."
fi

BACKEND_PORT=${BACKEND_PORT:-8000}
FRONTEND_PORT=${FRONTEND_PORT:-5173}

info "Starting backend on http://localhost:${BACKEND_PORT}"
uv run uvicorn backend.app.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" \
  --log-level info &
BACK_PID=$!

info "Starting frontend (Vite) on http://localhost:${FRONTEND_PORT}"
npm --prefix frontend run dev -- --host --port "$FRONTEND_PORT" &
FRONT_PID=$!

cleanup() {
  warn "Stopping dev servers..."
  kill "$BACK_PID" "$FRONT_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

wait -n "$BACK_PID" "$FRONT_PID"
