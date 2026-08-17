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
  if using_external_contract; then
    [[ -n "$target_name" ]] || fail "A target name is required."
    [[ "$(contract_value target_slug)" == "$target_name" ]] ||
      fail "Deployment contract does not select target '$target_name'."
    printf '%s\n' "$target_name"
    return 0
  fi
  case "$target_name" in
    bdf | netrias)
      [[ -f "$(target_config_path "$target_name")" ]] || fail "Missing target contract: $(target_config_path "$target_name")"
      [[ "$(target_value "$target_name" target_slug)" == "$target_name" ]] || fail "Target contract does not identify itself as '$target_name'."
      printf '%s\n' "$target_name"
      ;;
    *)
      fail "Choose a target: bdf or netrias."
      ;;
  esac
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

  if using_external_contract; then
    python3 "$SCRIPT_DIR/deployment_contract.py" validate \
      "$DATA_CHORD_DEPLOYMENT_CONTRACT" "$target_name" "$stage_name" ||
      fail "Invalid external deployment contract."
    return 0
  fi

  common_file="$(common_tfvars_path "$target_name")"
  stage_file="$(stage_tfvars_path "$target_name" "$stage_name")"
  [[ -f "$common_file" ]] || fail "Missing target application config: $common_file"
  [[ -f "$stage_file" ]] || fail "Data Chord is not configured for $target_name/$stage_name."
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

target_config_path() {
  if using_external_contract; then
    printf '%s\n' "$DATA_CHORD_DEPLOYMENT_CONTRACT"
    return 0
  fi
  printf '%s/targets/%s.json\n' "$INFRA_DIR" "$1"
}

using_external_contract() {
  [[ -n "${DATA_CHORD_DEPLOYMENT_CONTRACT:-}" ]]
}

contract_value() {
  python3 "$SCRIPT_DIR/deployment_contract.py" get \
    "$DATA_CHORD_DEPLOYMENT_CONTRACT" "$1"
}

common_tfvars_path() {
  printf '%s/env/%s/common.tfvars\n' "$INFRA_DIR" "$1"
}

stage_tfvars_path() {
  printf '%s/env/%s/%s.tfvars\n' "$INFRA_DIR" "$1" "$2"
}

netrias_api_key_secret_name_for() {
  if using_external_contract; then
    contract_value netrias_api_key_secret_name
  else
    printf 'data-chord/%s/netrias-api-key\n' "$1"
  fi
}

target_value() {
  local target_name="$1"
  local key="$2"

  if using_external_contract; then
    contract_value "$key"
    return 0
  fi

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

  if using_external_contract; then
    contract_value state_key
  elif [[ "$target_name" == "bdf" && "$stage_name" == "prod" ]]; then
    printf 'data-chord/%s/tofu.tfstate\n' "$stage_name"
  else
    printf 'datachord/%s/%s/tofu.tfstate\n' "$target_name" "$stage_name"
  fi
}

require_deployer_identity() {
  local target_name="$1"
  local expected_account partition deployer_role_arn deployer_role_name identity account_id caller_arn

  expected_account="$(target_value "$target_name" expected_account_id)"
  partition="aws"
  if using_external_contract; then
    partition="$(target_value "$target_name" aws_partition)"
  fi
  deployer_role_arn="$(target_value "$target_name" deployer_role_arn)"
  deployer_role_name="${deployer_role_arn##*/}"
  identity="$(aws sts get-caller-identity --query '[Account,Arn]' --output text)" || fail "Could not resolve the AWS deployment identity."
  read -r account_id caller_arn <<<"$identity"

  [[ "$account_id" == "$expected_account" ]] || fail "AWS credentials resolve to account '$account_id', not target account '$expected_account'."
  [[ "$caller_arn" == "arn:$partition:sts::$expected_account:assumed-role/$deployer_role_name/"* ]] ||
    fail "AWS credentials must assume '$deployer_role_arn'. Current caller is '$caller_arn'."
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
  tofu -chdir="$INFRA_DIR" init \
    -backend-config="bucket=$bucket" \
    -backend-config="key=$state_key" \
    -backend-config="region=$region" \
    -backend-config="encrypt=true" \
    -backend-config="use_lockfile=true" \
    -input=false \
    -reconfigure
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
