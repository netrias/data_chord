#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_command python3
TARGET_NAME="$(require_target_name "${1:-}")"
STAGE_NAME="$(require_stage_name "${2:-}")"
require_configured_deployment "$TARGET_NAME" "$STAGE_NAME"
MODE="${3:-ensure}"

case "$MODE" in
  ensure | check) ;;
  *) fail "Choose a secret mode: ensure or check." ;;
esac

require_command aws
select_deployment_credentials "$TARGET_NAME"
require_deployer_identity "$TARGET_NAME"

SECRET_NAME="$(netrias_api_key_secret_name_for "$STAGE_NAME")"
REGION="$(target_value "$TARGET_NAME" aws_region)"

secret_version_token() {
  local current_version_id="$1"

  DATA_CHORD_SECRET_IDENTITY="$SECRET_NAME" \
    DATA_CHORD_SECRET_VALUE="$NETRIAS_API_KEY" \
    DATA_CHORD_SECRET_VERSION="$current_version_id" \
    python3 -c 'import hashlib, os
identity = os.environ["DATA_CHORD_SECRET_IDENTITY"].encode()
value = os.environ["DATA_CHORD_SECRET_VALUE"].encode()
version = os.environ["DATA_CHORD_SECRET_VERSION"].encode()
print(hashlib.sha256(identity + b"\0" + version + b"\0" + value).hexdigest())
'
}

[[ -n "$REGION" ]] || fail "aws_region is missing in $(target_config_path "$TARGET_NAME")"

if aws secretsmanager describe-secret --region "$REGION" --secret-id "$SECRET_NAME" >/dev/null 2>&1; then
  if [[ -n "${NETRIAS_API_KEY:-}" && "$MODE" == "ensure" ]]; then
    # Treat NETRIAS_API_KEY as the desired value only in explicit ensure mode.
    CURRENT_SECRET_JSON="$(
      aws secretsmanager get-secret-value \
        --region "$REGION" \
        --secret-id "$SECRET_NAME" \
        --query '{VersionId:VersionId,SecretString:SecretString}' \
        --output json
    )" || fail "Could not read current Secrets Manager value: $SECRET_NAME"
    CURRENT_VERSION_ID="$(
      DATA_CHORD_DESIRED_SECRET_VALUE="$NETRIAS_API_KEY" \
        python3 -c 'import json, os, sys
current = json.load(sys.stdin)
if current.get("SecretString") == os.environ["DATA_CHORD_DESIRED_SECRET_VALUE"]:
    raise SystemExit(0)
version_id = current.get("VersionId")
if not isinstance(version_id, str) or not version_id:
    raise SystemExit("Current secret version has no VersionId")
print(version_id)
' <<<"$CURRENT_SECRET_JSON"
    )"
    if [[ -z "$CURRENT_VERSION_ID" ]]; then
      log "Secrets Manager secret already has the desired value: $SECRET_NAME"
      exit 0
    fi
    CLIENT_REQUEST_TOKEN="$(secret_version_token "$CURRENT_VERSION_ID")"
    log "Updating Secrets Manager value: $SECRET_NAME"
    aws secretsmanager put-secret-value \
      --region "$REGION" \
      --secret-id "$SECRET_NAME" \
      --client-request-token "$CLIENT_REQUEST_TOKEN" \
      --secret-string "$NETRIAS_API_KEY" \
      >/dev/null
  else
    log "Secrets Manager secret exists: $SECRET_NAME"
  fi
  exit 0
fi

if [[ "$MODE" != "ensure" ]]; then
  fail "Missing Secrets Manager secret: $SECRET_NAME. Cause: the stage API key has not been prepared in this AWS account. Set NETRIAS_API_KEY and run '$0 $TARGET_NAME $STAGE_NAME ensure', then rerun the plan."
fi

[[ -n "${NETRIAS_API_KEY:-}" ]] ||
  fail "Missing $SECRET_NAME. Cause: ensure mode needs the desired API key value. Set NETRIAS_API_KEY, then rerun '$0 $TARGET_NAME $STAGE_NAME ensure'."

CLIENT_REQUEST_TOKEN="$(secret_version_token create)"
log "Creating Secrets Manager secret: $SECRET_NAME"
aws secretsmanager create-secret \
  --region "$REGION" \
  --name "$SECRET_NAME" \
  --client-request-token "$CLIENT_REQUEST_TOKEN" \
  --secret-string "$NETRIAS_API_KEY" \
  >/dev/null
