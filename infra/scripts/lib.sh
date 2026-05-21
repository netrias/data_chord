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

require_env_name() {
  local env_name="${1:-}"
  case "$env_name" in
    staging | prod)
      printf '%s\n' "$env_name"
      ;;
    *)
      fail "Choose an environment: just deploy staging or just deploy prod."
      ;;
  esac
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

env_tfvars_path() {
  printf '%s/env/%s.tfvars\n' "$INFRA_DIR" "$1"
}

common_tfvars_path() {
  printf '%s/env/common.tfvars\n' "$INFRA_DIR"
}

backend_config_path() {
  printf '%s/env/%s.backend.hcl\n' "$INFRA_DIR" "$1"
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

env_tfvar_value() {
  local env_name="$1"
  local key="$2"
  local env_file common_file value

  env_file="$(env_tfvars_path "$env_name")"
  common_file="$(common_tfvars_path)"
  value="$(tfvar_value "$env_file" "$key")"
  if [[ -z "$value" && -f "$common_file" ]]; then
    value="$(tfvar_value "$common_file" "$key")"
  fi
  printf '%s\n' "$value"
}

backend_value() {
  tfvar_value "$1" "$2"
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
