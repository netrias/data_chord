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
  local target_name="$1"
  local stage_name="${2:-}"
  case "$stage_name" in
    dev | qa | staging | prod)
      [[ -f "$(stage_tfvars_path "$target_name" "$stage_name")" ]] || fail "Missing stage config: $(stage_tfvars_path "$target_name" "$stage_name")"
      printf '%s\n' "$stage_name"
      ;;
    *)
      fail "Choose a stage: dev, qa, staging, or prod."
      ;;
  esac
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
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

tfvar_value() {
  local file="$1"
  local key="$2"
  awk -F= -v key="$key" '
    $1 ~ "^[[:space:]]*" key "[[:space:]]*$" {
      value = $2
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^"|"$/, "", value)
      print value
      exit
    }
  ' "$file"
}

deployment_tfvar_value() {
  local target_name="$1"
  local stage_name="$2"
  local key="$3"
  local stage_file common_file value

  stage_file="$(stage_tfvars_path "$target_name" "$stage_name")"
  common_file="$(common_tfvars_path "$target_name")"
  value="$(tfvar_value "$stage_file" "$key")"
  if [[ -z "$value" && -f "$common_file" ]]; then
    value="$(tfvar_value "$common_file" "$key")"
  fi
  printf '%s\n' "$value"
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

  if [[ "$target_name" == "bdf" && ( "$stage_name" == "staging" || "$stage_name" == "prod" ) ]]; then
    printf 'data-chord/%s/tofu.tfstate\n' "$stage_name"
  else
    printf 'datachord/%s/%s/tofu.tfstate\n' "$target_name" "$stage_name"
  fi
}

require_deployer_identity() {
  local target_name="$1"
  local expected_account deployer_role_arn deployer_role_name identity account_id caller_arn

  [[ -n "${AWS_PROFILE:-}" ]] || fail "Set AWS_PROFILE to a profile that assumes the target deployer role."

  expected_account="$(target_value "$target_name" expected_account_id)"
  deployer_role_arn="$(target_value "$target_name" deployer_role_arn)"
  deployer_role_name="${deployer_role_arn##*/}"
  identity="$(aws sts get-caller-identity --query '[Account,Arn]' --output text)" || fail "Could not resolve AWS identity for profile '$AWS_PROFILE'."
  read -r account_id caller_arn <<<"$identity"

  [[ "$account_id" == "$expected_account" ]] || fail "AWS profile '$AWS_PROFILE' resolves to account '$account_id', not target account '$expected_account'."
  [[ "$caller_arn" == "arn:aws:sts::$expected_account:assumed-role/$deployer_role_name/"* ]] ||
    fail "AWS profile '$AWS_PROFILE' must assume '$deployer_role_arn'. Current caller is '$caller_arn'."
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
  local output_json
  output_json="$(tofu -chdir="$INFRA_DIR" output -json "$output_name" 2>/dev/null)" || return 0
  python3 -c 'import json, sys
data = json.load(sys.stdin)
value = data["value"] if isinstance(data, dict) and "value" in data else data
print(json.dumps(value) if isinstance(value, (dict, list)) else value)
' <<<"$output_json" 2>/dev/null || true
}
