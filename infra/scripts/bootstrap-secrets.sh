#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

TARGET_NAME="$(require_deployment_target "${1:-}")"
STAGE_NAME="$(require_stage_name "${2:-}")"
MODE="${3:-ensure}"
COMMON_TFVARS_FILE="$(common_tfvars_path "$TARGET_NAME")"
TFVARS_FILE="$(stage_tfvars_path "$TARGET_NAME" "$STAGE_NAME")"

require_command aws
require_aws_profile
[[ -f "$COMMON_TFVARS_FILE" ]] || fail "Missing common config: $COMMON_TFVARS_FILE"
[[ -f "$TFVARS_FILE" ]] || fail "Missing env config: $TFVARS_FILE"
assert_expected_aws_account "$TARGET_NAME"

SECRET_NAME="$(deployment_tfvar_value "$TARGET_NAME" "$STAGE_NAME" netrias_api_key_secret_name)"
REGION="$(deployment_tfvar_value "$TARGET_NAME" "$STAGE_NAME" aws_region)"

[[ -n "$SECRET_NAME" ]] || fail "netrias_api_key_secret_name is missing in $TFVARS_FILE"
[[ -n "$REGION" ]] || fail "aws_region is missing in $COMMON_TFVARS_FILE or $TFVARS_FILE"

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
