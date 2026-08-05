#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="$TEST_DIR/../scripts/deploy.sh"
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
  "codebuild start-build")
    printf 'aws start-build %s\n' "$*" >>"$MOCK_CALLS"
    printf 'build-1\n'
    ;;
  "codebuild batch-get-builds")
    printf 'SUCCEEDED\tCOMPLETED\tNone\tNone\tNone\n'
    ;;
  "ecr describe-images")
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
    elif [[ "$*" == *"events[0].message"* ]]; then
      printf 'None\n'
    else
      printf 'None\n'
    fi
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
    else
      printf 'tofu full-apply %s\n' "$args" >>"$MOCK_CALLS"
      touch "$MOCK_FULL_APPLIED"
    fi
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
    DATA_CHORD_TF_DATA_DIR="$scenario_root/tofu-data" \
    "$DEPLOY_SCRIPT" netrias staging deploy >/dev/null 2>&1

  assert_call_absent "tofu bootstrap-apply " "$calls_file"
  assert_call_absent "aws start-build " "$calls_file"
  [[ -f "$scenario_root/full-applied" ]] || fail_test "Retry did not run the full application apply"
}

run_first_deploy
run_failed_bootstrap
run_retry_after_image_build

printf 'Deployment flow tests passed.\n'
