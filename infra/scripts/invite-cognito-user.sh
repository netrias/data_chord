#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_command python3
usage() {
  cat >&2 <<'EOF'
Usage: infra/scripts/invite-cognito-user.sh <bdf|netrias> <dev|qa|staging|prod> <email> [resend]

Creates a Cognito user for Data Chord. Cognito emails the user a temporary
password. Pass "resend" as the fourth argument to resend the invitation for an
existing user.
EOF
}

TARGET_NAME="$(require_target_name "${1:-}")"
STAGE_NAME="$(require_stage_name "${2:-}")"
require_configured_deployment "$TARGET_NAME" "$STAGE_NAME"
EMAIL="${3:-}"
MESSAGE_ACTION="${4:-}"

AWS_REGION_VALUE="$(target_value "$TARGET_NAME" aws_region)"
export AWS_REGION="$AWS_REGION_VALUE"
export AWS_DEFAULT_REGION="$AWS_REGION_VALUE"

require_command aws
require_command tofu

if [[ -z "$EMAIL" || "$EMAIL" != *@*.* ]]; then
  usage
  fail "Provide the user's email address."
fi

if [[ -n "$MESSAGE_ACTION" && "$MESSAGE_ACTION" != "resend" ]]; then
  usage
  fail "Unknown action: $MESSAGE_ACTION"
fi

require_deployer_identity "$TARGET_NAME"
log "Using verified AWS deployment-role credentials"
init_tofu "$TARGET_NAME" "$STAGE_NAME" >/dev/null

USER_POOL_ID="$(tofu_output cognito_user_pool_id)"
APP_URL="$(tofu_output app_url)"
GET_USER_OUTPUT=""
USER_STATUS=""
[[ -n "$USER_POOL_ID" ]] || fail "Cognito user pool is not available. Deploy $TARGET_NAME/$STAGE_NAME first."

if GET_USER_OUTPUT="$(aws cognito-idp admin-get-user \
  --user-pool-id "$USER_POOL_ID" \
  --username "$EMAIL" \
  --query UserStatus \
  --output text 2>&1)"; then
  USER_STATUS="$GET_USER_OUTPUT"

  if [[ "$MESSAGE_ACTION" != "resend" ]]; then
    log "User already exists in $TARGET_NAME/$STAGE_NAME: $EMAIL ($USER_STATUS)"
    [[ -z "$APP_URL" ]] || log "App URL: $APP_URL"
    exit 0
  fi

  if [[ "$USER_STATUS" == "CONFIRMED" ]]; then
    # Admin invites only reset the temporary-password path; confirmed users
    # should recover access through Cognito's normal password flow.
    fail "User is already confirmed: $EMAIL. Not resending an admin invite because that is only for temporary-password onboarding."
  fi

  log "Resending Cognito invitation for $EMAIL in $TARGET_NAME/$STAGE_NAME"
  aws cognito-idp admin-create-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$EMAIL" \
    --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
    --desired-delivery-mediums EMAIL \
    --message-action RESEND >/dev/null
else
  if [[ "$GET_USER_OUTPUT" != *"UserNotFoundException"* ]]; then
    fail "Could not check Cognito user '$EMAIL': $GET_USER_OUTPUT"
  fi

  log "Creating Cognito user for $EMAIL in $TARGET_NAME/$STAGE_NAME"
  aws cognito-idp admin-create-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$EMAIL" \
    --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
    --desired-delivery-mediums EMAIL >/dev/null
fi

log "Cognito invitation sent to: $EMAIL"
[[ -z "$APP_URL" ]] || log "App URL: $APP_URL"
