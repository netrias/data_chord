set shell := ["bash", "-cu"]

default:
	@just --list

sync:
	uv sync --extra dev

lint:
	uv run ruff check .

typecheck:
	uv run basedpyright

test:
	uv run pytest

test-e2e:
	npm run test:e2e

e2e-install:
	npm install
	npx playwright install

app:
	uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

app-reload:
	DEV_MODE=true uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload --reload-exclude .venv

# Start the app after freeing the target port. Override with: just start 8001
start port="8000":
	@if command -v lsof >/dev/null 2>&1; then \
		pids="$(lsof -ti tcp:{{port}} -sTCP:LISTEN || true)"; \
		if [ -n "$pids" ]; then \
			echo "Stopping process(es) on port {{port}}: $pids"; \
			kill $pids; \
			sleep 1; \
			remaining="$(lsof -ti tcp:{{port}} -sTCP:LISTEN || true)"; \
			if [ -n "$remaining" ]; then \
				echo "Force-stopping process(es) on port {{port}}: $remaining"; \
				kill -9 $remaining; \
				sleep 1; \
			fi; \
		fi; \
	else \
		echo "lsof is not installed; cannot clear port {{port}} before start."; \
	fi
	uv run uvicorn backend.app.main:app --host 0.0.0.0 --port {{port}}

js-test:
	cd tests/js && npm test

# Syntax-check all frontend JavaScript files (catches duplicate declarations, syntax errors)
js-check:
	@echo "Checking JavaScript syntax..."
	@find src -path '*/static/*.js' -exec node --check {} \;
	@echo "All JavaScript files pass syntax check"
