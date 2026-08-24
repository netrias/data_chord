set shell := ["bash", "-cu"]

default:
	@just --list

sync:
	# Security: use the committed lockfile for normal dependency installs.
	uv sync --frozen --extra dev
	npm ci

lint:
	uv run pre-commit run --all-files --show-diff-on-failure

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

perf-staging base_url="" target="netrias":
	@set -euo pipefail; \
	url="{{base_url}}"; \
	if [ -z "$url" ]; then url="${DATA_CHORD_STAGING_URL:-}"; fi; \
	if [ -z "$url" ]; then \
		url="https://$(python3 infra/scripts/environment.py get environments/{{target}}/staging.json {{target}} staging domain_name)"; \
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

demo:
	bash scripts/run_demo.sh

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
	tofu -chdir=infra/customer-platform validate
	tofu -chdir=infra/modules/data-plane validate

infra-test:
	tofu -chdir=infra test
	tofu -chdir=infra/customer-platform test
	tofu -chdir=infra/modules/data-plane test
	bash infra/tests/deployment_flow_test.sh
	bash -n infra/scripts/*.sh infra/tests/*.sh

# Save and show a read-only deployment forecast.
plan target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} plan

# Apply checked saved plans, build the image, and verify health.
deploy target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} deploy

# Save and show a customer-platform forecast from a bootstrap handoff.
customer-plan target stage handoff:
	infra/scripts/customer-platform-deploy.sh {{target}} {{stage}} plan {{handoff}}

# Apply the checked customer-platform data-plane plan.
customer-deploy target stage handoff:
	infra/scripts/customer-platform-deploy.sh {{target}} {{stage}} deploy {{handoff}}
