#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

ENV_NAME="$(require_env_name "${1:-}")"

require_command aws

REGION="$(env_tfvar_value "$ENV_NAME" aws_region)"
[[ -n "$REGION" ]] || fail "aws_region is missing for environment: $ENV_NAME"
BUCKET="$(resolve_state_bucket_name "$ENV_NAME")"

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

log "State backend is ready: s3://$BUCKET"
