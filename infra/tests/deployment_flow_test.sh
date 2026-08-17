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
      printf '945365518758\tarn:aws:sts::945365518758:assumed-role/datachord-deployer/test\n'
    else
      printf '111111111111\tarn:aws:iam::111111111111:user/operator\n'
    fi
    ;;
  "sts assume-role") printf 'ASIATEST\tsecret\ttoken\t2099-01-01T00:00:00Z\n' ;;
  "iam get-role")
    printf '/foundation/\tarn:aws:iam::945365518758:policy/datachord-deployer-boundary\n'
    ;;
  "iam get-policy")
    printf 'arn:aws:iam::945365518758:policy/datachord-application-role-boundary\n'
    ;;
  "ecr describe-images")
    if [[ -f "$MOCK_IMAGE_FILE" ]]; then
      printf '{}\n'
    else
      printf 'ImageNotFoundException\n' >&2
      exit 254
    fi
    ;;
  "codebuild start-build") touch "$MOCK_IMAGE_FILE"; printf 'build-1\n' ;;
  "codebuild batch-get-builds") printf 'SUCCEEDED\tCOMPLETED\n' ;;
  "ecs describe-services") printf 'COMPLETED\t1\t1\t0\n' ;;
  "elbv2 describe-target-health") printf 'healthy\n' ;;
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

safe_plan='{"resource_changes":[{"address":"aws_ecr_repository.app","change":{"actions":["create"]}},{"address":"aws_dynamodb_table.reference_data","change":{"actions":["create"]}},{"address":"aws_s3_bucket.workflow","change":{"actions":["create"]}},{"address":"aws_s3_bucket_versioning.workflow","change":{"actions":["create"]}}]}'
prerequisite_plan='{"resource_changes":[{"address":"aws_ecr_repository.app","change":{"actions":["create"]}},{"address":"aws_s3_bucket.workflow","change":{"actions":["create"]}},{"address":"aws_s3_bucket_versioning.workflow","change":{"actions":["create"]}}]}'
application_plan='{"resource_changes":[{"address":"aws_dynamodb_table.reference_data","change":{"actions":["create"]}}]}'

run_command() {
  local calls="$1"
  shift
  : >"$calls"
  : >"$calls.state-pulls"
  PATH="$MOCK_BIN:/usr/bin:/bin" \
    MOCK_CALLS="$calls" \
    MOCK_COMMIT="$MOCK_COMMIT" \
    MOCK_IMAGE_FILE="$TEST_ROOT/image" \
    MOCK_EMPTY_STATE="${MOCK_EMPTY_STATE:-0}" \
    MOCK_ALREADY_DEPLOYER="${MOCK_ALREADY_DEPLOYER:-0}" \
    MOCK_STATE_CHANGE_AFTER_PULL="${MOCK_STATE_CHANGE_AFTER_PULL:-0}" \
    MOCK_STATE_COUNTER_FILE="$calls.state-pulls" \
    AWS_CREDENTIAL_EXPIRATION="${MOCK_CREDENTIAL_EXPIRATION:-}" \
    MOCK_FORECAST_JSON="${MOCK_FORECAST_JSON_OVERRIDE:-$safe_plan}" \
    MOCK_PREREQUISITE_JSON="${MOCK_PREREQUISITE_JSON_OVERRIDE:-$prerequisite_plan}" \
    MOCK_APPLICATION_JSON="$application_plan" \
    DATA_CHORD_PLAN_ROOT="$TEST_ROOT/receipts" \
    DATA_CHORD_BUILD_ROOT="$TEST_ROOT/build" \
    "$DEPLOY_SCRIPT" "$@" </dev/null
}

# Given BDF has a legacy state layout.
bdf_calls="$TEST_ROOT/bdf-calls"
: >"$bdf_calls"
# When the new plan command selects BDF, then it stops before AWS and OpenTofu.
if PATH="$MOCK_BIN:/usr/bin:/bin" MOCK_CALLS="$bdf_calls" "$DEPLOY_SCRIPT" bdf staging plan >/dev/null 2>&1; then
  fail_test "BDF plan succeeded"
fi
[[ ! -s "$bdf_calls" ]] || fail_test "BDF reached an external command"

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
run_command "$deploy_calls" netrias staging deploy >/dev/null
# Then only saved plans apply, no data import runs, and health is checked.
assert_contains "$deploy_calls" "prerequisites.tfplan"
assert_contains "$deploy_calls" "--duration-seconds 14400"
assert_contains "$deploy_calls" "application.tfplan"
assert_contains "$deploy_calls" "aws codebuild start-build"
assert_contains "$deploy_calls" "sleep "
assert_contains "$deploy_calls" "aws elbv2 describe-target-health"
assert_absent "$deploy_calls" "-auto-approve"
assert_absent "$deploy_calls" "uv run"

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
