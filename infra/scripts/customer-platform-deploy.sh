#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DEPLOYMENT_ROOT=customer-platform
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

TARGET_NAME="${1:-}"
STAGE_NAME="${2:-}"
MODE="${3:-}"
ENVIRONMENT_FILE="${4:-}"

case "$MODE" in
  plan | deploy) ;;
  *) fail "Use: just customer-plan <target> <stage> <handoff> or just customer-deploy <target> <stage> <handoff>." ;;
esac
[[ -n "$ENVIRONMENT_FILE" ]] || fail "A bootstrap handoff file is required."

for command in aws git python3 tofu; do
  require_command "$command"
done
validate_environment

AWS_REGION_VALUE="$(environment_value region)"
export AWS_REGION="$AWS_REGION_VALUE"
export AWS_DEFAULT_REGION="$AWS_REGION_VALUE"
COMMIT="$(git_commit)"
RECEIPT="${DATA_CHORD_PLAN_ROOT:-$REPO_DIR/.plans}/$TARGET_NAME-$STAGE_NAME-customer-platform.json"
OUTPUTS="${DATA_CHORD_PLAN_ROOT:-$REPO_DIR/.plans}/$TARGET_NAME-$STAGE_NAME-customer-platform-outputs.json"
PLAN_DIR=""
TFVARS_FILE=""

create_plan_directory() {
  local plan_root="${DATA_CHORD_BUILD_ROOT:-$REPO_DIR/build/plans}"
  mkdir -p "$plan_root"
  PLAN_DIR="$(mktemp -d "$plan_root/$TARGET_NAME-$STAGE_NAME-customer-platform.XXXXXX")"
  TFVARS_FILE="$PLAN_DIR/environment.tfvars.json"
  write_tofu_variables "$TFVARS_FILE"
}

reject_full_state() {
  local bucket full_key error_file output
  bucket="$(environment_value state_bucket_name)"
  full_key="datachord/$TARGET_NAME/$STAGE_NAME/tofu.tfstate"
  error_file="$PLAN_DIR/full-state.err"
  if aws s3api head-object --bucket "$bucket" --key "$full_key" >/dev/null 2>"$error_file"; then
    fail "Full deployment state already exists for $TARGET_NAME/$STAGE_NAME. Use one deployment root."
  fi
  output="$(<"$error_file")"
  [[ "$output" == *"404"* || "$output" == *"Not Found"* || "$output" == *"NoSuchKey"* ]] ||
    fail "Could not check for full deployment state: $output"
}

common_checks() {
  require_deployable_git_state
  assume_deployer_role
  verify_foundation_contract
  create_plan_directory
  reject_full_state
  init_tofu
}

state_identity() {
  pull_state "$PLAN_DIR/state.json" "$PLAN_DIR/state-pull.err"
}

create_saved_plan() {
  local plan_file="$1"
  shift
  tofu -chdir="$INFRA_DIR" plan \
    -input=false \
    -var-file="$TFVARS_FILE" \
    -out="$plan_file" \
    "$@" >/dev/null
  tofu -chdir="$INFRA_DIR" show "$plan_file"
  tofu -chdir="$INFRA_DIR" show -json "$plan_file" >"$plan_file.json"
}

receipt_create() {
  python3 "$SCRIPT_DIR/deployment_receipt.py" create \
    --receipt "$RECEIPT" \
    --environment "$ENVIRONMENT_FILE" \
    --target "$TARGET_NAME" \
    --stage "$STAGE_NAME" \
    --deployment-root "$DEPLOYMENT_ROOT" \
    --commit "$COMMIT" \
    --state "$1" \
    --plan-json "$2"
}

receipt_validate() {
  python3 "$SCRIPT_DIR/deployment_receipt.py" validate \
    --receipt "$RECEIPT" \
    --environment "$ENVIRONMENT_FILE" \
    --target "$TARGET_NAME" \
    --stage "$STAGE_NAME" \
    --deployment-root "$DEPLOYMENT_ROOT" \
    --commit "$COMMIT" \
    --state "$1" \
    --expected-status "$2"
}

receipt_status() {
  python3 "$SCRIPT_DIR/deployment_receipt.py" status \
    --receipt "$RECEIPT" \
    --from-status "$1" \
    --to-status "$2"
}

run_plan() {
  local plan_file state_path
  python3 "$SCRIPT_DIR/deployment_receipt.py" invalidate --receipt "$RECEIPT"
  common_checks
  state_path="$(state_identity)"
  plan_file="$PLAN_DIR/forecast.tfplan"
  log "Creating the customer-platform forecast for $TARGET_NAME/$STAGE_NAME"
  create_saved_plan "$plan_file" -lock=false
  receipt_create "$state_path" "$plan_file.json"
  log "Plan saved: $RECEIPT"
  log "No AWS resources were changed."
}

run_deploy() {
  local plan_file state_path
  common_checks
  state_path="$(state_identity)"
  receipt_validate "$state_path" planned
  claim_deployment_root
  receipt_status planned in_progress
  plan_file="$PLAN_DIR/application.tfplan"
  create_saved_plan "$plan_file"
  state_path="$(state_identity)"
  receipt_validate "$state_path" in_progress
  python3 "$SCRIPT_DIR/deployment_receipt.py" check-plan \
    --receipt "$RECEIPT" \
    --plan-json "$plan_file.json" \
    --phase application
  log "Applying the displayed customer-platform saved plan"
  tofu -chdir="$INFRA_DIR" apply -input=false "$plan_file"
  tofu -chdir="$INFRA_DIR" output -json >"$OUTPUTS"
  chmod 600 "$OUTPUTS"
  receipt_status in_progress complete
  log "Customer-platform data plane is ready. Runtime settings and IAM policies: $OUTPUTS"
}

if [[ "$MODE" == "plan" ]]; then
  run_plan
else
  run_deploy
fi
