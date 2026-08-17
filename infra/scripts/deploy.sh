#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_command python3
TARGET_NAME="$(require_target_name "${1:-}")"
STAGE_NAME="$(require_stage_name "${2:-}")"
require_configured_deployment "$TARGET_NAME" "$STAGE_NAME"
MODE="${3:-deploy}"

AWS_REGION_VALUE="$(target_value "$TARGET_NAME" aws_region)"
export AWS_REGION="$AWS_REGION_VALUE"
export AWS_DEFAULT_REGION="$AWS_REGION_VALUE"

require_command aws
require_command git
require_command tofu

tofu_args=(
  "-var=expected_account_id=$(target_value "$TARGET_NAME" expected_account_id)"
  "-var=aws_region=$(target_value "$TARGET_NAME" aws_region)"
  "-var=application_role_boundary_arn=$(target_value "$TARGET_NAME" application_role_boundary_arn)"
  "-var=application_role_path=$(target_value "$TARGET_NAME" application_role_path)"
  "-var=deployment_target=$TARGET_NAME"
  "-var=environment=$STAGE_NAME"
)
if using_external_contract; then
  tofu_args+=(
    "-var=application_repository_url=$(contract_value application_repository_url)"
    "-var=domain_label=$(contract_value domain_label)"
    "-var=github_app_secret_name=$(contract_value github_app_secret_name)"
    "-var=hosted_zone_name=$(contract_value hosted_zone_name)"
    "-var=netrias_api_key_secret_name=$(contract_value netrias_api_key_secret_name)"
  )
else
  tofu_args=(
    "-var-file=$(common_tfvars_path "$TARGET_NAME")"
    "-var-file=$(stage_tfvars_path "$TARGET_NAME" "$STAGE_NAME")"
    "${tofu_args[@]}"
  )
fi
PLAN_DIR=""

git_branch() {
  git -C "$REPO_DIR" branch --show-current
}

git_commit() {
  git -C "$REPO_DIR" rev-parse HEAD
}

git_image_tag() {
  git -C "$REPO_DIR" rev-parse --short=12 HEAD
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

  [[ -z "$dirty_status" ]] || fail "Working tree has uncommitted changes. Commit them before deploying."

  if using_external_contract; then
    local expected_commit
    expected_commit="$(contract_value application_commit)"
    [[ "$commit" == "$expected_commit" ]] ||
      fail "DataChord checkout is '$commit', not pinned commit '$expected_commit'."
    log "Deploy source: pinned commit @ ${commit:0:12}"
    return 0
  fi

  [[ -n "$branch" ]] || fail "Cannot deploy from a detached HEAD."

  # CodeBuild pulls from GitHub, so local-only commits would build a different
  # image than the one this script is about to deploy.
  if ! remote_branch_matches_commit "$branch" "$commit"; then
    fail "origin/$branch does not match local HEAD. Push branch '$branch' before deploying."
  fi

  log "Deploy source: $branch @ ${commit:0:12}"
}

create_plan_directory() {
  local plans_root="$REPO_DIR/build/plans"

  mkdir -p "$plans_root"
  PLAN_DIR="$(mktemp -d "$plans_root/$TARGET_NAME-$STAGE_NAME.XXXXXX")"
}

show_and_check_plan() {
  local plan_file="$1"

  tofu -chdir="$INFRA_DIR" show "$plan_file"
  tofu -chdir="$INFRA_DIR" show -json "$plan_file" | python3 -c '
import json
import sys

plan = json.load(sys.stdin)
resources = plan.get("resource_changes") or []
for resource in resources:
    address = resource.get("address")
    actions = resource.get("change", {}).get("actions", [])
    if address == "aws_s3_bucket.workflow" and "delete" in actions:
        print(
            "The plan would delete or replace the durable workflow bucket.",
            file=sys.stderr,
        )
        raise SystemExit(1)

log_bucket_deletion = next(
    (
        resource
        for resource in resources
        if resource.get("address") == "aws_s3_bucket.alb_logs"
        and "delete" in resource.get("change", {}).get("actions", [])
    ),
    None,
)
if log_bucket_deletion is not None:
    load_balancer = next(
        (resource for resource in resources if resource.get("address") == "aws_lb.app"),
        None,
    )
    load_balancer_before = (
        load_balancer.get("change", {}).get("before") or {}
        if load_balancer is not None
        else {}
    )
    access_logs = load_balancer_before.get("access_logs") or []
    if any(logs.get("enabled", False) for logs in access_logs):
        print(
            "ALB access logging must already be disabled before the log bucket "
            "can be removed in a later saved plan.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    bucket_before = log_bucket_deletion.get("change", {}).get("before") or {}
    if not bucket_before.get("force_destroy", False):
        print(
            "Remove the ALB log bucket in two stages. First retain the bucket "
            "with force_destroy = true while access logs are disabled. Apply "
            "that saved plan. Then remove the bucket in a later saved plan.",
            file=sys.stderr,
        )
        raise SystemExit(1)
'
}

create_saved_plan() {
  local plan_file="$1"
  local image_tag="$2"
  shift 2

  tofu -chdir="$INFRA_DIR" plan \
    -input=false \
    "${tofu_args[@]}" \
    "-var=image_tag=$image_tag" \
    "-out=$plan_file" \
    "$@" >/dev/null
  show_and_check_plan "$plan_file" || fail "OpenTofu plan failed a storage safety check."
  log "Saved plan: $plan_file"
}

apply_saved_plan() {
  local plan_file="$1"

  tofu -chdir="$INFRA_DIR" apply -input=false "$plan_file"
}

confirm_saved_plan() {
  local label="$1"
  local answer

  [[ "${DATA_CHORD_REQUIRE_CONFIRMATION:-0}" == "1" ]] || return 0
  printf 'Apply the displayed %s plan? Type yes to continue: ' "$label" >&2
  IFS= read -r answer
  [[ "$answer" == "yes" ]] || fail "Plan was not approved. No plan changes were applied."
}

apply_stack() {
  local image_tag="$1"
  local plan_file="$PLAN_DIR/final.tfplan"

  log "Planning OpenTofu stack for $TARGET_NAME/$STAGE_NAME with image tag $image_tag"
  create_saved_plan "$plan_file" "$image_tag"
  confirm_saved_plan "application"
  log "Applying the displayed OpenTofu plan"
  apply_saved_plan "$plan_file"
}

reconcile_build_resources() {
  local image_tag="$1"
  local plan_file="$PLAN_DIR/build-resources.tfplan"

  log "Reconciling build resources for $TARGET_NAME/$STAGE_NAME"
  create_saved_plan \
    "$plan_file" \
    "$image_tag" \
    -target=aws_codebuild_project.app_image
  confirm_saved_plan "build prerequisite"
  apply_saved_plan "$plan_file"
}

plan_stack() {
  local image_tag="$1"
  local plan_file="$PLAN_DIR/final.tfplan"

  log "Planning OpenTofu stack for $TARGET_NAME/$STAGE_NAME with image tag $image_tag"
  create_saved_plan "$plan_file" "$image_tag" -lock=false
}

require_application_state_handoff_complete() {
  local handoff_file="$INFRA_DIR/migration-handoff.tf"
  local legacy_addresses state_addresses address

  if [[ -e "$handoff_file" ]]; then
    [[ -r "$handoff_file" ]] || fail "Could not read migration handoff addresses: $handoff_file"
    legacy_addresses="$(awk '$1 == "from" && $2 == "=" { print $3 }' "$handoff_file")" ||
      fail "Could not read migration handoff addresses: $handoff_file"
  else
    legacy_addresses=""
  fi

  if ! state_addresses="$(tofu -chdir="$INFRA_DIR" state list 2>&1)"; then
    if [[ "$state_addresses" == *"No state file was found"* ]]; then
      return 0
    fi
    fail "Could not inspect application state before deploy: $state_addresses"
  fi

  while IFS= read -r address; do
    [[ -n "$address" ]] || continue
    if grep -Fqx -- "$address" <<<"$state_addresses"; then
      fail "Legacy BDF application state is present. Complete the privileged saved-plan handoff in infra/README.md before deploy."
    fi
  done <<<"$legacy_addresses"
}

check_secret() {
  "$SCRIPT_DIR/bootstrap-secrets.sh" "$TARGET_NAME" "$STAGE_NAME" check
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

ecr_repository_name() {
  local repository_url

  repository_url="$(tofu_output ecr_repository_url)"
  [[ -n "$repository_url" ]] || fail "ECR repository output is not available. Apply the build prerequisites first."
  printf '%s\n' "${repository_url##*/}"
}

image_exists() {
  local image_tag="$1"
  local repository_name="$2"
  local output

  if output="$(aws ecr describe-images --repository-name "$repository_name" --image-ids "imageTag=$image_tag" 2>&1)"; then
    return 0
  fi
  if [[ "$output" != *"ImageNotFoundException"* ]]; then
    fail "Could not check ECR image '$repository_name:$image_tag': $output"
  fi

  return 1
}

ensure_image() {
  local image_tag="$1"
  local repository_name build_id

  repository_name="$(ecr_repository_name)"
  if image_exists "$image_tag" "$repository_name"; then
    log "Immutable image already exists; reusing $repository_name:$image_tag"
    return 0
  fi

  build_id="$(start_build)"
  watch_build "$build_id"
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
  local deadline status phase group_name stream_name deep_link fields

  log "Watching CodeBuild build: $build_id"
  deadline=$((SECONDS + (65 * 60)))
  while (( SECONDS < deadline )); do
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

  print_build_logs_hint "${group_name:-None}" "${stream_name:-None}" "${deep_link:-None}"
  fail "Timed out waiting for CodeBuild after 65 minutes"
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

plan_image_tag() {
  local image_tag

  if using_external_contract; then
    ensure_deployable_git_state
    git_image_tag
    return 0
  fi

  if [[ -n "${DATA_CHORD_IMAGE_TAG:-}" ]]; then
    printf '%s\n' "$DATA_CHORD_IMAGE_TAG"
    return 0
  fi

  image_tag="$(current_image_tag)"
  if [[ -n "$image_tag" ]]; then
    printf '%s\n' "$image_tag"
    return 0
  fi

  ensure_deployable_git_state
  git_image_tag
}

print_target_health() {
  local target_group_arn
  target_group_arn="$(required_tofu_output target_group_arn)"

  log "Current target health:"
  aws elbv2 describe-target-health \
    --target-group-arn "$target_group_arn" \
    --query "TargetHealthDescriptions[].{Target:Target.Id,State:TargetHealth.State,Reason:TargetHealth.Reason,Description:TargetHealth.Description}" \
    --output table || fail "Could not read application target health."
}

require_healthy_targets() {
  local target_group_arn states state

  target_group_arn="$(tofu_output target_group_arn)"
  [[ -n "$target_group_arn" ]] || fail "The application target group is unavailable."
  states="$(
    aws elbv2 describe-target-health \
      --target-group-arn "$target_group_arn" \
      --query "TargetHealthDescriptions[].TargetHealth.State" \
      --output text
  )" || fail "Could not read application target health."
  [[ -n "$states" && "$states" != "None" ]] || fail "The application target group has no registered targets."

  for state in $states; do
    [[ "$state" == "healthy" ]] || fail "The application target is not healthy: $state"
  done
  log "Application targets are healthy"
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
  local app_url cluster service
  app_url="$(required_tofu_output app_url)"
  cluster="$(required_tofu_output ecs_cluster_name)"
  service="$(required_tofu_output ecs_service_name)"

  log "App URL: $app_url"

  aws ecs describe-services \
    --cluster "$cluster" \
    --services "$service" \
    --query "services[0].{Desired:desiredCount,Running:runningCount,Pending:pendingCount,Status:status,LatestEvent:events[0].message}" \
    --output table || fail "Could not read ECS service status."

  print_target_health
}

run_app_deploy() {
  local image_tag app_url

  require_deployer_identity "$TARGET_NAME"
  log "Using verified AWS deployment-role credentials"
  ensure_deployable_git_state
  image_tag="$(git_image_tag)"
  init_tofu "$TARGET_NAME" "$STAGE_NAME"
  require_application_state_handoff_complete
  check_secret
  create_plan_directory

  reconcile_build_resources "$image_tag"
  ensure_image "$image_tag"
  apply_stack "$image_tag"
  watch_ecs_rollout
  require_healthy_targets

  app_url="$(required_tofu_output app_url)"
  log "Deploy complete: $app_url"
}

run_plan() {
  local image_tag

  require_deployer_identity "$TARGET_NAME"
  log "Using verified AWS deployment-role credentials"
  check_secret
  init_tofu "$TARGET_NAME" "$STAGE_NAME"
  create_plan_directory
  image_tag="$(plan_image_tag)"
  plan_stack "$image_tag"
}

case "$MODE" in
  deploy)
    run_app_deploy
    ;;
  plan)
    run_plan
    ;;
  status)
    require_deployer_identity "$TARGET_NAME"
    init_tofu "$TARGET_NAME" "$STAGE_NAME"
    print_status
    ;;
  output-url)
    require_deployer_identity "$TARGET_NAME"
    init_tofu "$TARGET_NAME" "$STAGE_NAME" >/dev/null
    required_tofu_output app_url
    ;;
  *)
    fail "Unknown deploy mode: $MODE"
    ;;
esac
