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

infra-test:
	tofu -chdir=infra test
	bash infra/tests/deployment_contract_test.sh
	bash infra/tests/deployment_flow_test.sh
	bash -n infra/scripts/*.sh infra/tests/*.sh

# Prepare or update the stage API secret. target=bdf|netrias; stage=dev|qa|staging|prod; AWS_PROFILE is required; NETRIAS_API_KEY creates or updates.
prepare-stage-secret target stage:
	infra/scripts/bootstrap-secrets.sh {{target}} {{stage}} ensure

# Deploy the app. target=bdf|netrias; stage=dev|qa|staging|prod; AWS_PROFILE is required.
deploy target stage:
	infra/scripts/deploy.sh {{target}} {{stage}}

# Apply infrastructure while keeping the deployed image. target=bdf|netrias; stage=dev|qa|staging|prod; AWS_PROFILE is required.
deploy-infra target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} deploy-infra

# Plan infrastructure. target=bdf|netrias; stage=dev|qa|staging|prod; AWS_PROFILE is required.
deploy-plan target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} plan

# Show deployment status. target=bdf|netrias; stage=dev|qa|staging|prod; AWS_PROFILE is required.
deploy-status target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} status

# Show deployment logs. target=bdf|netrias; stage=dev|qa|staging|prod; AWS_PROFILE is required.
deploy-logs target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} logs

# Build the current commit without an application apply. target=bdf|netrias; stage=dev|qa|staging|prod; AWS_PROFILE is required.
deploy-build target stage:
	infra/scripts/deploy.sh {{target}} {{stage}} build

# Invite a user. target=bdf|netrias; stage=dev|qa|staging|prod; AWS_PROFILE is required.
invite-user target stage email:
	infra/scripts/invite-cognito-user.sh {{target}} {{stage}} {{email}}

# Resend an invite. target=bdf|netrias; stage=dev|qa|staging|prod; AWS_PROFILE is required.
resend-user-invite target stage email:
	infra/scripts/invite-cognito-user.sh {{target}} {{stage}} {{email}} resend
