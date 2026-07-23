#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../scripts/lib.sh
source "$TEST_DIR/../scripts/lib.sh"

assert_equal() {
  local expected="$1"
  local actual="$2"
  local description="$3"

  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL: %s\nExpected: %s\nActual:   %s\n' "$description" "$expected" "$actual" >&2
    exit 1
  fi
}

assert_fails_with() {
  local expected_message="$1"
  shift
  local output

  if output="$("$@" 2>&1)"; then
    printf 'FAIL: command unexpectedly succeeded: %s\n' "$*" >&2
    exit 1
  fi
  if [[ "$output" != *"$expected_message"* ]]; then
    printf 'FAIL: expected error containing %q, got:\n%s\n' "$expected_message" "$output" >&2
    exit 1
  fi
}

assert_equal "strides" "$(require_deployment_target strides)" "STRIDES target resolves"
assert_equal "netrias" "$(require_deployment_target netrias)" "Netrias target resolves"
assert_equal "staging" "$(require_stage_name staging)" "staging stage resolves"
assert_equal "prod" "$(require_stage_name prod)" "prod stage resolves"

assert_fails_with "Choose a deployment target" require_deployment_target ../netrias
assert_fails_with "Choose a deployment target" require_deployment_target unknown
assert_fails_with "Choose a stage" require_stage_name development

assert_equal \
  "$INFRA_DIR/env/strides/staging.tfvars" \
  "$(stage_tfvars_path strides staging)" \
  "STRIDES staging config path"
assert_equal \
  "$INFRA_DIR/env/netrias/prod.backend.hcl" \
  "$(backend_config_path netrias prod)" \
  "Netrias production backend path"
assert_equal \
  "vpc-08c111f13ad3e8b44" \
  "$(deployment_tfvar_value netrias staging vpc_id)" \
  "Netrias staging inherits its target VPC"
assert_equal \
  "data-chord/prod/netrias-api-key" \
  "$(deployment_tfvar_value netrias prod netrias_api_key_secret_name)" \
  "Netrias production reads its stage secret"
assert_equal \
  "netrias-data-chord-tofu-state-084828580051-us-east-2" \
  "$(backend_value "$(backend_config_path strides staging)" bucket)" \
  "STRIDES keeps its existing state bucket"
assert_equal \
  "netrias-data-chord-tofu-state-945365518758-us-east-2" \
  "$(backend_value "$(backend_config_path netrias staging)" bucket)" \
  "Netrias uses its account-specific state bucket"
assert_equal \
  "data-chord/prod/tofu.tfstate" \
  "$(backend_value "$(backend_config_path netrias prod)" key)" \
  "Netrias production uses isolated state"

export AWS_PROFILE="test-profile"
MOCK_AWS_ACCOUNT_ID="084828580051"
aws() {
  printf '%s\n' "$MOCK_AWS_ACCOUNT_ID"
}

assert_expected_aws_account strides

MOCK_AWS_ACCOUNT_ID="945365518758"
assert_expected_aws_account netrias
assert_fails_with "requires AWS account 084828580051" assert_expected_aws_account strides

unset AWS_PROFILE
assert_fails_with "Set AWS_PROFILE" require_aws_profile

printf 'Deployment configuration tests passed\n'
