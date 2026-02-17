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

js-test:
	cd tests/js && npm test

# Syntax-check all frontend JavaScript files (catches duplicate declarations, syntax errors)
js-check:
	@echo "Checking JavaScript syntax..."
	@find src -path '*/static/*.js' -exec node --check {} \;
	@echo "All JavaScript files pass syntax check"
