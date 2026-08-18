#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_command python3
TARGET_NAME="${1:-}"
STAGE_NAME="${2:-}"
MODE="${3:-}"
ENVIRONMENT_FILE="$(environment_path "$TARGET_NAME" "$STAGE_NAME")"

# This validation rejects missing or invalid environments before external tools run.
validate_environment
case "$MODE" in
  plan | deploy) ;;
  *) fail "Use: just plan <target> <stage> or just deploy <target> <stage>." ;;
esac
if [[ "$MODE" == "deploy" ]]; then
  export DEPLOYER_SESSION_SECONDS=14400
  export DEPLOYER_REQUIRED_REMAINING_SECONDS=10800
else
  export DEPLOYER_SESSION_SECONDS=3600
  export DEPLOYER_REQUIRED_REMAINING_SECONDS=0
fi

for command in aws git tofu; do
  require_command "$command"
done

AWS_REGION_VALUE="$(environment_value region)"
export AWS_REGION="$AWS_REGION_VALUE"
export AWS_DEFAULT_REGION="$AWS_REGION_VALUE"
COMMIT="$(git_commit)"
RECEIPT="${DATA_CHORD_PLAN_ROOT:-$REPO_DIR/.plans}/$TARGET_NAME-$STAGE_NAME.json"
PLAN_DIR=""
TFVARS_FILE=""

create_plan_directory() {
  local plan_root="${DATA_CHORD_BUILD_ROOT:-$REPO_DIR/build/plans}"
  mkdir -p "$plan_root"
  PLAN_DIR="$(mktemp -d "$plan_root/$TARGET_NAME-$STAGE_NAME.XXXXXX")"
  TFVARS_FILE="$PLAN_DIR/environment.tfvars.json"
  write_tofu_variables "$TFVARS_FILE"
}

common_checks() {
  require_deployable_git_state
  assume_deployer_role
  verify_foundation_contract
  init_tofu
  create_plan_directory
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
    -var="image_tag=$COMMIT" \
    -out="$plan_file" \
    "$@" >/dev/null
  tofu -chdir="$INFRA_DIR" show "$plan_file"
  tofu -chdir="$INFRA_DIR" show -json "$plan_file" >"$plan_file.json"
}

receipt_create() {
  local state_path="$1"
  local plan_json="$2"
  python3 "$SCRIPT_DIR/deployment_receipt.py" create \
    --receipt "$RECEIPT" \
    --environment "$ENVIRONMENT_FILE" \
    --target "$TARGET_NAME" \
    --stage "$STAGE_NAME" \
    --commit "$COMMIT" \
    --state "$state_path" \
    --plan-json "$plan_json"
}

receipt_validate() {
  local state_path="$1"
  local expected_status="$2"
  python3 "$SCRIPT_DIR/deployment_receipt.py" validate \
    --receipt "$RECEIPT" \
    --environment "$ENVIRONMENT_FILE" \
    --target "$TARGET_NAME" \
    --stage "$STAGE_NAME" \
    --commit "$COMMIT" \
    --state "$state_path" \
    --expected-status "$expected_status"
}

receipt_status() {
  python3 "$SCRIPT_DIR/deployment_receipt.py" status \
    --receipt "$RECEIPT" \
    --from-status "$1" \
    --to-status "$2"
}

check_internal_plan() {
  python3 "$SCRIPT_DIR/deployment_receipt.py" check-plan \
    --receipt "$RECEIPT" \
    --plan-json "$1" \
    --phase "$2"
}

run_plan() {
  local state_path plan_file
  python3 "$SCRIPT_DIR/deployment_receipt.py" invalidate --receipt "$RECEIPT"
  common_checks
  state_path="$(state_identity)"
  plan_file="$PLAN_DIR/forecast.tfplan"
  log "Creating a read-only deployment forecast for $TARGET_NAME/$STAGE_NAME"
  create_saved_plan "$plan_file" -lock=false
  receipt_create "$state_path" "$plan_file.json"
  log "Plan saved: $RECEIPT"
  log "This is a bounded resource forecast. Deploy creates and checks fresh saved plans before each apply."
  log "No application resource changes were made."
}

apply_prerequisites() {
  local plan_file="$PLAN_DIR/prerequisites.tfplan" state_path
  log "Planning the application prerequisites"
  create_saved_plan "$plan_file" \
    -target=aws_codebuild_project.app_image \
    -target=aws_iam_role_policy.application_build
  check_internal_plan "$plan_file.json" prerequisite
  state_path="$(state_identity)"
  receipt_validate "$state_path" in_progress
  log "Applying the displayed prerequisite saved plan"
  tofu -chdir="$INFRA_DIR" apply -input=false "$plan_file"
  verify_prerequisite_policy
}

retry_instruction() {
  printf 'The current plan cannot be reused. Next: just plan %s %s' "$TARGET_NAME" "$STAGE_NAME"
}

verify_prerequisite_policy() {
  local attempt error_detail exit_code error_file plan_file
  log "Verifying the prerequisite IAM policy"
  for attempt in {1..5}; do
    plan_file="$PLAN_DIR/prerequisite-policy-verification-$attempt.tfplan"
    error_file="$plan_file.err"
    if tofu -chdir="$INFRA_DIR" plan \
      -input=false \
      -detailed-exitcode \
      -var-file="$TFVARS_FILE" \
      -var="image_tag=$COMMIT" \
      -target=aws_iam_role_policy.application_build \
      -out="$plan_file" > /dev/null 2>"$error_file"; then
      log "Prerequisite IAM policy verified"
      return 0
    else
      exit_code=$?
    fi
    if [[ "$exit_code" == "2" ]]; then
      ((attempt < 5)) && sleep 2
      continue
    fi
    error_detail="$(<"$error_file")"
    [[ -n "$error_detail" ]] || error_detail="OpenTofu returned no error message"
    fail "Could not verify the prerequisite IAM policy. OpenTofu: $error_detail. $(retry_instruction)"
  done
  fail "The prerequisite IAM policy was not applied. No image build started. $(retry_instruction)"
}

ecr_repository_name() {
  local repository_url
  repository_url="$(required_tofu_output ecr_repository_url)"
  printf '%s\n' "${repository_url##*/}"
}

image_exists() {
  local output
  if output="$(aws ecr describe-images \
    --repository-name "$1" \
    --image-ids "imageTag=$COMMIT" 2>&1)"; then
    return 0
  fi
  [[ "$output" == *"ImageNotFoundException"* ]] ||
    fail "Could not inspect the application image: $output"
  return 1
}

watch_build() {
  local build_id="$1"
  local deadline status phase fields previous="" response_file error_file error_detail
  response_file="$PLAN_DIR/codebuild-status.json"
  error_file="$PLAN_DIR/codebuild-status.err"
  deadline=$((SECONDS + ${DATA_CHORD_BUILD_WAIT_SECONDS:-3900}))
  while ((SECONDS < deadline)); do
    if ! aws codebuild batch-get-builds \
      --ids "$build_id" \
      --output json >"$response_file" 2>"$error_file"; then
      error_detail="$(<"$error_file")"
      [[ -n "$error_detail" ]] || error_detail="AWS returned no error message"
      fail "Could not inspect CodeBuild $build_id. Status: UNKNOWN. Phase: UNKNOWN. AWS: $error_detail. $(retry_instruction)"
    fi
    if ! fields="$(python3 "$SCRIPT_DIR/codebuild_status.py" "$response_file" "$build_id" "$TARGET_NAME" "$STAGE_NAME" 2>&1)"; then
      fail "$fields"
    fi
    read -r status phase <<<"$fields"
    if [[ "$status:$phase" != "$previous" ]]; then
      log "CodeBuild: $status ($phase)"
      previous="$status:$phase"
    fi
    case "$status" in
      SUCCEEDED) return 0 ;;
    esac
    sleep 10
  done
  fail "CodeBuild $build_id did not finish within 65 minutes. Status: ${status:-UNKNOWN}. Phase: ${phase:-UNKNOWN}. AWS returned no failure message. $(retry_instruction)"
}

ensure_image() {
  local repository build_id project start_error error_detail
  repository="$(ecr_repository_name)"
  if image_exists "$repository"; then
    log "Reusing immutable image $repository:$COMMIT"
    return 0
  fi
  project="$(required_tofu_output codebuild_project_name)"
  log "Building immutable image for commit $COMMIT"
  start_error="$PLAN_DIR/codebuild-start.err"
  if ! build_id="$(aws codebuild start-build \
    --project-name "$project" \
    --source-version "$COMMIT" \
    --query 'build.id' \
    --output text 2>"$start_error")"; then
    error_detail="$(<"$start_error")"
    [[ -n "$error_detail" ]] || error_detail="AWS returned no error message"
    fail "CodeBuild did not start. Build ID: not created. Status: NOT_STARTED. Phase: NOT_STARTED. AWS: $error_detail. $(retry_instruction)"
  fi
  [[ -n "$build_id" && "$build_id" != "None" ]] ||
    fail "CodeBuild did not start. Build ID: not created. Status: NOT_STARTED. Phase: NOT_STARTED. AWS returned no build ID. $(retry_instruction)"
  watch_build "$build_id"
  image_exists "$repository" || fail "CodeBuild finished but image $repository:$COMMIT does not exist."
}

apply_application() {
  local plan_file="$PLAN_DIR/application.tfplan"
  log "Planning the complete application"
  create_saved_plan "$plan_file"
  check_internal_plan "$plan_file.json" application
  log "Applying the displayed application saved plan"
  tofu -chdir="$INFRA_DIR" apply -input=false "$plan_file"
}

watch_ecs() {
  local cluster service deadline fields state desired running pending
  cluster="$(required_tofu_output ecs_cluster_name)"
  service="$(required_tofu_output ecs_service_name)"
  deadline=$((SECONDS + 900))
  log "Waiting for ECS service $cluster/$service"
  while ((SECONDS < deadline)); do
    fields="$(aws ecs describe-services \
      --cluster "$cluster" \
      --services "$service" \
      --query "services[0].deployments[?status=='PRIMARY'] | [0].[rolloutState,desiredCount,runningCount,pendingCount]" \
      --output text)"
    read -r state desired running pending <<<"$fields"
    if [[ "$state" == "COMPLETED" && "$running" == "$desired" && "$pending" == "0" ]]; then
      return 0
    fi
    sleep 15
  done
  fail "ECS did not become stable within 15 minutes."
}

require_healthy_targets() {
  local target_group states state
  target_group="$(required_tofu_output target_group_arn)"
  states="$(aws elbv2 describe-target-health \
    --target-group-arn "$target_group" \
    --query 'TargetHealthDescriptions[].TargetHealth.State' \
    --output text)"
  [[ -n "$states" && "$states" != "None" ]] || fail "The load balancer has no targets."
  for state in $states; do
    [[ "$state" == "healthy" ]] || fail "A load balancer target is $state."
  done
}

run_deploy() {
  local state_path app_url
  common_checks
  state_path="$(state_identity)"
  receipt_validate "$state_path" planned
  receipt_status planned in_progress
  apply_prerequisites
  ensure_image
  apply_application
  watch_ecs
  require_healthy_targets
  receipt_status in_progress complete
  app_url="$(required_tofu_output app_url)"
  log "Deploy complete: $app_url"
}

if [[ "$MODE" == "plan" ]]; then
  run_plan
else
  run_deploy
fi
