#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="$TEST_DIR/../scripts/deploy.sh"
JUSTFILE="$TEST_DIR/../../Justfile"
BUILDSPEC="$TEST_DIR/../buildspec.yml"
TEST_ROOT="$(mktemp -d)"
MOCK_BIN="$TEST_ROOT/bin"
MOCK_COMMIT="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
mkdir -p "$MOCK_BIN"

fail_test() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

assert_contains() {
  grep -Fq -- "$2" "$1" || fail_test "Expected '$2' in $1"
}

assert_absent() {
  if grep -Fq -- "$2" "$1"; then
    fail_test "Did not expect '$2' in $1"
  fi
}

assert_before() {
  local first second
  first="$(grep -nF -- "$2" "$1" | head -n 1 | cut -d: -f1 || true)"
  second="$(grep -nF -- "$3" "$1" | head -n 1 | cut -d: -f1 || true)"
  [[ -n "$first" && -n "$second" && "$first" -lt "$second" ]] ||
    fail_test "Expected '$2' before '$3' in $1"
}

cat >"$MOCK_BIN/git" <<'MOCK_GIT'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${1:-}" == "-C" ]] && shift 2
printf 'git %s\n' "$*" >>"$MOCK_CALLS"
case "${1:-}" in
  rev-parse) printf '%s\n' "$MOCK_COMMIT" ;;
  status) ;;
  ls-files) ;;
  ls-remote) printf '%s\trefs/heads/test\n' "$MOCK_COMMIT" ;;
  *) exit 2 ;;
esac
MOCK_GIT

cat >"$MOCK_BIN/aws" <<'MOCK_AWS'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'aws %s\n' "$*" >>"$MOCK_CALLS"
case "${1:-} ${2:-}" in
  "sts get-caller-identity")
    if [[ "${MOCK_ALREADY_DEPLOYER:-0}" == "1" || "${AWS_ACCESS_KEY_ID:-}" == "ASIATEST" ]]; then
      printf '%s\tarn:aws:sts::%s:assumed-role/%s/test\n' "$MOCK_ACCOUNT" "$MOCK_ACCOUNT" "$MOCK_ROLE_NAME"
    else
      printf '111111111111\tarn:aws:iam::111111111111:user/operator\n'
    fi
    ;;
  "sts assume-role") printf 'ASIATEST\tsecret\ttoken\t2099-01-01T00:00:00Z\n' ;;
  "iam get-role")
    printf '/foundation/\t%s\n' "$MOCK_DEPLOYER_BOUNDARY_ARN"
    ;;
  "iam get-policy")
    printf '%s\n' "$MOCK_APPLICATION_BOUNDARY_ARN"
    ;;
  "ecr describe-images")
    if [[ -f "$MOCK_IMAGE_FILE" ]]; then
      printf '{}\n'
    else
      printf 'ImageNotFoundException\n' >&2
      exit 254
    fi
    ;;
  "codebuild start-build")
    if [[ "${MOCK_CODEBUILD_START_FAILURE:-0}" == "1" ]]; then
      printf 'AccessDenied: cannot start build\n' >&2
      exit 254
    fi
    [[ "${MOCK_CODEBUILD_FAILURE:-0}" == "1" ]] || touch "$MOCK_IMAGE_FILE"
    printf 'build-1\n'
    ;;
  "codebuild batch-get-builds")
    if [[ "${MOCK_CODEBUILD_STATUS_FAILURE:-0}" == "1" ]]; then
      printf 'ServiceUnavailable: cannot inspect build\n' >&2
      exit 254
    fi
    if [[ "$*" == *"builds[0].[buildStatus,currentPhase]"* ]]; then
      if [[ "${MOCK_CODEBUILD_FAILURE:-0}" == "1" ]]; then
        printf 'FAILED\tCOMPLETED\n'
      else
        printf 'SUCCEEDED\tCOMPLETED\n'
      fi
      exit 0
    fi
    if [[ "${MOCK_CODEBUILD_NOT_FOUND:-0}" == "1" ]]; then
      printf '{"builds":[],"buildsNotFound":["build-1"]}\n'
    elif [[ "${MOCK_CODEBUILD_WRONG_ID:-0}" == "1" ]]; then
      printf '%s\n' '{"builds":[{"id":"another-build","buildStatus":"SUCCEEDED","currentPhase":"COMPLETED","phases":[]}],"buildsNotFound":[]}'
    elif [[ "${MOCK_CODEBUILD_MISSING_STATUS:-0}" == "1" ]]; then
      printf '%s\n' '{"builds":[{"id":"build-1","currentPhase":"DOWNLOAD_SOURCE","phases":[]}],"buildsNotFound":[]}'
    elif [[ "${MOCK_CODEBUILD_FAILURE:-0}" == "1" ]]; then
      if [[ "${MOCK_CODEBUILD_MALFORMED_PHASE:-0}" == "1" ]]; then
        printf '%s\n' '{"builds":[{"id":"build-1","buildStatus":"FAILED","currentPhase":"COMPLETED","phases":["invalid-phase"]}],"buildsNotFound":[]}'
      elif [[ "${MOCK_CODEBUILD_NO_CONTEXT:-0}" == "1" ]]; then
        printf '%s\n' '{"builds":[{"id":"build-1","buildStatus":"FAILED","currentPhase":"COMPLETED","phases":[{"phaseType":"DOWNLOAD_SOURCE","phaseStatus":"FAILED","contexts":[]}]}],"buildsNotFound":[]}'
      else
        printf '%s\n' '{"builds":[{"id":"build-1","buildStatus":"FAILED","currentPhase":"COMPLETED","phases":[{"phaseType":"DOWNLOAD_SOURCE","phaseStatus":"FAILED","contexts":[{"message":"Access denied to connection test-connection"}]}]}],"buildsNotFound":[]}'
      fi
    else
      printf '%s\n' '{"builds":[{"id":"build-1","buildStatus":"SUCCEEDED","currentPhase":"COMPLETED","phases":[]}],"buildsNotFound":[]}'
    fi
    ;;
  "ecs describe-services") printf 'COMPLETED\t1\t1\t0\n' ;;
  "elbv2 describe-target-health") printf '%s\n' "$MOCK_TARGET_STATES" ;;
  *) printf 'Unexpected AWS call: %s\n' "$*" >&2; exit 2 ;;
esac
MOCK_AWS

cat >"$MOCK_BIN/tofu" <<'MOCK_TOFU'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'tofu %s\n' "$*" >>"$MOCK_CALLS"
arguments=" $* "
case "$arguments" in
  *" init "*) ;;
  *" state pull "*)
    if [[ "${MOCK_EMPTY_STATE:-0}" != "1" ]]; then
      serial="${MOCK_STATE_SERIAL:-1}"
      if [[ "${MOCK_STATE_CHANGE_AFTER_PULL:-0}" == "1" ]]; then
        pulls=0
        [[ -f "$MOCK_STATE_COUNTER_FILE" ]] && pulls="$(<"$MOCK_STATE_COUNTER_FILE")"
        pulls=$((pulls + 1))
        printf '%s\n' "$pulls" >"$MOCK_STATE_COUNTER_FILE"
        if ((pulls > 1)); then
          serial=2
        fi
      fi
      printf '{"lineage":"lineage-1","serial":%s}\n' "$serial"
    fi
    ;;
  *" plan "*)
    for argument in "$@"; do
      if [[ "$argument" == -out=* ]]; then
        : >"${argument#-out=}"
      fi
    done
    if [[ "$arguments" == *" -detailed-exitcode "* ]]; then
      if [[ "${MOCK_CONVERGENCE_AWS_FAILURE:-0}" == "1" ]]; then
        exit 1
      fi
      if [[ "${MOCK_CONVERGENCE_FAILURE:-0}" == "1" ]]; then
        exit 2
      fi
      attempts=0
      [[ -s "$MOCK_CONVERGENCE_COUNTER_FILE" ]] && attempts="$(<"$MOCK_CONVERGENCE_COUNTER_FILE")"
      attempts=$((attempts + 1))
      printf '%s\n' "$attempts" >"$MOCK_CONVERGENCE_COUNTER_FILE"
      if ((attempts <= MOCK_CONVERGENCE_REMAINING)); then
        exit 2
      fi
    fi
    ;;
  *" show "*)
    plan_file="${!#}"
    if [[ "$arguments" == *" -json "* ]]; then
      case "$plan_file" in
        *forecast.tfplan) printf '%s\n' "$MOCK_FORECAST_JSON" ;;
        *prerequisites.tfplan) printf '%s\n' "$MOCK_PREREQUISITE_JSON" ;;
        *application.tfplan) printf '%s\n' "$MOCK_APPLICATION_JSON" ;;
        *) exit 2 ;;
      esac
    else
      printf 'Displayed %s\n' "$plan_file"
    fi
    ;;
  *" apply "*)
    if [[ "${MOCK_APPLY_FAIL:-0}" == "1" && "$arguments" == *" prerequisites.tfplan "* ]]; then
      exit 23
    fi
    ;;
  *" output "*)
    case "${!#}" in
      reference_data_table) printf 'data-chord-staging-reference-data\n' ;;
      ecr_repository_url) printf '945365518758.dkr.ecr.us-east-2.amazonaws.com/data-chord-staging\n' ;;
      codebuild_project_name) printf 'data-chord-staging-image\n' ;;
      ecs_cluster_name | ecs_service_name) printf 'data-chord-staging\n' ;;
      target_group_arn) printf 'arn:aws:elasticloadbalancing:us-east-2:945365518758:targetgroup/app/1\n' ;;
      app_url) printf 'https://data-chord-staging.apps.netrias.com\n' ;;
      *) exit 2 ;;
    esac
    ;;
  *) printf 'Unexpected OpenTofu call: %s\n' "$*" >&2; exit 2 ;;
esac
MOCK_TOFU

cat >"$MOCK_BIN/uv" <<'MOCK_UV'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'uv %s\n' "$*" >>"$MOCK_CALLS"
MOCK_UV

cat >"$MOCK_BIN/sleep" <<'MOCK_SLEEP'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'sleep %s\n' "$*" >>"$MOCK_CALLS"
MOCK_SLEEP

chmod +x "$MOCK_BIN/git" "$MOCK_BIN/aws" "$MOCK_BIN/tofu" "$MOCK_BIN/uv" "$MOCK_BIN/sleep"

safe_plan='{"resource_changes":[{"address":"aws_ecr_repository.app","change":{"actions":["create"]}},{"address":"aws_dynamodb_table.reference_data","change":{"actions":["create"]}},{"address":"aws_s3_bucket.workflow","change":{"actions":["create"]}}]}'
prerequisite_plan='{"resource_changes":[{"address":"aws_ecr_repository.app","change":{"actions":["create"]}},{"address":"aws_s3_bucket.workflow","change":{"actions":["create"]}}]}'
application_plan='{"resource_changes":[{"address":"aws_dynamodb_table.reference_data","change":{"actions":["create"]}}]}'

run_command() {
  local calls="$1"
  shift
  : >"$calls"
  : >"$calls.state-pulls"
  : >"$calls.convergence"
  PATH="$MOCK_BIN:/usr/bin:/bin" \
    MOCK_CALLS="$calls" \
    MOCK_ACCOUNT="${MOCK_ACCOUNT:-945365518758}" \
    MOCK_ROLE_NAME="${MOCK_ROLE_NAME:-datachord-deployer}" \
    MOCK_DEPLOYER_BOUNDARY_ARN="${MOCK_DEPLOYER_BOUNDARY_ARN:-arn:aws:iam::945365518758:policy/datachord-deployer-boundary}" \
    MOCK_APPLICATION_BOUNDARY_ARN="${MOCK_APPLICATION_BOUNDARY_ARN:-arn:aws:iam::945365518758:policy/datachord-application-role-boundary}" \
    MOCK_COMMIT="$MOCK_COMMIT" \
    MOCK_IMAGE_FILE="$TEST_ROOT/image" \
    MOCK_EMPTY_STATE="${MOCK_EMPTY_STATE:-0}" \
    MOCK_ALREADY_DEPLOYER="${MOCK_ALREADY_DEPLOYER:-0}" \
    MOCK_STATE_CHANGE_AFTER_PULL="${MOCK_STATE_CHANGE_AFTER_PULL:-0}" \
    MOCK_CODEBUILD_FAILURE="${MOCK_CODEBUILD_FAILURE:-0}" \
    MOCK_CODEBUILD_NOT_FOUND="${MOCK_CODEBUILD_NOT_FOUND:-0}" \
    MOCK_CODEBUILD_NO_CONTEXT="${MOCK_CODEBUILD_NO_CONTEXT:-0}" \
    MOCK_CODEBUILD_MALFORMED_PHASE="${MOCK_CODEBUILD_MALFORMED_PHASE:-0}" \
    MOCK_CODEBUILD_MISSING_STATUS="${MOCK_CODEBUILD_MISSING_STATUS:-0}" \
    MOCK_CODEBUILD_START_FAILURE="${MOCK_CODEBUILD_START_FAILURE:-0}" \
    MOCK_CODEBUILD_STATUS_FAILURE="${MOCK_CODEBUILD_STATUS_FAILURE:-0}" \
    MOCK_CODEBUILD_WRONG_ID="${MOCK_CODEBUILD_WRONG_ID:-0}" \
    MOCK_CONVERGENCE_AWS_FAILURE="${MOCK_CONVERGENCE_AWS_FAILURE:-0}" \
    MOCK_CONVERGENCE_FAILURE="${MOCK_CONVERGENCE_FAILURE:-0}" \
    MOCK_CONVERGENCE_REMAINING="${MOCK_CONVERGENCE_REMAINING:-0}" \
    MOCK_TARGET_STATES="${MOCK_TARGET_STATES:-healthy}" \
    MOCK_CONVERGENCE_COUNTER_FILE="$calls.convergence" \
    MOCK_STATE_COUNTER_FILE="$calls.state-pulls" \
    AWS_CREDENTIAL_EXPIRATION="${MOCK_CREDENTIAL_EXPIRATION:-}" \
    MOCK_FORECAST_JSON="${MOCK_FORECAST_JSON_OVERRIDE:-$safe_plan}" \
    MOCK_PREREQUISITE_JSON="${MOCK_PREREQUISITE_JSON_OVERRIDE:-$prerequisite_plan}" \
    MOCK_APPLICATION_JSON="${MOCK_APPLICATION_JSON_OVERRIDE:-$application_plan}" \
    DATA_CHORD_PLAN_ROOT="$TEST_ROOT/receipts" \
    DATA_CHORD_BUILD_ROOT="$TEST_ROOT/build" \
    DATA_CHORD_BUILD_WAIT_SECONDS="${DATA_CHORD_BUILD_WAIT_SECONDS:-3900}" \
    "$DEPLOY_SCRIPT" "$@" </dev/null
}

# Given BDF production has no new-system environment file.
bdf_calls="$TEST_ROOT/bdf-calls"
: >"$bdf_calls"
# When the new plan command selects BDF production, then it stops before AWS and OpenTofu.
if PATH="$MOCK_BIN:/usr/bin:/bin" MOCK_CALLS="$bdf_calls" "$DEPLOY_SCRIPT" bdf prod plan >/dev/null 2>&1; then
  fail_test "BDF production plan succeeded"
fi
[[ ! -s "$bdf_calls" ]] || fail_test "BDF production reached an external command"

# Given BDF staging has a new isolated foundation configuration.
bdf_staging_calls="$TEST_ROOT/bdf-staging-calls"
# When the new plan command selects BDF staging.
MOCK_ACCOUNT=084828580051 \
  MOCK_ROLE_NAME=bdf-datachord-deployer \
  MOCK_DEPLOYER_BOUNDARY_ARN=arn:aws:iam::084828580051:policy/bdf-datachord-deployer-boundary \
  MOCK_APPLICATION_BOUNDARY_ARN=arn:aws:iam::084828580051:policy/bdf-datachord-application-role-boundary \
  MOCK_EMPTY_STATE=1 \
  run_command "$bdf_staging_calls" bdf staging plan >/dev/null
# Then it assumes and verifies the new role and initializes only the new state location.
assert_contains "$bdf_staging_calls" "--role-arn arn:aws:iam::084828580051:role/foundation/bdf-datachord-deployer"
assert_contains "$bdf_staging_calls" "iam get-role --role-name bdf-datachord-deployer"
assert_contains "$bdf_staging_calls" "-backend-config=bucket=bdf-datachord-state-084828580051-us-east-2"
assert_contains "$bdf_staging_calls" "-backend-config=key=datachord/bdf/staging/tofu.tfstate"

# Given a new backend returns success with an empty state body.
empty_calls="$TEST_ROOT/empty-state-calls"
# When the first plan runs, then its receipt records an absent state instead of parsing empty JSON.
MOCK_EMPTY_STATE=1 run_command "$empty_calls" netrias staging plan >/dev/null
python3 - "$TEST_ROOT/receipts/netrias-staging.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as receipt_file:
    assert json.load(receipt_file)["state"] == {"kind": "absent"}
PY

# Given one clean pushed commit and unchanged remote state.
plan_calls="$TEST_ROOT/plan-calls"
# When plan runs without stdin.
run_command "$plan_calls" netrias staging plan >/dev/null
# Then it assumes and checks the foundation role, creates a lock-free forecast, and makes no resource write.
assert_contains "$plan_calls" "aws sts assume-role"
assert_contains "$plan_calls" "--duration-seconds 3600"
assert_contains "$plan_calls" "git ls-remote https://github.com/netrias/data_chord.git"
assert_contains "$plan_calls" "tofu -chdir="
assert_contains "$plan_calls" "-lock=false"
assert_absent "$plan_calls" " apply "
assert_absent "$plan_calls" "aws codebuild start-build"
assert_absent "$plan_calls" "uv run"
[[ -f "$TEST_ROOT/receipts/netrias-staging.json" ]] || fail_test "Plan receipt was not saved"

# Given the exact plan receipt still matches code, config, account, and state.
deploy_calls="$TEST_ROOT/deploy-calls"
# When deploy runs without stdin.
MOCK_CONVERGENCE_REMAINING=4 \
  MOCK_TARGET_STATES=$'healthy\tdraining' \
  run_command "$deploy_calls" netrias staging deploy >/dev/null
# Then the policy is applied and converges before CodeBuild, saved plans apply,
# the workflow bucket needs no versioning wait, no data import runs, and the
# healthy new target is accepted while the old target drains.
assert_contains "$deploy_calls" "prerequisites.tfplan"
assert_contains "$deploy_calls" "-target=aws_iam_role_policy.application_build"
assert_contains "$deploy_calls" "-detailed-exitcode"
assert_before "$deploy_calls" " apply -input=false" " -detailed-exitcode"
assert_before "$deploy_calls" " -detailed-exitcode" "aws codebuild start-build"
[[ "$(<"$deploy_calls.convergence")" == "5" ]] ||
  fail_test "Deploy did not wait for the prerequisite policy to converge"
assert_contains "$deploy_calls" "--duration-seconds 14400"
assert_contains "$deploy_calls" "application.tfplan"
assert_contains "$deploy_calls" "aws codebuild start-build"
assert_contains "$deploy_calls" "sleep "
assert_contains "$deploy_calls" "aws elbv2 describe-target-health"
assert_absent "$deploy_calls" "aws_s3_bucket_versioning.workflow"
assert_absent "$deploy_calls" "sleep 900"
assert_absent "$deploy_calls" "-auto-approve"
assert_absent "$deploy_calls" "uv run"

# Given a stable ECS service has no healthy load-balancer target.
run_command "$plan_calls" netrias staging plan >/dev/null
draining_calls="$TEST_ROOT/draining-calls"
draining_output="$TEST_ROOT/draining-output"
# When deploy finds only a draining target, then it reports that no healthy target exists.
if MOCK_TARGET_STATES=draining \
  run_command "$draining_calls" netrias staging deploy >"$draining_output" 2>&1; then
  fail_test "Deploy accepted a target group with no healthy target"
fi
assert_contains "$draining_output" "The load balancer has no healthy targets."

# Given an existing deployment manages workflow bucket versioning and runs an older app revision.
versioning_handoff='{"resource_changes":[{"address":"aws_ecs_service.app","change":{"actions":["update"]}},{"address":"aws_ecs_task_definition.application","change":{"actions":["delete","create"]}},{"address":"aws_s3_bucket_versioning.workflow","change":{"actions":["forget"]}}]}'
migration_plan_calls="$TEST_ROOT/migration-plan-calls"
MOCK_FORECAST_JSON_OVERRIDE="$versioning_handoff" \
  run_command "$migration_plan_calls" netrias staging plan >/dev/null
migration_deploy_calls="$TEST_ROOT/migration-deploy-calls"
# When the normal deploy command applies the approved ownership handoff.
MOCK_FORECAST_JSON_OVERRIDE="$versioning_handoff" \
  MOCK_PREREQUISITE_JSON_OVERRIDE='{"resource_changes":[]}' \
  MOCK_APPLICATION_JSON_OVERRIDE="$versioning_handoff" \
  run_command "$migration_deploy_calls" netrias staging deploy >/dev/null
# Then OpenTofu applies the handoff without targeting versioning or waiting 15 minutes.
assert_contains "$migration_deploy_calls" "application.tfplan"
assert_absent "$migration_deploy_calls" "-target=aws_s3_bucket_versioning.workflow"
assert_absent "$migration_deploy_calls" "sleep 900"

# Given the saved prerequisite apply returns but the live policy stays stale.
run_command "$plan_calls" netrias staging plan >/dev/null
convergence_calls="$TEST_ROOT/convergence-calls"
convergence_output="$TEST_ROOT/convergence-output"
# When deploy checks convergence, then it stops before CodeBuild and gives one safe next action.
if MOCK_CONVERGENCE_FAILURE=1 run_command "$convergence_calls" netrias staging deploy >"$convergence_output" 2>&1; then
  fail_test "Deploy accepted a stale prerequisite policy"
fi
assert_absent "$convergence_calls" "aws codebuild start-build"
assert_contains "$convergence_output" "The prerequisite IAM policy was not applied."
assert_contains "$convergence_output" "The current plan cannot be reused. Next: just plan netrias staging"

# Given AWS prevents OpenTofu from inspecting the prerequisite policy.
run_command "$plan_calls" netrias staging plan >/dev/null
convergence_aws_calls="$TEST_ROOT/convergence-aws-calls"
convergence_aws_output="$TEST_ROOT/convergence-aws-output"
# When convergence cannot be checked, then deploy stops before CodeBuild and reports the failed check.
if MOCK_CONVERGENCE_AWS_FAILURE=1 run_command "$convergence_aws_calls" netrias staging deploy >"$convergence_aws_output" 2>&1; then
  fail_test "Deploy ignored a prerequisite inspection failure"
fi
assert_absent "$convergence_aws_calls" "aws codebuild start-build"
assert_contains "$convergence_aws_output" "Could not verify the prerequisite IAM policy."
assert_contains "$convergence_aws_output" "OpenTofu returned no error message"
assert_contains "$convergence_aws_output" "The current plan cannot be reused. Next: just plan netrias staging"

# Given AWS accepts the prerequisite policy but CodeBuild fails to download source.
run_command "$plan_calls" netrias staging plan >/dev/null
mv "$TEST_ROOT/image" "$TEST_ROOT/image-from-success"
codebuild_failure_calls="$TEST_ROOT/codebuild-failure-calls"
codebuild_failure_output="$TEST_ROOT/codebuild-failure-output"
# When deploy observes the terminal build, then it shows the build, phase, AWS message, and next action.
if MOCK_CODEBUILD_FAILURE=1 run_command "$codebuild_failure_calls" netrias staging deploy >"$codebuild_failure_output" 2>&1; then
  fail_test "Deploy accepted a failed CodeBuild run"
fi
assert_contains "$codebuild_failure_output" "CodeBuild build-1 failed in DOWNLOAD_SOURCE."
assert_contains "$codebuild_failure_output" "AWS: Access denied to connection test-connection"
assert_contains "$codebuild_failure_output" "The current plan cannot be reused. Next: just plan netrias staging"

# Given AWS rejects the request to start CodeBuild.
run_command "$plan_calls" netrias staging plan >/dev/null
start_failure_calls="$TEST_ROOT/start-failure-calls"
start_failure_output="$TEST_ROOT/start-failure-output"
# When deploy requests a build, then it reports that no build ID exists and gives the safe next action.
if MOCK_CODEBUILD_START_FAILURE=1 run_command "$start_failure_calls" netrias staging deploy >"$start_failure_output" 2>&1; then
  fail_test "Deploy ignored a CodeBuild start failure"
fi
assert_contains "$start_failure_output" "CodeBuild did not start. Build ID: not created."
assert_contains "$start_failure_output" "AWS: AccessDenied: cannot start build"
assert_contains "$start_failure_output" "The current plan cannot be reused. Next: just plan netrias staging"

# Given CodeBuild starts but AWS rejects the status request.
run_command "$plan_calls" netrias staging plan >/dev/null
status_failure_calls="$TEST_ROOT/status-failure-calls"
status_failure_output="$TEST_ROOT/status-failure-output"
# When deploy inspects the build, then it reports the build ID and AWS error.
if MOCK_CODEBUILD_STATUS_FAILURE=1 run_command "$status_failure_calls" netrias staging deploy >"$status_failure_output" 2>&1; then
  fail_test "Deploy ignored a CodeBuild status failure"
fi
assert_contains "$status_failure_output" "Could not inspect CodeBuild build-1. Status: UNKNOWN. Phase: UNKNOWN."
assert_contains "$status_failure_output" "AWS: ServiceUnavailable: cannot inspect build"
assert_contains "$status_failure_output" "The current plan cannot be reused. Next: just plan netrias staging"
mv "$TEST_ROOT/image" "$TEST_ROOT/image-from-status-failure"

# Given AWS cannot find the build ID it just returned.
run_command "$plan_calls" netrias staging plan >/dev/null
not_found_calls="$TEST_ROOT/not-found-calls"
not_found_output="$TEST_ROOT/not-found-output"
# When deploy reads the response, then it reports the missing build and safe next action.
if MOCK_CODEBUILD_NOT_FOUND=1 run_command "$not_found_calls" netrias staging deploy >"$not_found_output" 2>&1; then
  fail_test "Deploy ignored a missing CodeBuild record"
fi
assert_contains "$not_found_output" "Could not inspect CodeBuild build-1. Status: UNKNOWN. Phase: UNKNOWN."
assert_contains "$not_found_output" "AWS: the requested build build-1 was not returned"
assert_contains "$not_found_output" "The current plan cannot be reused. Next: just plan netrias staging"
mv "$TEST_ROOT/image" "$TEST_ROOT/image-from-not-found"

# Given a failed build has no AWS context message.
run_command "$plan_calls" netrias staging plan >/dev/null
no_context_calls="$TEST_ROOT/no-context-calls"
no_context_output="$TEST_ROOT/no-context-output"
# When deploy reports the failure, then it keeps the phase and states that the message is absent.
if MOCK_CODEBUILD_FAILURE=1 MOCK_CODEBUILD_NO_CONTEXT=1 run_command "$no_context_calls" netrias staging deploy >"$no_context_output" 2>&1; then
  fail_test "Deploy ignored a failed build without context"
fi
assert_contains "$no_context_output" "CodeBuild build-1 failed in DOWNLOAD_SOURCE. Status: FAILED."
assert_contains "$no_context_output" "AWS: no failure message was returned"
assert_contains "$no_context_output" "The current plan cannot be reused. Next: just plan netrias staging"

# Given AWS returns a build record without its status.
run_command "$plan_calls" netrias staging plan >/dev/null
missing_status_calls="$TEST_ROOT/missing-status-calls"
missing_status_output="$TEST_ROOT/missing-status-output"
# When deploy reads the record, then it stops with the build ID, phase, and next action.
if MOCK_CODEBUILD_MISSING_STATUS=1 run_command "$missing_status_calls" netrias staging deploy >"$missing_status_output" 2>&1; then
  fail_test "Deploy accepted a CodeBuild record without status"
fi
assert_contains "$missing_status_output" "CodeBuild build-1 returned no status. Phase: DOWNLOAD_SOURCE."
assert_contains "$missing_status_output" "The current plan cannot be reused. Next: just plan netrias staging"
mv "$TEST_ROOT/image" "$TEST_ROOT/image-from-missing-status"

# Given AWS returns a different build than the one that deploy started.
run_command "$plan_calls" netrias staging plan >/dev/null
wrong_id_calls="$TEST_ROOT/wrong-id-calls"
wrong_id_output="$TEST_ROOT/wrong-id-output"
# When deploy reads the record, then it rejects it as an unusable status response.
if MOCK_CODEBUILD_WRONG_ID=1 run_command "$wrong_id_calls" netrias staging deploy >"$wrong_id_output" 2>&1; then
  fail_test "Deploy accepted a different CodeBuild record"
fi
assert_contains "$wrong_id_output" "AWS: the requested build build-1 was not returned"
assert_contains "$wrong_id_output" "The current plan cannot be reused. Next: just plan netrias staging"
mv "$TEST_ROOT/image" "$TEST_ROOT/image-from-wrong-id"

# Given a failed build contains an invalid phase record.
run_command "$plan_calls" netrias staging plan >/dev/null
malformed_phase_calls="$TEST_ROOT/malformed-phase-calls"
malformed_phase_output="$TEST_ROOT/malformed-phase-output"
# When deploy reads the failure, then it gives a structured error and no traceback.
if MOCK_CODEBUILD_FAILURE=1 MOCK_CODEBUILD_MALFORMED_PHASE=1 run_command "$malformed_phase_calls" netrias staging deploy >"$malformed_phase_output" 2>&1; then
  fail_test "Deploy accepted an invalid CodeBuild phase"
fi
assert_contains "$malformed_phase_output" "CodeBuild build-1 failed. Status: FAILED. Phase: COMPLETED."
assert_contains "$malformed_phase_output" "AWS returned no valid CodeBuild phase"
assert_absent "$malformed_phase_output" "Traceback"

# Given CodeBuild does not finish before the local wait limit.
run_command "$plan_calls" netrias staging plan >/dev/null
timeout_calls="$TEST_ROOT/timeout-calls"
timeout_output="$TEST_ROOT/timeout-output"
# When the wait expires, then deploy reports the build, unknown state, and next action.
if DATA_CHORD_BUILD_WAIT_SECONDS=0 run_command "$timeout_calls" netrias staging deploy >"$timeout_output" 2>&1; then
  fail_test "Deploy accepted a timed-out CodeBuild wait"
fi
assert_contains "$timeout_output" "CodeBuild build-1 did not finish within 65 minutes. Status: UNKNOWN. Phase: UNKNOWN."
assert_contains "$timeout_output" "The current plan cannot be reused. Next: just plan netrias staging"
mv "$TEST_ROOT/image" "$TEST_ROOT/image-from-timeout"

# Given a new plan receipt was created for state serial one.
run_command "$plan_calls" netrias staging plan >/dev/null
# When remote state changes before deploy, then deploy stops before its first apply.
changed_calls="$TEST_ROOT/changed-calls"
: >"$changed_calls"
if PATH="$MOCK_BIN:/usr/bin:/bin" \
  MOCK_CALLS="$changed_calls" \
  MOCK_COMMIT="$MOCK_COMMIT" \
  MOCK_IMAGE_FILE="$TEST_ROOT/image" \
  MOCK_STATE_SERIAL=2 \
  MOCK_FORECAST_JSON="$safe_plan" \
  MOCK_PREREQUISITE_JSON="$prerequisite_plan" \
  MOCK_APPLICATION_JSON="$application_plan" \
  DATA_CHORD_PLAN_ROOT="$TEST_ROOT/receipts" \
  DATA_CHORD_BUILD_ROOT="$TEST_ROOT/build" \
  "$DEPLOY_SCRIPT" netrias staging deploy </dev/null >/dev/null 2>&1; then
  fail_test "Deploy accepted changed state"
fi
assert_absent "$changed_calls" " apply "

# Given CI already has the deployer role but does not provide its expiry.
run_command "$plan_calls" netrias staging plan >/dev/null
# When deploy cannot prove that three hours remain, then it stops before apply.
active_role_calls="$TEST_ROOT/active-role-calls"
MOCK_ALREADY_DEPLOYER=1
if run_command "$active_role_calls" netrias staging deploy >/dev/null 2>&1; then
  fail_test "Deploy accepted an active role with an unknown expiry"
fi
unset MOCK_ALREADY_DEPLOYER
assert_absent "$active_role_calls" " apply "

# Given state still matches when deploy starts and changes while the prerequisite plan is created.
run_command "$plan_calls" netrias staging plan >/dev/null
# When deploy rechecks state before its first apply, then it rejects the stale saved plan.
race_calls="$TEST_ROOT/state-race-calls"
MOCK_STATE_CHANGE_AFTER_PULL=1
if run_command "$race_calls" netrias staging deploy >/dev/null 2>&1; then
  fail_test "Deploy accepted a state change after prerequisite planning"
fi
unset MOCK_STATE_CHANGE_AFTER_PULL
assert_contains "$race_calls" "prerequisites.tfplan"
assert_absent "$race_calls" " apply "

# Given a fresh receipt allows only the forecasted prerequisite resources.
run_command "$plan_calls" netrias staging plan >/dev/null
MOCK_PREREQUISITE_JSON_OVERRIDE='{"resource_changes":[{"address":"aws_vpc.app","change":{"actions":["create"]}}]}'
# When the prerequisite plan adds another resource, then deploy stops before apply.
unexpected_calls="$TEST_ROOT/unexpected-calls"
if run_command "$unexpected_calls" netrias staging deploy >/dev/null 2>&1; then
  fail_test "Deploy accepted an unexpected prerequisite resource"
fi
assert_absent "$unexpected_calls" " apply "

# Given deployment has one supported operator interface.
# When the Justfile is inspected, then it exposes only plan and deploy with target and stage.
assert_contains "$JUSTFILE" "plan target stage:"
assert_contains "$JUSTFILE" "deploy target stage:"
assert_absent "$JUSTFILE" "status target stage"
assert_absent "$JUSTFILE" "plan target stage profile"

# Given the deploy command uses a full commit image tag.
# When the CodeBuild recipe is inspected, then it keeps the same full commit.
assert_contains "$BUILDSPEC" 'export IMAGE_TAG="$SOURCE_TAG"'
assert_absent "$BUILDSPEC" "cut -c1-12"

printf 'Deployment flow behavior tests passed.\n'
