#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="$TEST_DIR/../scripts/deploy.sh"
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
    ;;
  "secretsmanager get-secret-value")
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
      app_url)
        [[ -f "$MOCK_FULL_APPLIED" ]] || exit 1
        printf '"https://data-chord-staging.apps.netrias.com"\n'
        ;;
      *)
        exit 1
        ;;
    esac
    ;;
  *" apply "*)
    if [[ "$args" == *"-target=aws_codebuild_project.app_image"* ]]; then
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

  assert_call_contains "tofu bootstrap-apply " "-target=aws_codebuild_project.app_image" "$calls_file"
  assert_call_contains "tofu bootstrap-apply " "-var=environment=staging" "$calls_file"
  assert_call_contains "tofu full-apply " "-var=environment=staging" "$calls_file"
  assert_call_contains "tofu full-apply " "-var=netrias_api_key_secret_name=data-chord/staging/netrias-api-key" "$calls_file"
  assert_call_contains "tofu init " "-backend-config=key=datachord/netrias/staging/tofu.tfstate" "$calls_file"
  assert_call_absent "aws secret-write " "$calls_file"
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

  assert_call_contains "tofu bootstrap-apply " "-target=aws_codebuild_project.app_image" "$calls_file"
  assert_call_contains "tofu state-list " "state list" "$calls_file"
  assert_call_absent "aws start-build " "$calls_file"
  [[ -f "$scenario_root/full-applied" ]] || fail_test "Retry did not run the full application apply"
}

run_drifted_build_prerequisites() {
  local scenario_root="$TEST_ROOT/drifted-build-prerequisites"
  local calls_file="$scenario_root/calls"
  local reconcile_line build_line full_apply_line
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
    MOCK_RECONCILED_FILE="$scenario_root/aws-build-reconciled" \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging deploy >/dev/null 2>&1

  reconcile_line="$(grep -n '^tofu bootstrap-apply ' "$calls_file" | cut -d: -f1)"
  build_line="$(grep -n '^aws start-build ' "$calls_file" | cut -d: -f1)"
  full_apply_line="$(grep -n '^tofu full-apply ' "$calls_file" | cut -d: -f1)"
  (( reconcile_line < build_line && build_line < full_apply_line )) ||
    fail_test "Stored build output did not reconcile before image build and full apply"
}

run_empty_state_plan() {
  local scenario_root="$TEST_ROOT/empty-state-plan"
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
    NETRIAS_API_KEY=must-not-write-during-plan \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging plan >/dev/null 2>&1

  assert_call_contains "tofu init " "-backend-config=key=datachord/netrias/staging/tofu.tfstate" "$calls_file"
  assert_call_contains "tofu plan " "-var=environment=staging" "$calls_file"
  assert_call_contains "tofu plan " "-var=image_tag=0123456789ab" "$calls_file"
  assert_call_absent "tofu bootstrap-apply " "$calls_file"
  assert_call_absent "tofu full-apply " "$calls_file"
  assert_call_absent "aws start-build " "$calls_file"
  assert_call_absent "aws secret-write " "$calls_file"
}

run_empty_state_infra_deploy_fails() {
  local scenario_root="$TEST_ROOT/empty-state-infra-deploy"
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
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging deploy-infra >/dev/null 2>&1; then
    fail_test "Infrastructure-only deployment accepted an empty state without an existing image tag"
  fi

  assert_call_absent "tofu full-apply " "$calls_file"
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

run_infra_image_override() {
  local scenario_root="$TEST_ROOT/infra-image-override"
  local calls_file="$scenario_root/calls"
  mkdir -p "$scenario_root"
  : >"$calls_file"

  PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    DATA_CHORD_IMAGE_TAG=operator123456 \
    MOCK_ACCOUNT_ID=945365518758 \
    MOCK_BUILD_READY="$scenario_root/build-ready" \
    MOCK_CALLS="$calls_file" \
    MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
    MOCK_FULL_APPLIED="$scenario_root/full-applied" \
    MOCK_STATE_ADDRESSES=aws_s3_bucket.workflow \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging deploy-infra >/dev/null 2>&1

  assert_call_contains "tofu full-apply " "-var=image_tag=operator123456" "$calls_file"
  assert_call_absent "tofu bootstrap-apply " "$calls_file"
  assert_call_absent "aws start-build " "$calls_file"
}

run_dirty_infra_deploy_fails() {
  local scenario_root="$TEST_ROOT/dirty-infra-deploy"
  local calls_file="$scenario_root/calls"
  mkdir -p "$scenario_root"
  : >"$calls_file"

  if PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    DATA_CHORD_IMAGE_TAG=operator123456 \
    MOCK_ACCOUNT_ID=945365518758 \
    MOCK_BUILD_READY="$scenario_root/build-ready" \
    MOCK_CALLS="$calls_file" \
    MOCK_COMMIT=0123456789abcdef0123456789abcdef01234567 \
    MOCK_FULL_APPLIED="$scenario_root/full-applied" \
    MOCK_GIT_DIRTY=1 \
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging deploy-infra >/dev/null 2>&1; then
    fail_test "Infrastructure-only deploy accepted a dirty worktree"
  fi

  assert_call_absent "tofu init " "$calls_file"
  assert_no_deploy_writes "$calls_file"
}

run_build_reuses_immutable_image() {
  local scenario_root="$TEST_ROOT/build-image-reuse"
  local calls_file="$scenario_root/calls"
  local init_line state_line prerequisite_line image_line
  mkdir -p "$scenario_root"
  : >"$calls_file"

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
    "$DEPLOY_SCRIPT" netrias staging build >/dev/null 2>&1

  assert_call_contains "tofu state-list " "state list" "$calls_file"
  assert_call_contains "tofu bootstrap-apply " "-target=aws_codebuild_project.app_image" "$calls_file"
  assert_call_contains "aws image-check " "imageTag=0123456789ab" "$calls_file"
  assert_call_absent "aws start-build " "$calls_file"
  assert_call_absent "tofu full-apply " "$calls_file"
  assert_call_absent "aws secret-write " "$calls_file"

  init_line="$(grep -n '^tofu init ' "$calls_file" | cut -d: -f1)"
  state_line="$(grep -n '^tofu state-list ' "$calls_file" | cut -d: -f1)"
  prerequisite_line="$(grep -n '^tofu bootstrap-apply ' "$calls_file" | cut -d: -f1)"
  image_line="$(grep -n '^aws image-check ' "$calls_file" | cut -d: -f1)"
  (( init_line < state_line && state_line < prerequisite_line && prerequisite_line < image_line )) ||
    fail_test "Image build did not initialize, inspect state, reconcile prerequisites, and check the image in order"
}

run_legacy_state_guard() {
  local legacy_addresses=()
  local address calls_file mode scenario_root

  [[ -e "$HANDOFF_FILE" ]] || return 0
  [[ -r "$HANDOFF_FILE" ]] || fail_test "Migration handoff file is not readable"
  while IFS= read -r address; do
    [[ -z "$address" ]] || legacy_addresses+=("$address")
  done < <(awk '$1 == "from" && $2 == "=" { print $3 }' "$HANDOFF_FILE")
  (( ${#legacy_addresses[@]} > 0 )) || fail_test "Migration handoff file has no legacy addresses"

  for address in "${legacy_addresses[@]}"; do
    for mode in deploy deploy-infra build; do
      scenario_root="$TEST_ROOT/legacy-${mode}-${address//./-}"
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
        "$DEPLOY_SCRIPT" bdf staging "$mode" >/dev/null 2>&1; then
        fail_test "$mode accepted legacy state address: $address"
      fi

      assert_no_deploy_writes "$calls_file"
    done
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
run_drifted_build_prerequisites
run_empty_state_plan
run_empty_state_infra_deploy_fails
run_existing_state_plan
run_plan_image_override
run_infra_image_override
run_dirty_infra_deploy_fails
run_build_reuses_immutable_image
run_legacy_state_guard

printf 'Deployment flow tests passed.\n'
