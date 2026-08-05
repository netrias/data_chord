#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

TARGET_NAME="$(require_target_name "${1:-}")"
STAGE_NAME="$(require_stage_name "${2:-}")"
require_configured_deployment "$TARGET_NAME" "$STAGE_NAME"
MODE="${3:-ensure}"

require_command aws
require_deployer_identity "$TARGET_NAME"

SECRET_NAME="$(netrias_api_key_secret_name_for "$STAGE_NAME")"
REGION="$(target_value "$TARGET_NAME" aws_region)"

[[ -n "$REGION" ]] || fail "aws_region is missing in $(target_config_path "$TARGET_NAME")"

if aws secretsmanager describe-secret --region "$REGION" --secret-id "$SECRET_NAME" >/dev/null 2>&1; then
  if [[ -n "${NETRIAS_API_KEY:-}" && "$MODE" == "ensure" ]]; then
    # Treat NETRIAS_API_KEY as the desired value only during deploys; plan mode
    # should verify presence without rotating a secret.
    log "Updating Secrets Manager value: $SECRET_NAME"
    aws secretsmanager put-secret-value \
      --region "$REGION" \
      --secret-id "$SECRET_NAME" \
      --secret-string "$NETRIAS_API_KEY" \
      >/dev/null
  else
    log "Secrets Manager secret exists: $SECRET_NAME"
  fi
  exit 0
fi

if [[ "$MODE" != "ensure" ]]; then
  fail "Missing Secrets Manager secret: $SECRET_NAME"
fi

[[ -n "${NETRIAS_API_KEY:-}" ]] || fail "Missing $SECRET_NAME. Set NETRIAS_API_KEY for the first deploy."

log "Creating Secrets Manager secret: $SECRET_NAME"
aws secretsmanager create-secret \
  --region "$REGION" \
  --name "$SECRET_NAME" \
  --secret-string "$NETRIAS_API_KEY" \
  >/dev/null
