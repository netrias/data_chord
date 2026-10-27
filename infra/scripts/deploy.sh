#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

ENV_NAME="$(require_env_name "${1:-}")"
MODE="${2:-deploy}"
BACKEND_FILE="$(backend_config_path "$ENV_NAME")"
COMMON_TFVARS_FILE="$(common_tfvars_path)"
ENV_TFVARS_FILE="$(env_tfvars_path "$ENV_NAME")"

export AWS_PROFILE="${AWS_PROFILE:-strides}"
AWS_REGION_VALUE="$(env_tfvar_value "$ENV_NAME" aws_region)"
[[ -n "$AWS_REGION_VALUE" ]] || fail "aws_region is missing in $COMMON_TFVARS_FILE or $ENV_TFVARS_FILE"
export AWS_REGION="$AWS_REGION_VALUE"
export AWS_DEFAULT_REGION="$AWS_REGION_VALUE"

require_command aws
require_command git
require_command tofu

[[ -f "$BACKEND_FILE" ]] || fail "Missing backend config: $BACKEND_FILE"
[[ -f "$COMMON_TFVARS_FILE" ]] || fail "Missing common config: $COMMON_TFVARS_FILE"
[[ -f "$ENV_TFVARS_FILE" ]] || fail "Missing env config: $ENV_TFVARS_FILE"

tofu_args=(
  "-var-file=$COMMON_TFVARS_FILE"
  "-var-file=$ENV_TFVARS_FILE"
)

AUTH_BYPASS_CIDRS_SECRET_NAME="data-chord/$ENV_NAME/auth-bypass-cidrs"

git_branch() {
  git -C "$REPO_DIR" branch --show-current
}

git_commit() {
  git -C "$REPO_DIR" rev-parse HEAD
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

  if [[ -n "$dirty_status" ]]; then
    fail "Working tree has uncommitted changes. Commit the reviewed security release first."
  fi

  # CodeBuild pulls from GitHub, so local-only commits would build a different
  # image than the one this script is about to deploy.
  if ! remote_branch_matches_commit "$branch" "$commit"; then
    fail "origin/$branch does not match local HEAD. Push branch '$branch' before deploying."
  fi

  log "Deploy source: $branch @ ${commit:0:12}"
}

init_tofu() {
  log "Initializing OpenTofu backend for $ENV_NAME"
  tofu -chdir="$INFRA_DIR" init \
    -backend-config="$BACKEND_FILE" \
    -input=false \
    -reconfigure
}

check_release_plan() {
  local plan_json="$1" tag expected_digest actual_digest
  local qualification_args=()
  if [[ -n "${DATA_CHORD_RELEASE_QUALIFICATION:-}" ]]; then
    qualification_args=(--qualification "$DATA_CHORD_RELEASE_QUALIFICATION")
  fi
  aws ecs describe-services --cluster data-chord-prod --services data-chord-prod \
    --query 'services[0]' --output json > "$plan_json.running-service.json"
  local running_task
  running_task="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["taskDefinition"])' "$plan_json.running-service.json")"
  aws ecs describe-task-definition --task-definition "$running_task" \
    --query 'taskDefinition.{taskDefinitionArn:taskDefinitionArn,containerDefinitions:containerDefinitions[].{name:name,image:image}}' \
    --output json > "$plan_json.running-task.json"
  python3 -I "$SCRIPT_DIR/check-security-release-plan.py" "$plan_json" "${qualification_args[@]}" \
    --running-service "$plan_json.running-service.json" --running-task "$plan_json.running-task.json" > "$plan_json.images"
  while IFS=$'\t' read -r tag expected_digest; do
    [[ -n "$tag" ]] || continue
    actual_digest="$(aws ecr describe-images --repository-name "data-chord-$ENV_NAME" \
      --image-ids "imageTag=$tag" --query 'imageDetails[0].imageDigest' --output text)"
    [[ "$actual_digest" == "$expected_digest" ]] || fail "Qualified image digest changed: $tag"
  done < "$plan_json.images"
}

apply_stack() {
  local saved_plan="${DATA_CHORD_SAVED_PLAN:?Set DATA_CHORD_SAVED_PLAN to the reviewed plan file}"
  [[ "$saved_plan" == /* ]] || fail "Use the absolute saved-plan path printed by plan"
  ensure_deployable_git_state
  python3 - "$saved_plan" "$(git_commit)" <<'PYRECEIPT'
import hashlib, json, sys
from pathlib import Path
path = Path(sys.argv[1])
record = json.loads(Path(str(path) + '.receipt.json').read_text())
if record != {'source_commit': sys.argv[2], 'plan_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}:
    raise SystemExit('Saved plan or source changed')
PYRECEIPT
  tofu -chdir="$INFRA_DIR" show -json "$saved_plan" > "$saved_plan.json"
  check_release_plan "$saved_plan.json"
  log "Applying the reviewed saved plan: $saved_plan"
  tofu -chdir="$INFRA_DIR" apply -input=false "$saved_plan"
}

plan_stack() {
  local image_tag="$1" saved_plan
  require_immutable_image_tag "$image_tag"
  ensure_deployable_git_state
  mkdir -p "$REPO_DIR/.plans"
  saved_plan="$(mktemp -d "$REPO_DIR/.plans/$ENV_NAME-$(git_commit).XXXXXX")/release.tfplan"
  log "Saving the security plan for $ENV_NAME with image tag $image_tag"
  # Leave the unrelated unconfirmed email-subscription drift untouched.
  tofu -chdir="$INFRA_DIR" plan -input=false "${tofu_args[@]}" \
    -exclude=aws_sns_topic_subscription.alert_email "-var=image_tag=$image_tag" "-out=$saved_plan"
  tofu -chdir="$INFRA_DIR" show -json "$saved_plan" > "$saved_plan.json"
  check_release_plan "$saved_plan.json"
  python3 - "$saved_plan" "$(git_commit)" <<'PYRECEIPT'
import hashlib, json, sys
from pathlib import Path
path = Path(sys.argv[1])
Path(str(path) + '.receipt.json').write_text(json.dumps({'source_commit': sys.argv[2], 'plan_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
PYRECEIPT
  log "Review this plan, then set DATA_CHORD_SAVED_PLAN=$saved_plan and run the apply mode."
}

check_secret() {
  "$SCRIPT_DIR/bootstrap-secrets.sh" "$ENV_NAME" check
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
    --environment-variables-override "name=APP_SOURCE_COMMIT,value=$commit,type=PLAINTEXT" \
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

run_plan() {
  local image_tag
  log "Using AWS profile: $AWS_PROFILE"
  [[ "$(aws sts get-caller-identity --query Account --output text)" == 084828580051 ]] || fail "Wrong AWS account"
  [[ "$AWS_REGION_VALUE" == us-east-2 && "$ENV_NAME" == prod ]] || fail "This security release uses the existing BDF production only"
  check_secret
  load_auth_bypass_cidrs
  init_tofu
  image_tag="$(infra_image_tag)"
  plan_stack "$image_tag"
}

case "$MODE" in
  deploy | deploy-app | app)
    fail "Security release: run plan, review the saved plan, then use apply."
    ;;
  deploy-infra | infra)
    fail "Security release: run plan, review the saved plan, then use apply."
    ;;
  plan)
    run_plan
    ;;
  apply)
    [[ "$(aws sts get-caller-identity --query Account --output text)" == 084828580051 ]] || fail "Wrong AWS account"
    [[ "$AWS_REGION_VALUE" == us-east-2 && "$ENV_NAME" == prod ]] || fail "Wrong security release environment"
    init_tofu
    apply_stack
    watch_ecs_rollout
    print_status
    ;;
  status)
    init_tofu
    print_status
    ;;
  logs)
    init_tofu
    tail_logs
    ;;
  build)
    [[ "$(aws sts get-caller-identity --query Account --output text)" == 084828580051 ]] || fail "Wrong AWS account"
    [[ "$AWS_REGION_VALUE" == us-east-2 && "$ENV_NAME" == prod ]] || fail "Wrong security release environment"
    ensure_deployable_git_state
    init_tofu
    build_id="$(start_build)"
    watch_build "$build_id"
    log "Image build complete. OpenTofu has not been applied, so ECS was not rolled."
    ;;
  *)
    fail "Unknown deploy mode: $MODE"
    ;;
esac
