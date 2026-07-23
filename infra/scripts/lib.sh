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

require_deployment_target() {
  local target="${1:-}"

  if [[ ! "$target" =~ ^[a-z0-9][a-z0-9-]*$ || ! -d "$INFRA_DIR/env/$target" ]]; then
    fail "Choose a deployment target with checked-in config, for example: strides or netrias."
  fi
  printf '%s\n' "$target"
}

require_stage_name() {
  local stage="${1:-}"
  case "$stage" in
    staging | prod)
      printf '%s\n' "$stage"
      ;;
    *)
      fail "Choose a stage: staging or prod."
      ;;
  esac
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

deployment_config_dir() {
  printf '%s/env/%s\n' "$INFRA_DIR" "$1"
}

common_tfvars_path() {
  printf '%s/common.tfvars\n' "$(deployment_config_dir "$1")"
}

stage_tfvars_path() {
  printf '%s/%s.tfvars\n' "$(deployment_config_dir "$1")" "$2"
}

backend_config_path() {
  printf '%s/%s.backend.hcl\n' "$(deployment_config_dir "$1")" "$2"
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
  local target="$1"
  local stage="$2"
  local key="$3"
  local stage_file common_file value

  stage_file="$(stage_tfvars_path "$target" "$stage")"
  common_file="$(common_tfvars_path "$target")"
  value="$(tfvar_value "$stage_file" "$key")"
  if [[ -z "$value" && -f "$common_file" ]]; then
    value="$(tfvar_value "$common_file" "$key")"
  fi
  printf '%s\n' "$value"
}

backend_value() {
  tfvar_value "$1" "$2"
}

require_aws_profile() {
  [[ -n "${AWS_PROFILE:-}" ]] \
    || fail "Set AWS_PROFILE to credentials for the selected deployment target."
}

resolve_aws_account_id() {
  local account_id

  account_id="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" \
    || fail "Unable to determine the AWS account for profile '$AWS_PROFILE'."
  [[ "$account_id" =~ ^[0-9]{12}$ ]] \
    || fail "AWS returned an invalid account ID for profile '$AWS_PROFILE': $account_id"
  printf '%s\n' "$account_id"
}

assert_expected_aws_account() {
  local target="$1"
  local expected_account_id actual_account_id common_file

  common_file="$(common_tfvars_path "$target")"
  expected_account_id="$(tfvar_value "$common_file" aws_account_id)"
  [[ "$expected_account_id" =~ ^[0-9]{12}$ ]] \
    || fail "aws_account_id is missing or invalid in $common_file"

  actual_account_id="$(resolve_aws_account_id)"
  [[ "$actual_account_id" == "$expected_account_id" ]] \
    || fail "Deployment target '$target' requires AWS account $expected_account_id, but profile '$AWS_PROFILE' uses $actual_account_id."
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
