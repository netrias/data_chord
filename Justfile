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

perf-staging base_url="":
	@set -euo pipefail; \
	url="{{base_url}}"; \
	if [ -z "$url" ]; then url="${DATA_CHORD_STAGING_URL:-}"; fi; \
	if [ -z "$url" ]; then \
		tofu -chdir=infra init -backend-config=env/staging.backend.hcl -input=false >/dev/null; \
		url="$(tofu -chdir=infra output -raw app_url)"; \
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
	cd tests/js && npm test

# Syntax-check all frontend JavaScript files (catches duplicate declarations, syntax errors)
js-check:
	@echo "Checking JavaScript syntax..."
	@find src -path '*/static/*.js' -exec node --check {} \;
	@echo "All JavaScript files pass syntax check"

infra-fmt:
	tofu -chdir=infra fmt -recursive

infra-validate:
	tofu -chdir=infra validate

infra-plan env:
	infra/scripts/deploy.sh {{env}} plan

infra-apply env:
	infra/scripts/deploy.sh {{env}} deploy-infra

deploy env:
	infra/scripts/deploy.sh {{env}}

deploy-app env:
	infra/scripts/deploy.sh {{env}} deploy-app

deploy-infra env:
	infra/scripts/deploy.sh {{env}} deploy-infra

deploy-plan env:
	infra/scripts/deploy.sh {{env}} plan

deploy-status env:
	infra/scripts/deploy.sh {{env}} status

deploy-logs env:
	infra/scripts/deploy.sh {{env}} logs

deploy-build env:
	infra/scripts/deploy.sh {{env}} build

invite-user env email:
	infra/scripts/invite-cognito-user.sh {{env}} {{email}}

resend-user-invite env email:
	infra/scripts/invite-cognito-user.sh {{env}} {{email}} resend
