#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

TARGET_NAME="$(require_deployment_target "${1:-}")"
STAGE_NAME="$(require_stage_name "${2:-}")"
BACKEND_FILE="$(backend_config_path "$TARGET_NAME" "$STAGE_NAME")"

require_command aws
require_aws_profile
[[ -f "$BACKEND_FILE" ]] || fail "Missing backend config: $BACKEND_FILE"
assert_expected_aws_account "$TARGET_NAME"

BUCKET="$(backend_value "$BACKEND_FILE" bucket)"
REGION="$(backend_value "$BACKEND_FILE" region)"

[[ -n "$BUCKET" ]] || fail "Backend bucket is missing in $BACKEND_FILE"
[[ -n "$REGION" ]] || fail "Backend region is missing in $BACKEND_FILE"

if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  log "State bucket exists: $BUCKET"
else
  log "Creating state bucket: $BUCKET"
  # us-east-1 rejects LocationConstraint even though every other region
  # requires it.
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket \
      --bucket "$BUCKET" \
      --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION" \
      >/dev/null
  fi
fi

log "Enabling state bucket public-access block"
aws s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true \
  >/dev/null

log "Enabling state bucket versioning"
aws s3api put-bucket-versioning \
  --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled \
  >/dev/null

log "Enabling state bucket encryption"
aws s3api put-bucket-encryption \
  --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
  >/dev/null

log "State backend is ready for $TARGET_NAME/$STAGE_NAME: s3://$BUCKET"
