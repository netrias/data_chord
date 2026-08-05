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
    return 1
  fi
}

assert_fails_with() {
  local expected_message="$1"
  shift
  local output

  if output="$("$@" 2>&1)"; then
    printf 'FAIL: command succeeded but should fail: %s\n' "$*" >&2
    return 1
  fi
  [[ "$output" == *"$expected_message"* ]] || {
    printf 'FAIL: expected error containing %q, got: %s\n' "$expected_message" "$output" >&2
    return 1
  }
}

test_supported_targets() {
  assert_equal "bdf" "$(require_target_name bdf)" "BDF target"
  assert_equal "netrias" "$(require_target_name netrias)" "Netrias target"
  assert_fails_with "Choose a target" require_target_name other
}

test_supported_stages() {
  local stage_name

  for stage_name in dev qa staging prod; do
    assert_equal "$stage_name" "$(require_stage_name "$stage_name")" "stage identifier $stage_name"
  done
  assert_fails_with "Choose a stage" require_stage_name test
}

test_configured_deployments_are_separate_from_stage_names() {
  require_configured_deployment bdf staging
  require_configured_deployment bdf prod
  require_configured_deployment netrias staging

  assert_fails_with "not configured for bdf/dev" require_configured_deployment bdf dev
  assert_fails_with "not configured for netrias/prod" require_configured_deployment netrias prod
}

test_state_keys_preserve_only_live_legacy_states() {
  assert_equal "data-chord/staging/tofu.tfstate" "$(state_key_for bdf staging)" "BDF staging state key"
  assert_equal "data-chord/prod/tofu.tfstate" "$(state_key_for bdf prod)" "BDF production state key"
  assert_equal "datachord/bdf/dev/tofu.tfstate" "$(state_key_for bdf dev)" "new BDF state key"
  assert_equal "datachord/netrias/prod/tofu.tfstate" "$(state_key_for netrias prod)" "new Netrias state key"
}

test_deployer_identity_is_required() {
  aws() {
    printf '%s\t%s\n' "${MOCK_ACCOUNT_ID}" "${MOCK_CALLER_ARN}"
  }

  AWS_PROFILE=test \
    MOCK_ACCOUNT_ID=084828580051 \
    MOCK_CALLER_ARN=arn:aws:sts::084828580051:assumed-role/datachord-deployer/session \
    require_deployer_identity bdf

  assert_fails_with "not target account" env \
    AWS_PROFILE=test MOCK_ACCOUNT_ID=945365518758 \
    MOCK_CALLER_ARN=arn:aws:sts::945365518758:assumed-role/datachord-deployer/session \
    bash -c 'source "$1"; aws() { printf "%s\t%s\n" "$MOCK_ACCOUNT_ID" "$MOCK_CALLER_ARN"; }; require_deployer_identity bdf' \
    _ "$TEST_DIR/../scripts/lib.sh"

  assert_fails_with "must assume" env \
    AWS_PROFILE=test MOCK_ACCOUNT_ID=084828580051 \
    MOCK_CALLER_ARN=arn:aws:iam::084828580051:user/operator \
    bash -c 'source "$1"; aws() { printf "%s\t%s\n" "$MOCK_ACCOUNT_ID" "$MOCK_CALLER_ARN"; }; require_deployer_identity bdf' \
    _ "$TEST_DIR/../scripts/lib.sh"
}

test_backend_uses_contract_and_native_lock_file() {
  local tofu_calls
  tofu_calls="$(mktemp)"
  tofu() {
    printf '%s\n' "$@" >"$tofu_calls"
  }

  DATA_CHORD_TF_DATA_DIR="$(mktemp -d)" init_tofu netrias staging

  assert_equal "1" "$(grep -Fxc -- '-backend-config=bucket=netrias-data-chord-tofu-state-945365518758-us-east-2' "$tofu_calls")" "backend bucket"
  assert_equal "1" "$(grep -Fxc -- '-backend-config=key=datachord/netrias/staging/tofu.tfstate' "$tofu_calls")" "backend state key"
  assert_equal "1" "$(grep -Fxc -- '-backend-config=use_lockfile=true' "$tofu_calls")" "native lock file"
}

test_supported_targets
test_supported_stages
test_configured_deployments_are_separate_from_stage_names
test_state_keys_preserve_only_live_legacy_states
test_deployer_identity_is_required
test_backend_uses_contract_and_native_lock_file

printf 'Deployment contract tests passed.\n'
