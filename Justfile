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
	uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8001

app-reload:
	uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8001 --reload

js-test:
	cd tests/js && npm test
