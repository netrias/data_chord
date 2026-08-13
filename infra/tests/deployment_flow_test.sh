#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="$TEST_DIR/../scripts/deploy.sh"
JUSTFILE="$TEST_DIR/../../Justfile"
HANDOFF_FILE="$TEST_DIR/../migration-handoff.tf"
TEST_ROOT="$(mktemp -d)"
MOCK_BIN="$TEST_ROOT/bin"
mkdir -p "$MOCK_BIN"

fail_test() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

assert_call_contains() {
  local call_prefix="$1"
  local expected="$2"
  local calls_file="$3"
  local call

  call="$(grep -m 1 "^${call_prefix}" "$calls_file")" || fail_test "Missing call: $call_prefix"
  [[ "$call" == *"$expected"* ]] || fail_test "Call '$call_prefix' does not contain '$expected': $call"
}

assert_any_call_contains() {
  local call_prefix="$1"
  local expected="$2"
  local calls_file="$3"

  grep "^${call_prefix}" "$calls_file" | grep -Fq -- "$expected" ||
    fail_test "No '$call_prefix' call contains '$expected'"
}

assert_call_absent() {
  local call_prefix="$1"
  local calls_file="$2"

  if grep -q "^${call_prefix}" "$calls_file"; then
    fail_test "Unexpected call: $call_prefix"
  fi
}

assert_no_deploy_writes() {
  local calls_file="$1"

  assert_call_absent "tofu bootstrap-apply " "$calls_file"
  assert_call_absent "tofu full-apply " "$calls_file"
  assert_call_absent "aws start-build " "$calls_file"
  assert_call_absent "aws secret-write " "$calls_file"
}

assert_saved_plan_apply() {
  local call_prefix="$1"
  local expected_plan_name="$2"
  local calls_file="$3"
  local call

  call="$(grep -m 1 "^${call_prefix}" "$calls_file")" || fail_test "Missing call: $call_prefix"
  [[ "$call" == *"$expected_plan_name"* ]] ||
    fail_test "Apply did not use saved plan '$expected_plan_name': $call"
  [[ "$call" != *"-auto-approve"* ]] || fail_test "Saved-plan apply used -auto-approve: $call"
  [[ "$call" != *"-var="* ]] || fail_test "Saved-plan apply recalculated variables: $call"
}

cat >"$MOCK_BIN/git" <<'MOCK_GIT'
#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == "-C" ]]; then
  shift 2
fi

case "${1:-}" in
  branch)
    printf 'test-branch\n'
    ;;
  rev-parse)
    if [[ "${2:-}" == "--short=12" ]]; then
      printf '%s\n' "${MOCK_COMMIT:0:12}"
    else
      printf '%s\n' "$MOCK_COMMIT"
    fi
    ;;
  status)
    if [[ "${MOCK_GIT_DIRTY:-0}" == "1" ]]; then
      printf ' M infra/main.tf\n'
    fi
    ;;
  ls-remote)
    printf '%s\trefs/heads/test-branch\n' "$MOCK_COMMIT"
    ;;
  *)
    printf 'Unexpected git call: %s\n' "$*" >&2
    exit 2
    ;;
esac
MOCK_GIT

cat >"$MOCK_BIN/aws" <<'MOCK_AWS'
#!/usr/bin/env bash
set -Eeuo pipefail

case "${1:-} ${2:-}" in
  "sts get-caller-identity")
    printf '%s\tarn:aws:sts::%s:assumed-role/datachord-deployer/test\n' "$MOCK_ACCOUNT_ID" "$MOCK_ACCOUNT_ID"
    ;;
  "secretsmanager describe-secret")
    printf 'aws secret-check %s\n' "$*" >>"$MOCK_CALLS"
    ;;
  "secretsmanager get-secret-value")
    printf 'aws bypass-secret-read %s\n' "$*" >>"$MOCK_CALLS"
    printf 'ResourceNotFoundException: secret does not exist\n' >&2
    exit 254
    ;;
  "secretsmanager create-secret" | "secretsmanager put-secret-value")
    printf 'aws secret-write %s\n' "$*" >>"$MOCK_CALLS"
    ;;
  "codebuild start-build")
    [[ -z "${MOCK_RECONCILED_FILE:-}" || -f "$MOCK_RECONCILED_FILE" ]] || {
      printf 'Build prerequisites were not reconciled\n' >&2
      exit 24
    }
    printf 'aws start-build %s\n' "$*" >>"$MOCK_CALLS"
    printf 'build-1\n'
    ;;
  "codebuild batch-get-builds")
    printf 'SUCCEEDED\tCOMPLETED\tNone\tNone\tNone\n'
    ;;
  "ecr describe-images")
    printf 'aws image-check %s\n' "$*" >>"$MOCK_CALLS"
    if [[ "${MOCK_IMAGE_EXISTS:-0}" == "1" ]]; then
      printf '{}\n'
    else
      printf 'ImageNotFoundException: image does not exist\n' >&2
      exit 254
    fi
    ;;
  "ecs describe-services")
    printf 'aws describe-services %s\n' "$*" >>"$MOCK_CALLS"
    if [[ "$*" == *"deployments"* ]]; then
      printf 'COMPLETED\t1\t1\t0\n'
    elif [[ -n "${MOCK_DEPLOYED_IMAGE_TAG:-}" && "$*" == *"taskDefinition"* ]]; then
      printf 'arn:aws:ecs:us-east-2:945365518758:task-definition/data-chord-staging:1\n'
    elif [[ "$*" == *"events[0].message"* ]]; then
      printf 'None\n'
    else
      printf 'None\n'
    fi
    ;;
  "ecs describe-task-definition")
    printf '945365518758.dkr.ecr.us-east-2.amazonaws.com/data-chord-staging:%s\n' "$MOCK_DEPLOYED_IMAGE_TAG"
    ;;
  "elbv2 describe-target-health")
    printf 'aws target-health %s\n' "$*" >>"$MOCK_CALLS"
    printf '%s\n' "${MOCK_TARGET_HEALTH:-healthy}"
    ;;
  *)
    printf 'Unexpected aws call: %s\n' "$*" >&2
    exit 2
    ;;
esac
MOCK_AWS

cat >"$MOCK_BIN/tofu" <<'MOCK_TOFU'
#!/usr/bin/env bash
set -Eeuo pipefail

args="$*"
case " $args " in
  *" init "*)
    printf 'tofu init %s\n' "$args" >>"$MOCK_CALLS"
    ;;
  *" state list "*)
    printf 'tofu state-list %s\n' "$args" >>"$MOCK_CALLS"
    if [[ "${MOCK_NO_STATE_FILE:-0}" == "1" ]]; then
      printf 'No state file was found\n' >&2
      exit 1
    fi
    [[ -z "${MOCK_STATE_ADDRESSES:-}" ]] || printf '%s\n' "$MOCK_STATE_ADDRESSES"
    ;;
  *" output "*)
    if [[ "${MOCK_OUTPUT_COMMAND_FAIL:-0}" == "1" ]]; then
      printf 'AccessDenied: backend state is not readable\n' >&2
      exit 1
    fi
    output_name="${!#}"
    case "$output_name" in
      codebuild_project_name)
        [[ -f "$MOCK_BUILD_READY" ]] || exit 1
        printf '"data-chord-staging-image"\n'
        ;;
      ecr_repository_url)
        [[ -f "$MOCK_BUILD_READY" ]] || exit 1
        printf '"945365518758.dkr.ecr.us-east-2.amazonaws.com/data-chord-staging"\n'
        ;;
      ecs_cluster_name)
        [[ -f "$MOCK_FULL_APPLIED" ]] || exit 1
        printf '"data-chord-staging"\n'
        ;;
      ecs_service_name)
        [[ -f "$MOCK_FULL_APPLIED" ]] || exit 1
        printf '"data-chord-staging"\n'
        ;;
      target_group_arn)
        [[ -f "$MOCK_FULL_APPLIED" ]] || exit 1
        printf '"arn:aws:elasticloadbalancing:us-east-2:945365518758:targetgroup/data-chord-staging-app/1"\n'
        ;;
      app_url)
        if [[ "${MOCK_APP_URL_ABSENT:-0}" == "1" ]]; then
          printf 'Error: Output "app_url" not found\n' >&2
          exit 1
        fi
        [[ -f "$MOCK_FULL_APPLIED" ]] || exit 1
        printf '"https://data-chord-staging.apps.netrias.com"\n'
        ;;
      *)
        exit 1
        ;;
    esac
    ;;
  *" show "*)
    printf 'tofu show %s\n' "$args" >>"$MOCK_CALLS"
    if [[ "$args" == *" -json "* ]]; then
      if [[ -n "${MOCK_PLAN_JSON:-}" ]]; then
        printf '%s\n' "$MOCK_PLAN_JSON"
      else
        printf '%s\n' '{"resource_changes":[]}'
      fi
    fi
    ;;
  *" apply "*)
    if [[ "$args" == *"build-prerequisites.tfplan"* ]]; then
      printf 'tofu bootstrap-apply %s\n' "$args" >>"$MOCK_CALLS"
      [[ "${MOCK_BOOTSTRAP_FAIL:-0}" != "1" ]] || exit 23
      touch "$MOCK_BUILD_READY"
      [[ -z "${MOCK_RECONCILED_FILE:-}" ]] || touch "$MOCK_RECONCILED_FILE"
    else
      printf 'tofu full-apply %s\n' "$args" >>"$MOCK_CALLS"
      touch "$MOCK_FULL_APPLIED"
    fi
    ;;
  *" plan "*)
    printf 'tofu plan %s\n' "$args" >>"$MOCK_CALLS"
    for argument in "$@"; do
      if [[ "$argument" == -out=* ]]; then
        plan_path="${argument#-out=}"
        mkdir -p "$(dirname "$plan_path")"
        printf 'saved plan\n' >"$plan_path"
      fi
    done
    ;;
  *)
    printf 'Unexpected tofu call: %s\n' "$args" >&2
    exit 2
    ;;
esac
MOCK_TOFU

chmod +x "$MOCK_BIN/aws" "$MOCK_BIN/git" "$MOCK_BIN/tofu"

run_first_deploy() {
  local scenario_root="$TEST_ROOT/first-deploy"
  local calls_file="$scenario_root/calls"
  local bootstrap_line build_line full_apply_line output
  mkdir -p "$scenario_root"
  : >"$calls_file"

  if ! output="$(
    PATH="$MOCK_BIN:$PATH" \
      AWS_PROFILE=mock \
      MOCK_ACCOUNT_ID=945365518758 \
      MOCK_BUILD_READY="$scenario_root/build-ready" \
      MOCK_CALLS="$calls_file" \
      MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
      MOCK_FULL_APPLIED="$scenario_root/full-applied" \
      MOCK_NO_STATE_FILE=1 \
      NETRIAS_API_KEY=must-not-write-during-deploy \
      DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
      "$DEPLOY_SCRIPT" netrias staging deploy 2>&1
  )"; then
    printf '%s\n' "$output" >&2
    fail_test "First deployment failed"
  fi

  bootstrap_line="$(grep -n '^tofu bootstrap-apply ' "$calls_file" | cut -d: -f1)"
  build_line="$(grep -n '^aws start-build ' "$calls_file" | cut -d: -f1)"
  full_apply_line="$(grep -n '^tofu full-apply ' "$calls_file" | cut -d: -f1)"
  (( bootstrap_line < build_line && build_line < full_apply_line )) ||
    fail_test "First deployment did not bootstrap, build, and fully apply in order"

  assert_call_contains "tofu plan " "-target=aws_codebuild_project.app_image" "$calls_file"
  assert_call_contains "tofu plan " "-var=environment=staging" "$calls_file"
  assert_saved_plan_apply "tofu bootstrap-apply " "build-prerequisites.tfplan" "$calls_file"
  assert_saved_plan_apply "tofu full-apply " "final.tfplan" "$calls_file"
  assert_any_call_contains "tofu show " "build-prerequisites.tfplan" "$calls_file"
  assert_any_call_contains "tofu show " "final.tfplan" "$calls_file"
  assert_call_absent "aws bypass-secret-read " "$calls_file"
  assert_call_contains "tofu init " "-backend-config=key=datachord/netrias/staging/tofu.tfstate" "$calls_file"
  assert_call_absent "aws secret-write " "$calls_file"
  assert_call_absent "aws bypass-secret-read " "$calls_file"
  [[ -f "$scenario_root/full-applied" ]] || fail_test "Full application apply did not complete"
}

run_failed_bootstrap() {
  local scenario_root="$TEST_ROOT/failed-bootstrap"
  local calls_file="$scenario_root/calls"
  mkdir -p "$scenario_root"
  : >"$calls_file"

  if PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    MOCK_ACCOUNT_ID=945365518758 \
    MOCK_BOOTSTRAP_FAIL=1 \
    MOCK_BUILD_READY="$scenario_root/build-ready" \
    MOCK_CALLS="$calls_file" \
    MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
    MOCK_FULL_APPLIED="$scenario_root/full-applied" \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging deploy >/dev/null 2>&1; then
    fail_test "Deployment succeeded after the build-prerequisite apply failed"
  fi

  assert_call_contains "tofu plan " "-target=aws_codebuild_project.app_image" "$calls_file"
  assert_saved_plan_apply "tofu bootstrap-apply " "build-prerequisites.tfplan" "$calls_file"
  assert_call_absent "aws start-build " "$calls_file"
  assert_call_absent "tofu full-apply " "$calls_file"
}

run_retry_after_image_build() {
  local scenario_root="$TEST_ROOT/image-retry"
  local calls_file="$scenario_root/calls"
  mkdir -p "$scenario_root"
  : >"$calls_file"
  touch "$scenario_root/build-ready"

  PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    MOCK_ACCOUNT_ID=945365518758 \
    MOCK_BUILD_READY="$scenario_root/build-ready" \
    MOCK_CALLS="$calls_file" \
    MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
    MOCK_FULL_APPLIED="$scenario_root/full-applied" \
    MOCK_IMAGE_EXISTS=1 \
    MOCK_STATE_ADDRESSES=aws_s3_bucket.workflow \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging deploy >/dev/null 2>&1

  assert_call_absent "tofu bootstrap-apply " "$calls_file"
  if grep '^tofu plan ' "$calls_file" | grep -q -- '-target=aws_codebuild_project.app_image'; then
    fail_test "Existing build prerequisites used a targeted plan"
  fi
  assert_call_contains "tofu state-list " "state list" "$calls_file"
  assert_call_absent "aws start-build " "$calls_file"
  assert_saved_plan_apply "tofu full-apply " "final.tfplan" "$calls_file"
  [[ -f "$scenario_root/full-applied" ]] || fail_test "Retry did not run the full application apply"
}

run_empty_state_plan() {
  local scenario_root="$TEST_ROOT/empty-state-plan"
  local calls_file="$scenario_root/calls"
  mkdir -p "$scenario_root"
  : >"$calls_file"
  touch "$scenario_root/build-ready"

  PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    MOCK_ACCOUNT_ID=945365518758 \
    MOCK_BUILD_READY="$scenario_root/build-ready" \
    MOCK_CALLS="$calls_file" \
    MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
    MOCK_FULL_APPLIED="$scenario_root/full-applied" \
    NETRIAS_API_KEY=must-not-write-during-plan \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging plan >/dev/null 2>&1

  assert_call_contains "tofu init " "-backend-config=key=datachord/netrias/staging/tofu.tfstate" "$calls_file"
  assert_call_contains "tofu plan " "-var=environment=staging" "$calls_file"
  assert_call_contains "tofu plan " "-var=image_tag=0123456789ab" "$calls_file"
  assert_call_contains "tofu plan " "-lock=false" "$calls_file"
  assert_call_contains "tofu plan " "-out=" "$calls_file"
  assert_call_contains "tofu show " "final.tfplan" "$calls_file"
  assert_call_absent "tofu bootstrap-apply " "$calls_file"
  assert_call_absent "tofu full-apply " "$calls_file"
  assert_call_absent "aws start-build " "$calls_file"
  assert_call_absent "aws secret-write " "$calls_file"
}

run_existing_state_plan() {
  local scenario_root="$TEST_ROOT/existing-state-plan"
  local calls_file="$scenario_root/calls"
  mkdir -p "$scenario_root"
  : >"$calls_file"
  touch "$scenario_root/full-applied"

  PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    MOCK_ACCOUNT_ID=945365518758 \
    MOCK_BUILD_READY="$scenario_root/build-ready" \
    MOCK_CALLS="$calls_file" \
    MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
    MOCK_DEPLOYED_IMAGE_TAG=deployed123456 \
    MOCK_FULL_APPLIED="$scenario_root/full-applied" \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging plan >/dev/null 2>&1

  assert_call_contains "tofu plan " "-var=image_tag=deployed123456" "$calls_file"
  assert_call_absent "aws bypass-secret-read " "$calls_file"
}

run_plan_image_override() {
  local scenario_root="$TEST_ROOT/plan-image-override"
  local calls_file="$scenario_root/calls"
  mkdir -p "$scenario_root"
  : >"$calls_file"
  touch "$scenario_root/full-applied"

  PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    DATA_CHORD_IMAGE_TAG=operator123456 \
    MOCK_ACCOUNT_ID=945365518758 \
    MOCK_BUILD_READY="$scenario_root/build-ready" \
    MOCK_CALLS="$calls_file" \
    MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
    MOCK_DEPLOYED_IMAGE_TAG=deployed123456 \
    MOCK_FULL_APPLIED="$scenario_root/full-applied" \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging plan >/dev/null 2>&1

  assert_call_contains "tofu plan " "-var=image_tag=operator123456" "$calls_file"
}

run_dirty_deploy_fails() {
  local scenario_root="$TEST_ROOT/dirty-deploy"
  local calls_file="$scenario_root/calls"
  mkdir -p "$scenario_root"
  : >"$calls_file"

  if PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    MOCK_ACCOUNT_ID=945365518758 \
    MOCK_BUILD_READY="$scenario_root/build-ready" \
    MOCK_CALLS="$calls_file" \
    MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
    MOCK_FULL_APPLIED="$scenario_root/full-applied" \
    MOCK_GIT_DIRTY=1 \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging deploy >/dev/null 2>&1; then
    fail_test "Deploy accepted a dirty worktree"
  fi

  assert_call_absent "tofu init " "$calls_file"
  assert_no_deploy_writes "$calls_file"
}

run_output_url_contract() {
  local scenario_root="$TEST_ROOT/output-url"
  local calls_file="$scenario_root/calls"
  local output
  mkdir -p "$scenario_root"
  : >"$calls_file"
  touch "$scenario_root/full-applied"

  output="$(
    PATH="$MOCK_BIN:$PATH" \
      AWS_PROFILE=mock \
      MOCK_ACCOUNT_ID=945365518758 \
      MOCK_BUILD_READY="$scenario_root/build-ready" \
      MOCK_CALLS="$calls_file" \
      MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
      MOCK_FULL_APPLIED="$scenario_root/full-applied" \
      DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
      "$DEPLOY_SCRIPT" netrias staging output-url 2>/dev/null
  )"
  [[ "$output" == *"https://data-chord-staging.apps.netrias.com"* ]] || fail_test "output-url did not return the application URL"

  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      AWS_PROFILE=mock \
      MOCK_ACCOUNT_ID=945365518758 \
      MOCK_APP_URL_ABSENT=1 \
      MOCK_BUILD_READY="$scenario_root/build-ready" \
      MOCK_CALLS="$calls_file" \
      MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
      MOCK_FULL_APPLIED="$scenario_root/full-applied" \
      DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
      "$DEPLOY_SCRIPT" netrias staging output-url 2>&1
  )"; then
    fail_test "output-url accepted an absent app_url output"
  fi
  [[ "$output" == *"output 'app_url' is unavailable"* ]] || fail_test "Absent app_url was not identified as unavailable"

  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      AWS_PROFILE=mock \
      MOCK_ACCOUNT_ID=945365518758 \
      MOCK_BUILD_READY="$scenario_root/build-ready" \
      MOCK_CALLS="$calls_file" \
      MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
      MOCK_FULL_APPLIED="$scenario_root/full-applied" \
      MOCK_OUTPUT_COMMAND_FAIL=1 \
      DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
      "$DEPLOY_SCRIPT" netrias staging output-url 2>&1
  )"; then
    fail_test "output-url hid an OpenTofu output failure"
  fi
  [[ "$output" == *"Could not read OpenTofu output 'app_url'"* ]] || fail_test "OpenTofu output failure was reported as a missing URL"
}

run_status_is_read_only_and_reports_failures() {
  local scenario_root="$TEST_ROOT/status"
  local calls_file="$scenario_root/calls"
  local output
  mkdir -p "$scenario_root"
  : >"$calls_file"
  touch "$scenario_root/full-applied"

  PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    MOCK_ACCOUNT_ID=945365518758 \
    MOCK_BUILD_READY="$scenario_root/build-ready" \
    MOCK_CALLS="$calls_file" \
    MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
    MOCK_FULL_APPLIED="$scenario_root/full-applied" \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging status >/dev/null 2>&1

  assert_call_contains "aws describe-services " "Desired:desiredCount" "$calls_file"
  assert_call_contains "aws target-health " "describe-target-health" "$calls_file"
  assert_no_deploy_writes "$calls_file"
  assert_call_absent "tofu plan " "$calls_file"

  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      AWS_PROFILE=mock \
      MOCK_ACCOUNT_ID=945365518758 \
      MOCK_BUILD_READY="$scenario_root/build-ready" \
      MOCK_CALLS="$calls_file" \
      MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
      MOCK_FULL_APPLIED="$scenario_root/full-applied" \
      MOCK_OUTPUT_COMMAND_FAIL=1 \
      DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
      "$DEPLOY_SCRIPT" netrias staging status 2>&1
  )"; then
    fail_test "Status accepted an unreadable OpenTofu state"
  fi
  [[ "$output" == *"Could not read OpenTofu output"* ]] || fail_test "Status did not report the state read failure"
}

run_workflow_bucket_replacement_guard() {
  local scenario_root="$TEST_ROOT/workflow-bucket-guard"
  local calls_file="$scenario_root/calls"
  local output
  mkdir -p "$scenario_root"
  : >"$calls_file"

  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      AWS_PROFILE=mock \
      MOCK_ACCOUNT_ID=945365518758 \
      MOCK_BUILD_READY="$scenario_root/build-ready" \
      MOCK_CALLS="$calls_file" \
      MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
      MOCK_FULL_APPLIED="$scenario_root/full-applied" \
      MOCK_PLAN_JSON='{"resource_changes":[{"address":"aws_s3_bucket.workflow","change":{"actions":["delete","create"]}}]}' \
      DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
      "$DEPLOY_SCRIPT" netrias staging plan 2>&1
  )"; then
    fail_test "Plan accepted workflow bucket replacement"
  fi

  [[ "$output" == *"durable workflow bucket"* ]] || fail_test "Workflow bucket guard did not explain the failure"
  assert_call_absent "tofu bootstrap-apply " "$calls_file"
  assert_call_absent "tofu full-apply " "$calls_file"
}

run_alb_log_bucket_retirement_guard() {
  local scenario_root="$TEST_ROOT/alb-log-bucket-guard"
  local calls_file="$scenario_root/calls"
  local output
  mkdir -p "$scenario_root"
  : >"$calls_file"

  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      AWS_PROFILE=mock \
      MOCK_ACCOUNT_ID=945365518758 \
      MOCK_BUILD_READY="$scenario_root/build-ready" \
      MOCK_CALLS="$calls_file" \
      MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
      MOCK_FULL_APPLIED="$scenario_root/full-applied" \
      MOCK_PLAN_JSON='{"resource_changes":[{"address":"aws_s3_bucket.alb_logs","change":{"actions":["delete"],"before":{"force_destroy":false}}}]}' \
      DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
      "$DEPLOY_SCRIPT" netrias staging plan 2>&1
  )"; then
    fail_test "Plan accepted one-stage ALB log bucket deletion"
  fi

  [[ "$output" == *"two stages"* ]] || fail_test "ALB log bucket guard did not explain the two-stage retirement"
  assert_call_absent "tofu bootstrap-apply " "$calls_file"
  assert_call_absent "tofu full-apply " "$calls_file"
}

run_alb_logging_must_already_be_disabled() {
  local scenario_root="$TEST_ROOT/alb-logging-enabled"
  local calls_file="$scenario_root/calls"
  local output
  mkdir -p "$scenario_root"
  : >"$calls_file"

  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      AWS_PROFILE=mock \
      MOCK_ACCOUNT_ID=945365518758 \
      MOCK_BUILD_READY="$scenario_root/build-ready" \
      MOCK_CALLS="$calls_file" \
      MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
      MOCK_FULL_APPLIED="$scenario_root/full-applied" \
      MOCK_PLAN_JSON='{"resource_changes":[{"address":"aws_s3_bucket.alb_logs","change":{"actions":["delete"],"before":{"force_destroy":true}}},{"address":"aws_lb.app","change":{"actions":["update"],"before":{"access_logs":[{"enabled":true}]},"after":{"access_logs":[{"enabled":false}]}}}]}' \
      DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
      "$DEPLOY_SCRIPT" netrias staging plan 2>&1
  )"; then
    fail_test "Plan accepted ALB log bucket deletion while access logging was still enabled"
  fi

  [[ "$output" == *"already be disabled"* ]] || fail_test "ALB logging guard did not explain the required prior state"
  assert_call_absent "tofu bootstrap-apply " "$calls_file"
  assert_call_absent "tofu full-apply " "$calls_file"
}

run_retired_alb_log_bucket_can_be_removed() {
  local scenario_root="$TEST_ROOT/retired-alb-log-bucket"
  local calls_file="$scenario_root/calls"
  mkdir -p "$scenario_root"
  : >"$calls_file"

  PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    MOCK_ACCOUNT_ID=945365518758 \
    MOCK_BUILD_READY="$scenario_root/build-ready" \
    MOCK_CALLS="$calls_file" \
    MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
    MOCK_FULL_APPLIED="$scenario_root/full-applied" \
    MOCK_PLAN_JSON='{"resource_changes":[{"address":"aws_s3_bucket.alb_logs","change":{"actions":["delete"],"before":{"force_destroy":true}}}]}' \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging plan >/dev/null 2>&1
}

run_unhealthy_target_fails_verification() {
  local scenario_root="$TEST_ROOT/unhealthy-target"
  local calls_file="$scenario_root/calls"
  local output
  mkdir -p "$scenario_root"
  : >"$calls_file"
  touch "$scenario_root/build-ready"

  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      AWS_PROFILE=mock \
      MOCK_ACCOUNT_ID=945365518758 \
      MOCK_BUILD_READY="$scenario_root/build-ready" \
      MOCK_CALLS="$calls_file" \
      MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
      MOCK_FULL_APPLIED="$scenario_root/full-applied" \
      MOCK_IMAGE_EXISTS=1 \
      MOCK_STATE_ADDRESSES=aws_s3_bucket.workflow \
      MOCK_TARGET_HEALTH=unhealthy \
      DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
      "$DEPLOY_SCRIPT" netrias staging deploy 2>&1
  )"; then
    fail_test "Deploy accepted an unhealthy target"
  fi

  [[ "$output" == *"not healthy"* ]] || fail_test "Unhealthy target did not produce a useful error"
  assert_call_contains "aws target-health " "describe-target-health" "$calls_file"
}

run_deploy_requires_application_url() {
  local scenario_root="$TEST_ROOT/missing-deploy-url"
  local calls_file="$scenario_root/calls"
  local output
  mkdir -p "$scenario_root"
  : >"$calls_file"
  touch "$scenario_root/build-ready"

  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      AWS_PROFILE=mock \
      MOCK_ACCOUNT_ID=945365518758 \
      MOCK_APP_URL_ABSENT=1 \
      MOCK_BUILD_READY="$scenario_root/build-ready" \
      MOCK_CALLS="$calls_file" \
      MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
      MOCK_FULL_APPLIED="$scenario_root/full-applied" \
      MOCK_IMAGE_EXISTS=1 \
      MOCK_STATE_ADDRESSES=aws_s3_bucket.workflow \
      DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
      "$DEPLOY_SCRIPT" netrias staging deploy 2>&1
  )"; then
    fail_test "Deploy accepted a missing application URL"
  fi

  [[ "$output" == *"output 'app_url' is unavailable"* ]] || fail_test "Deploy did not report the missing application URL"
}

run_public_command_contract() {
  local public_commands

  public_commands="$(awk -F: '/^[a-z][a-z-]*([^:]*)?:/ { sub(/ .*/, "", $1); print $1 }' "$JUSTFILE")"
  for command in plan deploy status; do
    grep -Fxq "$command" <<<"$public_commands" || fail_test "Justfile is missing public command: $command"
  done
  for removed_command in prepare-stage-secret deploy-infra deploy-plan deploy-status deploy-logs deploy-build invite-user resend-user-invite; do
    if grep -Fxq "$removed_command" <<<"$public_commands"; then
      fail_test "Justfile still exposes extra deployment command: $removed_command"
    fi
  done
  grep -Fq 'AWS_PROFILE={{profile}}' "$JUSTFILE" || fail_test "Public deployment commands do not take an explicit AWS profile"
}

run_removed_write_modes_are_rejected() {
  local calls_file="$TEST_ROOT/removed-modes-calls"
  local mode output
  : >"$calls_file"

  for mode in deploy-infra build logs; do
    if output="$(
      PATH="$MOCK_BIN:$PATH" \
        MOCK_CALLS="$calls_file" \
        "$DEPLOY_SCRIPT" netrias staging "$mode" 2>&1
    )"; then
      fail_test "Removed deployment mode is still accepted: $mode"
    fi
    [[ "$output" == *"Unknown deploy mode"* ]] || fail_test "Removed mode '$mode' did not return the expected error"
  done
}

run_legacy_state_guard() {
  local legacy_addresses=()
  local address calls_file scenario_root

  [[ -e "$HANDOFF_FILE" ]] || return 0
  [[ -r "$HANDOFF_FILE" ]] || fail_test "Migration handoff file is not readable"
  while IFS= read -r address; do
    [[ -z "$address" ]] || legacy_addresses+=("$address")
  done < <(awk '$1 == "from" && $2 == "=" { print $3 }' "$HANDOFF_FILE")
  (( ${#legacy_addresses[@]} > 0 )) || fail_test "Migration handoff file has no legacy addresses"

  for address in "${legacy_addresses[@]}"; do
    scenario_root="$TEST_ROOT/legacy-deploy-${address//./-}"
    calls_file="$scenario_root/calls"
    mkdir -p "$scenario_root"
    : >"$calls_file"

    if PATH="$MOCK_BIN:$PATH" \
      AWS_PROFILE=mock \
      MOCK_ACCOUNT_ID=084828580051 \
      MOCK_BUILD_READY="$scenario_root/build-ready" \
      MOCK_CALLS="$calls_file" \
      MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
      MOCK_FULL_APPLIED="$scenario_root/full-applied" \
      MOCK_STATE_ADDRESSES="$address" \
      NETRIAS_API_KEY=guard-must-stop-before-secret-write \
      DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
      "$DEPLOY_SCRIPT" bdf staging deploy >/dev/null 2>&1; then
      fail_test "Deploy accepted legacy state address: $address"
    fi

    assert_no_deploy_writes "$calls_file"
  done

  scenario_root="$TEST_ROOT/legacy-plan"
  calls_file="$scenario_root/calls"
  mkdir -p "$scenario_root"
  : >"$calls_file"
  PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    MOCK_ACCOUNT_ID=084828580051 \
    MOCK_BUILD_READY="$scenario_root/build-ready" \
    MOCK_CALLS="$calls_file" \
    MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
    MOCK_FULL_APPLIED="$scenario_root/full-applied" \
    MOCK_STATE_ADDRESSES="${legacy_addresses[0]}" \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" bdf staging plan >/dev/null 2>&1
  assert_call_contains "tofu plan " "-var=environment=staging" "$calls_file"
}

run_first_deploy
run_failed_bootstrap
run_retry_after_image_build
run_empty_state_plan
run_existing_state_plan
run_plan_image_override
run_dirty_deploy_fails
run_output_url_contract
run_status_is_read_only_and_reports_failures
run_workflow_bucket_replacement_guard
run_alb_log_bucket_retirement_guard
run_alb_logging_must_already_be_disabled
run_retired_alb_log_bucket_can_be_removed
run_unhealthy_target_fails_verification
run_deploy_requires_application_url
run_public_command_contract
run_removed_write_modes_are_rejected
run_legacy_state_guard

printf 'Deployment flow tests passed.\n'
