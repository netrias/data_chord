set shell := ["bash", "-cu"]

default:
	@just --list

sync:
	# Security: use the committed lockfile for normal dependency installs.
	uv sync --frozen --extra dev

lint:
	uv run ruff check .

dead-code:
	uv run vulture

typecheck:
	uv run basedpyright

test:
	uv run pytest

test-e2e:
	npm run test:e2e

perf-e2e:
	npm run perf:e2e

perf-staging target="bdf" base_url="":
	@set -euo pipefail; \
	url="{{base_url}}"; \
	if [ -z "$url" ]; then url="${DATA_CHORD_STAGING_URL:-}"; fi; \
	if [ -z "$url" ]; then \
		url="$(infra/scripts/deploy.sh {{target}} staging output-url)"; \
	fi; \
	echo "Running staging performance journey against $url"; \
	PLAYWRIGHT_BASE_URL="$url" npm run perf:staging

e2e-install:
	# Security: npm ci enforces the lockfile and .npmrc package age gate.
	npm ci
	./node_modules/.bin/playwright install

app:
	uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

app-reload:
	DEV_MODE=true uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload --reload-exclude .venv

js-test:
	npm test

# Syntax-check all frontend JavaScript files (catches duplicate declarations, syntax errors)
js-check:
	@echo "Checking JavaScript syntax..."
	@find src -path '*/static/*.js' -exec node --check {} \;
	@echo "All JavaScript files pass syntax check"

infra-fmt:
	tofu -chdir=infra fmt -recursive

infra-validate:
	tofu -chdir=infra validate

infra-test:
	tofu -chdir=infra test
	bash infra/tests/deployment_contract_test.sh
	bash infra/tests/deployment_flow_test.sh
	bash infra/tests/secret_preparation_test.sh
	bash -n infra/scripts/*.sh infra/tests/*.sh

# Save and show a read-only OpenTofu plan.
plan target stage profile:
	AWS_PROFILE={{profile}} infra/scripts/deploy.sh {{target}} {{stage}} plan

# Build the pushed commit, apply the displayed saved plan, and verify ECS health.
deploy target stage profile:
	AWS_PROFILE={{profile}} infra/scripts/deploy.sh {{target}} {{stage}} deploy

# Show the deployed application and ECS service status.
status target stage profile:
	AWS_PROFILE={{profile}} infra/scripts/deploy.sh {{target}} {{stage}} status
