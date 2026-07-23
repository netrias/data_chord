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

perf-staging target base_url="":
	@set -euo pipefail; \
	url="{{base_url}}"; \
	if [ -z "$url" ]; then url="${DATA_CHORD_STAGING_URL:-}"; fi; \
	if [ -z "$url" ]; then \
		tofu -chdir=infra init -backend-config=env/{{target}}/staging.backend.hcl -input=false >/dev/null; \
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

infra-config-test:
	bash infra/tests/deployment-config-test.sh

infra-validate:
	bash infra/tests/deployment-config-test.sh
	tofu -chdir=infra validate

infra-plan target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} plan

infra-apply target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} deploy-infra

deploy target stage:
	infra/scripts/deploy.sh {{target}} {{stage}}

deploy-app target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} deploy-app

deploy-infra target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} deploy-infra

deploy-plan target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} plan

deploy-status target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} status

deploy-logs target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} logs

deploy-build target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} build

invite-user target stage email:
	infra/scripts/invite-cognito-user.sh {{target}} {{stage}} {{email}}

resend-user-invite target stage email:
	infra/scripts/invite-cognito-user.sh {{target}} {{stage}} {{email}} resend
