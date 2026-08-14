#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$INFRA_DIR/.." && pwd)"

log() {
  printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2
}

fail() {
  printf '[%s] ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2
  exit 1
}

require_target_name() {
  local target_name="${1:-}"

  [[ "$target_name" =~ ^[a-z0-9][a-z0-9-]*$ ]] ||
    fail "Target names may contain lowercase letters, numbers, and hyphens. Received: '${target_name:-<empty>}'."
  [[ -f "$(target_config_path "$target_name")" ]] ||
    fail "No target contract exists for '$target_name'. Cause: $(target_config_path "$target_name") is missing. Add that contract or choose an existing target."
  [[ "$(target_value "$target_name" target_slug)" == "$target_name" ]] ||
    fail "Target contract does not identify itself as '$target_name'. Cause: target_slug must match the contract file name."
  printf '%s\n' "$target_name"
}

require_stage_name() {
  local stage_name="${1:-}"
  case "$stage_name" in
    dev | qa | staging | prod)
      printf '%s\n' "$stage_name"
      ;;
    *)
      fail "Choose a stage: dev, qa, staging, or prod."
      ;;
  esac
}

require_configured_deployment() {
  local target_name="$1"
  local stage_name="$2"
  local common_file stage_file

  common_file="$(common_tfvars_path "$target_name")"
  stage_file="$(stage_tfvars_path "$target_name" "$stage_name")"
  [[ -f "$common_file" ]] ||
    fail "Missing target application config: $common_file. Cause: the target has no shared application settings. Add the committed target config before planning."
  [[ -f "$stage_file" ]] ||
    fail "Data Chord is not configured for $target_name/$stage_name. Cause: $stage_file is missing. Add and commit the stage config before planning."
}

require_command() {
  command -v "$1" >/dev/null 2>&1 ||
    fail "Missing required command: $1. Cause: it is not installed or is not on PATH. Install the project prerequisite, then rerun the command."
}

target_config_path() {
  printf '%s/targets/%s.json\n' "$INFRA_DIR" "$1"
}

common_tfvars_path() {
  printf '%s/env/%s/common.tfvars\n' "$INFRA_DIR" "$1"
}

stage_tfvars_path() {
  printf '%s/env/%s/%s.tfvars\n' "$INFRA_DIR" "$1" "$2"
}

netrias_api_key_secret_name_for() {
  printf 'data-chord/%s/netrias-api-key\n' "$1"
}

deployment_profile_name() {
  printf 'datachord-%s\n' "$1"
}

activate_aws_profile() {
  export AWS_PROFILE="$1"
  unset AWS_ACCESS_KEY_ID
  unset AWS_SECRET_ACCESS_KEY
  unset AWS_SESSION_TOKEN
  unset AWS_SECURITY_TOKEN
  unset AWS_WEB_IDENTITY_TOKEN_FILE
  unset AWS_ROLE_ARN
  unset AWS_ROLE_SESSION_NAME
  unset AWS_CONTAINER_CREDENTIALS_FULL_URI
  unset AWS_CONTAINER_CREDENTIALS_RELATIVE_URI
}

select_deployment_credentials() {
  local target_name="$1"
  local target_profile profiles

  target_profile="$(deployment_profile_name "$target_name")"
  if ! profiles="$(aws configure list-profiles 2>&1)"; then
    fail "Could not read local AWS profiles. AWS reported: $profiles. Cause: the AWS configuration is unreadable. Check AWS_CONFIG_FILE and file permissions."
  fi

  if grep -Fxq -- "$target_profile" <<<"$profiles"; then
    activate_aws_profile "$target_profile"
    log "Using target AWS profile: $AWS_PROFILE"
    return 0
  fi

  if [[ -n "${AWS_PROFILE:-}" ]]; then
    log "Target profile '$target_profile' is not configured. Using existing AWS profile: $AWS_PROFILE"
    return 0
  fi

  log "No AWS profile is selected. Using ambient AWS credentials. For local setup, run: just setup $target_name"
}

target_value() {
  local target_name="$1"
  local key="$2"

  python3 - "$(target_config_path "$target_name")" "$key" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as target_file:
    value = json.load(target_file)[sys.argv[2]]
print(value)
PY
}

state_key_for() {
  local target_name="$1"
  local stage_name="$2"

  if [[ "$target_name" == "bdf" && "$stage_name" == "prod" ]]; then
    printf 'data-chord/%s/tofu.tfstate\n' "$stage_name"
  else
    printf 'datachord/%s/%s/tofu.tfstate\n' "$target_name" "$stage_name"
  fi
}

require_deployer_identity() {
  local target_name="$1"
  local profile_name="${2:-}"
  local expected_account deployer_role_arn deployer_role_name deployer_partition identity account_id caller_arn
  local identity_args=(sts get-caller-identity --query '[Account,Arn]' --output text)

  expected_account="$(target_value "$target_name" expected_account_id)"
  deployer_role_arn="$(target_value "$target_name" deployer_role_arn)"
  deployer_role_name="${deployer_role_arn##*/}"
  deployer_partition="${deployer_role_arn#arn:}"
  deployer_partition="${deployer_partition%%:*}"
  [[ -z "$profile_name" ]] || identity_args+=(--profile "$profile_name")
  if ! identity="$(aws "${identity_args[@]}" 2>&1)"; then
    fail "Could not resolve the AWS deployment identity. AWS reported: $identity. Cause: the selected credentials are missing, expired, or cannot reach STS. For local setup, run 'just setup $target_name'. If AWS_PROFILE is set, unset it or select the intended profile."
  fi
  read -r account_id caller_arn <<<"$identity"

  [[ "$account_id" == "$expected_account" ]] ||
    fail "AWS credentials resolve to account '$account_id', not target account '$expected_account'. Cause: the wrong AWS credentials were selected. Run 'just setup $target_name' or set AWS_PROFILE to the intended deployment profile."
  [[ "$caller_arn" == "arn:$deployer_partition:sts::$expected_account:assumed-role/$deployer_role_name/"* ]] ||
    fail "AWS credentials must assume '$deployer_role_arn'. Current caller is '$caller_arn'. Cause: the credentials use a direct user or another role. Run 'just setup $target_name' or set AWS_PROFILE to a profile that assumes the deployer role."
}

init_tofu() {
  local target_name="$1"
  local stage_name="$2"
  local bucket region state_key

  bucket="$(target_value "$target_name" state_bucket_name)"
  region="$(target_value "$target_name" aws_region)"
  state_key="$(state_key_for "$target_name" "$stage_name")"
  export TF_DATA_DIR="${DATA_CHORD_TF_DATA_DIR:-$INFRA_DIR/.terraform-data/$target_name/$stage_name}"

  log "Initializing OpenTofu backend for $target_name/$stage_name at s3://$bucket/$state_key"
  if ! tofu -chdir="$INFRA_DIR" init \
    -backend-config="bucket=$bucket" \
    -backend-config="key=$state_key" \
    -backend-config="region=$region" \
    -backend-config="encrypt=true" \
    -backend-config="use_lockfile=true" \
    -input=false \
    -reconfigure; then
    fail "Could not initialize OpenTofu state for $target_name/$stage_name. Cause: the state bucket may be missing, inaccessible, or configured in another account or region. Review the OpenTofu error above, confirm the foundation deployment, and run 'just setup $target_name' to verify local access."
  fi
}

tofu_output() {
  local output_name="$1"
  tofu -chdir="$INFRA_DIR" output -raw "$output_name" 2>/dev/null || true
}

required_tofu_output() {
  local output_name="$1"
  local output

  if ! output="$(tofu -chdir="$INFRA_DIR" output -raw "$output_name" 2>&1)"; then
    if [[ "$output" == *"Output \"$output_name\" not found"* || "$output" == *"No outputs found"* ]]; then
      fail "OpenTofu output '$output_name' is unavailable. Apply the application stack first."
    fi
    fail "Could not read OpenTofu output '$output_name': $output"
  fi

  [[ -n "$output" ]] || fail "OpenTofu output '$output_name' is unavailable. Apply the application stack first."
  printf '%s\n' "$output"
}
