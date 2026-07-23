#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

TARGET_NAME="$(require_deployment_target "${1:-}")"
STAGE_NAME="$(require_stage_name "${2:-}")"
MODE="${3:-deploy}"
BACKEND_FILE="$(backend_config_path "$TARGET_NAME" "$STAGE_NAME")"
COMMON_TFVARS_FILE="$(common_tfvars_path "$TARGET_NAME")"
STAGE_TFVARS_FILE="$(stage_tfvars_path "$TARGET_NAME" "$STAGE_NAME")"

require_command aws
require_command git
require_command tofu
require_aws_profile

[[ -f "$BACKEND_FILE" ]] || fail "Missing backend config: $BACKEND_FILE"
[[ -f "$COMMON_TFVARS_FILE" ]] || fail "Missing common config: $COMMON_TFVARS_FILE"
[[ -f "$STAGE_TFVARS_FILE" ]] || fail "Missing stage config: $STAGE_TFVARS_FILE"

assert_expected_aws_account "$TARGET_NAME"

AWS_REGION_VALUE="$(deployment_tfvar_value "$TARGET_NAME" "$STAGE_NAME" aws_region)"
[[ -n "$AWS_REGION_VALUE" ]] || fail "aws_region is missing in $COMMON_TFVARS_FILE or $STAGE_TFVARS_FILE"
export AWS_REGION="$AWS_REGION_VALUE"
export AWS_DEFAULT_REGION="$AWS_REGION_VALUE"

tofu_args=(
  "-var-file=$COMMON_TFVARS_FILE"
  "-var-file=$STAGE_TFVARS_FILE"
)

AUTH_BYPASS_CIDRS_SECRET_NAME="data-chord/$STAGE_NAME/auth-bypass-cidrs"

git_branch() {
  git -C "$REPO_DIR" branch --show-current
}

git_commit() {
  git -C "$REPO_DIR" rev-parse HEAD
}

git_image_tag() {
  git -C "$REPO_DIR" rev-parse --short=12 HEAD
}

require_immutable_image_tag() {
  local image_tag="$1"

  if [[ "$image_tag" == "latest" || ! "$image_tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
    fail "Image tag must be an immutable Docker tag such as a short commit SHA, not '$image_tag'."
  fi
}

remote_branch_matches_commit() {
  local branch="$1"
  local commit="$2"
  local remote_commit
  remote_commit="$(
    git -C "$REPO_DIR" ls-remote origin "refs/heads/$branch" |
      awk '{print $1}'
  )"
  [[ "$remote_commit" == "$commit" ]]
}

ensure_deployable_git_state() {
  local branch commit dirty_status
  branch="$(git_branch)"
  commit="$(git_commit)"
  dirty_status="$(git -C "$REPO_DIR" status --porcelain)"

  [[ -n "$branch" ]] || fail "Cannot deploy from a detached HEAD."

  if [[ -n "$dirty_status" && "${DATA_CHORD_DEPLOY_ALLOW_DIRTY:-}" != "1" ]]; then
    fail "Working tree has uncommitted changes. Commit them, or rerun with DATA_CHORD_DEPLOY_ALLOW_DIRTY=1 to deploy the current HEAD anyway."
  fi

  # CodeBuild pulls from GitHub, so local-only commits would build a different
  # image than the one this script is about to deploy.
  if ! remote_branch_matches_commit "$branch" "$commit"; then
    fail "origin/$branch does not match local HEAD. Push branch '$branch' before deploying."
  fi

  log "Deploy source: $branch @ ${commit:0:12}"
}

init_tofu() {
  log "Initializing OpenTofu backend for $TARGET_NAME/$STAGE_NAME"
  tofu -chdir="$INFRA_DIR" init \
    -backend-config="$BACKEND_FILE" \
    -input=false \
    -reconfigure
}

apply_stack() {
  local image_tag="$1"
  require_immutable_image_tag "$image_tag"
  log "Applying OpenTofu stack for $TARGET_NAME/$STAGE_NAME with image tag $image_tag"
  tofu -chdir="$INFRA_DIR" apply -input=false -auto-approve "${tofu_args[@]}" "-var=image_tag=$image_tag"
}

plan_stack() {
  local image_tag="$1"
  require_immutable_image_tag "$image_tag"
  log "Planning OpenTofu stack for $TARGET_NAME/$STAGE_NAME with image tag $image_tag"
  tofu -chdir="$INFRA_DIR" plan -input=false "${tofu_args[@]}" "-var=image_tag=$image_tag"
}

bootstrap_state() {
  "$SCRIPT_DIR/bootstrap-state.sh" "$TARGET_NAME" "$STAGE_NAME"
}

ensure_secret() {
  "$SCRIPT_DIR/bootstrap-secrets.sh" "$TARGET_NAME" "$STAGE_NAME" ensure
}

check_secret() {
  "$SCRIPT_DIR/bootstrap-secrets.sh" "$TARGET_NAME" "$STAGE_NAME" check
}

load_auth_bypass_cidrs() {
  local output secret_value normalized

  if ! output="$(
    aws secretsmanager get-secret-value \
      --secret-id "$AUTH_BYPASS_CIDRS_SECRET_NAME" \
      --query SecretString \
      --output text 2>&1
  )"; then
    if [[ "$output" == *"ResourceNotFoundException"* ]]; then
      log "No auth bypass CIDR secret found: $AUTH_BYPASS_CIDRS_SECRET_NAME"
      return 0
    fi
    fail "Could not read auth bypass CIDR secret '$AUTH_BYPASS_CIDRS_SECRET_NAME': $output"
  fi

  secret_value="$output"
  if [[ -z "$secret_value" || "$secret_value" == "None" ]]; then
    log "Auth bypass CIDR secret is empty: $AUTH_BYPASS_CIDRS_SECRET_NAME"
    return 0
  fi

  # Keep bypass ranges out of tfvars so emergency/VPN access can change without
  # leaving sensitive network details in the repo.
  normalized="$(
    python3 -c 'import json, sys
raw = sys.stdin.read().strip()
try:
    value = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit(f"Secret must be a JSON array of CIDR strings: {exc}") from exc
if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
    raise SystemExit("Secret must be a JSON array of non-empty CIDR strings")
print(json.dumps(value))
' <<<"$secret_value"
  )" || fail "Invalid auth bypass CIDR secret: $AUTH_BYPASS_CIDRS_SECRET_NAME"

  export TF_VAR_auth_bypass_cidrs="$normalized"
  log "Loaded auth bypass CIDRs from Secrets Manager: $AUTH_BYPASS_CIDRS_SECRET_NAME"
}

start_build() {
  local project_name commit
  project_name="$(tofu_output codebuild_project_name)"
  commit="$(git_commit)"
  [[ -n "$project_name" ]] || fail "CodeBuild project is not available yet. Apply infrastructure first."

  log "Starting CodeBuild project: $project_name at ${commit:0:12}"
  aws codebuild start-build \
    --project-name "$project_name" \
    --source-version "$commit" \
    --query "build.id" \
    --output text
}

print_build_logs_hint() {
  local group_name="$1"
  local stream_name="$2"
  local deep_link="$3"

  if [[ "$group_name" != "None" && "$stream_name" != "None" ]]; then
    log "Recent CodeBuild log lines:"
    aws logs get-log-events \
      --log-group-name "$group_name" \
      --log-stream-name "$stream_name" \
      --limit 80 \
      --query "events[].message" \
      --output text || true
  fi

  [[ "$deep_link" == "None" ]] || log "CodeBuild logs: $deep_link"
}

watch_build() {
  local build_id="$1"
  local previous=""
  local status phase group_name stream_name deep_link fields

  log "Watching CodeBuild build: $build_id"
  while true; do
    fields="$(
      aws codebuild batch-get-builds \
        --ids "$build_id" \
        --query "builds[0].[buildStatus,currentPhase,logs.groupName,logs.streamName,logs.deepLink]" \
        --output text
    )"
    read -r status phase group_name stream_name deep_link <<<"$fields"

    if [[ "$status:$phase" != "$previous" ]]; then
      log "CodeBuild status: $status ($phase)"
      previous="$status:$phase"
    fi

    case "$status" in
      SUCCEEDED)
        return 0
        ;;
      FAILED | FAULT | STOPPED | TIMED_OUT)
        print_build_logs_hint "$group_name" "$stream_name" "$deep_link"
        fail "CodeBuild finished with status: $status"
        ;;
    esac

    sleep 10
  done
}

current_task_definition_arn() {
  local cluster service task_definition
  cluster="$(tofu_output ecs_cluster_name)"
  service="$(tofu_output ecs_service_name)"
  [[ -n "$cluster" && -n "$service" ]] || return 0

  task_definition="$(
    aws ecs describe-services \
      --cluster "$cluster" \
      --services "$service" \
      --query "services[0].taskDefinition" \
      --output text 2>/dev/null
  )" || return 0

  [[ "$task_definition" == "None" ]] || printf '%s\n' "$task_definition"
}

current_image_tag() {
  local task_definition image
  task_definition="$(current_task_definition_arn)"
  [[ -n "$task_definition" ]] || return 0

  image="$(
    aws ecs describe-task-definition \
      --task-definition "$task_definition" \
      --query "taskDefinition.containerDefinitions[?name=='app'] | [0].image" \
      --output text 2>/dev/null
  )" || return 0

  [[ -n "$image" && "$image" != "None" ]] || return 0
  [[ "$image" != *@* ]] || fail "Current ECS image uses a digest, but this deploy path expects an image tag: $image"
  printf '%s\n' "${image##*:}"
}

infra_image_tag() {
  local image_tag

  if [[ -n "${DATA_CHORD_IMAGE_TAG:-}" ]]; then
    printf '%s\n' "$DATA_CHORD_IMAGE_TAG"
    return 0
  fi

  image_tag="$(current_image_tag)"
  if [[ -n "$image_tag" ]]; then
    # Infra-only changes should keep the running app image unless the operator
    # explicitly provides a replacement tag.
    printf '%s\n' "$image_tag"
    return 0
  fi

  fail "No deployed image tag is available. Run an app deploy after the base infrastructure exists, or set DATA_CHORD_IMAGE_TAG to an existing immutable image tag."
}

print_target_health() {
  local target_group_arn
  target_group_arn="$(tofu_output target_group_arn)"
  [[ -n "$target_group_arn" ]] || return 0

  log "Current target health:"
  aws elbv2 describe-target-health \
    --target-group-arn "$target_group_arn" \
    --query "TargetHealthDescriptions[].{Target:Target.Id,State:TargetHealth.State,Reason:TargetHealth.Reason,Description:TargetHealth.Description}" \
    --output table || true
}

watch_ecs_rollout() {
  local cluster service deadline fields state desired running pending event previous
  cluster="$(tofu_output ecs_cluster_name)"
  service="$(tofu_output ecs_service_name)"
  [[ -n "$cluster" && -n "$service" ]] || fail "ECS service outputs are not available yet."

  log "Watching ECS rollout: $cluster/$service"
  deadline=$((SECONDS + 900))
  previous=""

  while (( SECONDS < deadline )); do
    fields="$(
      aws ecs describe-services \
        --cluster "$cluster" \
        --services "$service" \
        --query "services[0].deployments[?status=='PRIMARY'] | [0].[rolloutState,desiredCount,runningCount,pendingCount]" \
        --output text
    )"
    read -r state desired running pending <<<"$fields"
    event="$(
      aws ecs describe-services \
        --cluster "$cluster" \
        --services "$service" \
        --query "services[0].events[0].message" \
        --output text
    )"

    if [[ "$state:$desired:$running:$pending:$event" != "$previous" ]]; then
      log "ECS status: state=${state:-unknown} desired=${desired:-?} running=${running:-?} pending=${pending:-?}"
      [[ "$event" == "None" ]] || log "Latest ECS event: $event"
      previous="$state:$desired:$running:$pending:$event"
    fi

    if [[ "$state" == "COMPLETED" && "$running" == "$desired" && "$pending" == "0" ]]; then
      log "ECS rollout is stable"
      return 0
    fi

    sleep 15
  done

  print_target_health
  fail "Timed out waiting for ECS rollout"
}

print_status() {
  local app_url cluster service build_project
  app_url="$(tofu_output app_url)"
  cluster="$(tofu_output ecs_cluster_name)"
  service="$(tofu_output ecs_service_name)"
  build_project="$(tofu_output codebuild_project_name)"

  [[ -z "$app_url" ]] || log "App URL: $app_url"
  [[ -z "$build_project" ]] || log "CodeBuild project: $build_project"

  if [[ -n "$cluster" && -n "$service" ]]; then
    aws ecs describe-services \
      --cluster "$cluster" \
      --services "$service" \
      --query "services[0].{Desired:desiredCount,Running:runningCount,Pending:pendingCount,Status:status,LatestEvent:events[0].message}" \
      --output table || true
  fi

  print_target_health
}

tail_logs() {
  local ecs_group codebuild_group
  ecs_group="$(tofu_output ecs_log_group)"
  codebuild_group="$(tofu_output codebuild_log_group)"

  [[ -z "$ecs_group" ]] || {
    log "Recent ECS app logs: $ecs_group"
    aws logs tail "$ecs_group" --since 30m || true
  }

  [[ -z "$codebuild_group" ]] || {
    log "Recent CodeBuild logs: $codebuild_group"
    aws logs tail "$codebuild_group" --since 30m || true
  }
}

run_app_deploy() {
  local build_id image_tag app_url

  log "Deployment target: $TARGET_NAME/$STAGE_NAME (AWS profile: $AWS_PROFILE)"
  ensure_deployable_git_state
  image_tag="$(git_image_tag)"
  bootstrap_state
  ensure_secret
  load_auth_bypass_cidrs
  init_tofu

  build_id="$(start_build)"
  watch_build "$build_id"
  apply_stack "$image_tag"
  watch_ecs_rollout

  app_url="$(tofu_output app_url)"
  log "Deploy complete: $app_url"
}

run_infra_deploy() {
  local before_task_definition after_task_definition image_tag app_url

  log "Deployment target: $TARGET_NAME/$STAGE_NAME (AWS profile: $AWS_PROFILE)"
  bootstrap_state
  ensure_secret
  load_auth_bypass_cidrs
  init_tofu

  image_tag="$(infra_image_tag)"
  before_task_definition="$(current_task_definition_arn)"
  apply_stack "$image_tag"
  after_task_definition="$(current_task_definition_arn)"

  # Pure infrastructure edits do not always create a new task definition, so
  # only wait for an ECS rollout when there is actually a new task to watch.
  if [[ -n "$after_task_definition" && "$after_task_definition" != "$before_task_definition" ]]; then
    watch_ecs_rollout
  else
    log "No ECS task definition change detected; skipping rollout watch"
  fi

  app_url="$(tofu_output app_url)"
  log "Infra deploy complete: $app_url"
}

run_plan() {
  local image_tag

  log "Deployment target: $TARGET_NAME/$STAGE_NAME (AWS profile: $AWS_PROFILE)"
  bootstrap_state
  check_secret
  load_auth_bypass_cidrs
  init_tofu
  image_tag="$(infra_image_tag)"
  plan_stack "$image_tag"
}

case "$MODE" in
  deploy | deploy-app | app)
    run_app_deploy
    ;;
  deploy-infra | infra)
    run_infra_deploy
    ;;
  plan)
    run_plan
    ;;
  status)
    bootstrap_state
    init_tofu
    print_status
    ;;
  logs)
    bootstrap_state
    init_tofu
    tail_logs
    ;;
  build)
    ensure_deployable_git_state
    bootstrap_state
    init_tofu
    build_id="$(start_build)"
    watch_build "$build_id"
    log "Image build complete. OpenTofu has not been applied, so ECS was not rolled."
    ;;
  *)
    fail "Unknown deploy mode: $MODE"
    ;;
esac
