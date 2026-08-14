#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_command aws
require_command python3

TARGET_NAME="$(require_target_name "${1:-}")"
SOURCE_PROFILE="${2:-default}"
TARGET_PROFILE="$(deployment_profile_name "$TARGET_NAME")"
ROLE_ARN="$(target_value "$TARGET_NAME" deployer_role_arn)"
REGION="$(target_value "$TARGET_NAME" aws_region)"

[[ "$SOURCE_PROFILE" != "$TARGET_PROFILE" ]] ||
  fail "Source profile and target profile are both '$TARGET_PROFILE'. Cause: an assume-role profile cannot use itself as its source. Pass the base profile instead: just setup $TARGET_NAME <source-profile>"

if ! PROFILES="$(aws configure list-profiles 2>&1)"; then
  fail "Could not read local AWS profiles. AWS reported: $PROFILES. Cause: the AWS configuration is unreadable. Check AWS_CONFIG_FILE and file permissions."
fi

if ! grep -Fxq -- "$SOURCE_PROFILE" <<<"$PROFILES"; then
  fail "Source AWS profile '$SOURCE_PROFILE' does not exist. Cause: setup needs an existing login or credential profile before it can assume '$ROLE_ARN'. Run 'aws configure list-profiles', then rerun: just setup $TARGET_NAME <source-profile>"
fi

if ! SOURCE_IDENTITY="$(aws sts get-caller-identity --profile "$SOURCE_PROFILE" --query '[Account,Arn]' --output text 2>&1)"; then
  fail "Source AWS profile '$SOURCE_PROFILE' could not authenticate. AWS reported: $SOURCE_IDENTITY. Cause: its credentials or SSO session may be missing or expired. If it uses SSO, run 'aws sso login --profile $SOURCE_PROFILE', then rerun setup."
fi

profile_value() {
  aws configure get "$1" --profile "$TARGET_PROFILE" 2>/dev/null || true
}

reject_conflict() {
  local key="$1"
  local expected="$2"
  local actual

  actual="$(profile_value "$key")"
  [[ -z "$actual" || "$actual" == "$expected" ]] ||
    fail "AWS profile '$TARGET_PROFILE' has conflicting $key='$actual'. Cause: setup will not overwrite an existing profile with another meaning. Move or rename that profile, then rerun setup."
}

reject_credential_provider() {
  local key="$1"
  local actual

  actual="$(profile_value "$key")"
  [[ -z "$actual" ]] ||
    fail "AWS profile '$TARGET_PROFILE' has conflicting $key. Cause: the profile already uses another credential provider. Move or rename that profile, then rerun setup."
}

reject_conflict role_arn "$ROLE_ARN"
reject_conflict source_profile "$SOURCE_PROFILE"
reject_conflict region "$REGION"
reject_credential_provider credential_source
reject_credential_provider credential_process
reject_credential_provider web_identity_token_file
reject_credential_provider sso_session
reject_credential_provider sso_start_url
reject_credential_provider aws_access_key_id

CURRENT_ROLE_ARN="$(profile_value role_arn)"
CURRENT_SOURCE_PROFILE="$(profile_value source_profile)"
CURRENT_REGION="$(profile_value region)"

if [[ "$CURRENT_ROLE_ARN" == "$ROLE_ARN" && "$CURRENT_SOURCE_PROFILE" == "$SOURCE_PROFILE" && "$CURRENT_REGION" == "$REGION" ]]; then
  log "AWS profile '$TARGET_PROFILE' already has the required settings"
else
  if ! ASSUME_ROLE_RESULT="$(
    aws sts assume-role \
      --role-arn "$ROLE_ARN" \
      --role-session-name datachord-setup-preflight \
      --profile "$SOURCE_PROFILE" \
      --query 'AssumedRoleUser.Arn' \
      --output text 2>&1
  )"; then
    fail "Source AWS profile '$SOURCE_PROFILE' cannot assume '$ROLE_ARN'. AWS reported: $ASSUME_ROLE_RESULT. Cause: the role trust policy or source permissions do not allow this profile. Correct the role access, then rerun setup. No target profile settings were written."
  fi

  log "Configuring AWS profile '$TARGET_PROFILE' from source profile '$SOURCE_PROFILE'"
  aws configure set region "$REGION" --profile "$TARGET_PROFILE" ||
    fail "Could not set region on AWS profile '$TARGET_PROFILE'. Cause: the AWS configuration is not writable. Check AWS_CONFIG_FILE and file permissions."
  aws configure set source_profile "$SOURCE_PROFILE" --profile "$TARGET_PROFILE" ||
    fail "Could not set source_profile on AWS profile '$TARGET_PROFILE'. Cause: the AWS configuration is not writable. Check AWS_CONFIG_FILE and file permissions."
  aws configure set role_arn "$ROLE_ARN" --profile "$TARGET_PROFILE" ||
    fail "Could not set role_arn on AWS profile '$TARGET_PROFILE'. Cause: the AWS configuration is not writable. Check AWS_CONFIG_FILE and file permissions."
fi

require_deployer_identity "$TARGET_NAME" "$TARGET_PROFILE"

log "Setup complete: '$TARGET_PROFILE' assumes '$ROLE_ARN' in $REGION"
log "Next command: just plan $TARGET_NAME <stage>"
