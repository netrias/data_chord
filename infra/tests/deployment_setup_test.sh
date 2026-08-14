#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_SCRIPT="$TEST_DIR/../scripts/setup.sh"
TEST_ROOT="$(mktemp -d)"
MOCK_BIN="$TEST_ROOT/bin"
mkdir -p "$MOCK_BIN"

fail_test() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

assert_contains() {
  local expected="$1"
  local actual="$2"
  local description="$3"

  [[ "$actual" == *"$expected"* ]] ||
    fail_test "$description: expected output containing '$expected', got: $actual"
}

assert_call() {
  local expected="$1"
  local calls_file="$2"

  grep -Fqx -- "$expected" "$calls_file" || fail_test "Missing call: $expected"
}

assert_no_config_write() {
  local calls_file="$1"

  if grep -Fq 'configure set' "$calls_file"; then
    fail_test "Setup changed a conflicting profile"
  fi
}

cat >"$MOCK_BIN/aws" <<'MOCK_AWS'
#!/usr/bin/env bash
set -Eeuo pipefail

case "${1:-} ${2:-}" in
  "configure list-profiles")
    printf '%s\n' "${MOCK_PROFILES:-default}"
    ;;
  "configure get")
    key="${3:-}"
    case "$key" in
      role_arn) printf '%s\n' "${MOCK_ROLE_ARN:-}" ;;
      source_profile) printf '%s\n' "${MOCK_SOURCE_PROFILE:-}" ;;
      region) printf '%s\n' "${MOCK_REGION:-}" ;;
      credential_source) printf '%s\n' "${MOCK_CREDENTIAL_SOURCE:-}" ;;
      credential_process) printf '%s\n' "${MOCK_CREDENTIAL_PROCESS:-}" ;;
      web_identity_token_file) printf '%s\n' "${MOCK_WEB_IDENTITY_TOKEN_FILE:-}" ;;
      sso_session) printf '%s\n' "${MOCK_SSO_SESSION:-}" ;;
      sso_start_url) printf '%s\n' "${MOCK_SSO_START_URL:-}" ;;
      aws_access_key_id) printf '%s\n' "${MOCK_ACCESS_KEY_ID:-}" ;;
      *) printf 'Unexpected configure key: %s\n' "$key" >&2; exit 2 ;;
    esac
    ;;
  "configure set")
    printf 'configure set %s %s %s %s\n' "${3:-}" "${4:-}" "${5:-}" "${6:-}" >>"$MOCK_CALLS"
    ;;
  "sts get-caller-identity")
    profile="${AWS_PROFILE:-}"
    explicit_profile=0
    if [[ "$*" == *"--profile"* ]]; then
      explicit_profile=1
      while (( $# > 0 )); do
        if [[ "$1" == "--profile" ]]; then
          profile="$2"
          break
        fi
        shift
      done
    fi
    printf 'sts identity profile=%s explicit=%s\n' "$profile" "$explicit_profile" >>"$MOCK_CALLS"
    if [[ "${MOCK_AMBIENT_OVERRIDES:-0}" == "1" && "$explicit_profile" == "0" ]]; then
      printf '111122223333\tarn:aws:iam::111122223333:user/ambient\n'
      exit 0
    fi
    if [[ "$profile" == "${MOCK_SOURCE_NAME:-default}" ]]; then
      if [[ "${MOCK_SOURCE_AUTH_FAIL:-0}" == "1" ]]; then
        printf 'The SSO session has expired\n' >&2
        exit 1
      fi
      printf '945365518758\tarn:aws:iam::945365518758:user/operator\n'
    else
      printf '945365518758\tarn:%s:sts::945365518758:assumed-role/datachord-deployer/setup\n' "${MOCK_PARTITION:-aws}"
    fi
    ;;
  "sts assume-role")
    profile=""
    role_arn=""
    while (( $# > 0 )); do
      case "$1" in
        --profile) profile="${2:-}"; shift 2 ;;
        --role-arn) role_arn="${2:-}"; shift 2 ;;
        *) shift ;;
      esac
    done
    printf 'sts assume-role profile=%s role=%s\n' "$profile" "$role_arn" >>"$MOCK_CALLS"
    if [[ "${MOCK_ASSUME_ROLE_FAIL:-0}" == "1" ]]; then
      printf 'AccessDenied: source cannot assume target role\n' >&2
      exit 1
    fi
    printf 'arn:%s:sts::945365518758:assumed-role/datachord-deployer/datachord-setup-preflight\n' "${MOCK_PARTITION:-aws}"
    ;;
  *)
    printf 'Unexpected aws call: %s\n' "$*" >&2
    exit 2
    ;;
esac
MOCK_AWS
chmod +x "$MOCK_BIN/aws"

run_new_profile_setup() {
  local calls_file="$TEST_ROOT/new-profile-calls"
  local assume_line output write_line
  : >"$calls_file"

  # Given a valid source profile and no target profile,
  # when setup runs, then it proves role trust before it writes the profile.
  output="$(
    PATH="$MOCK_BIN:$PATH" \
      MOCK_CALLS="$calls_file" \
      MOCK_AMBIENT_OVERRIDES=1 \
      MOCK_PROFILES=default \
      AWS_ACCESS_KEY_ID=ambient-access-key \
      AWS_SECRET_ACCESS_KEY=ambient-secret-key \
      "$SETUP_SCRIPT" netrias 2>&1
  )"

  assert_call "configure set region us-east-2 --profile datachord-netrias" "$calls_file"
  assert_call "configure set source_profile default --profile datachord-netrias" "$calls_file"
  assert_call "configure set role_arn arn:aws:iam::945365518758:role/foundation/datachord-deployer --profile datachord-netrias" "$calls_file"
  assert_call "sts assume-role profile=default role=arn:aws:iam::945365518758:role/foundation/datachord-deployer" "$calls_file"
  assert_call "sts identity profile=datachord-netrias explicit=1" "$calls_file"
  assume_line="$(grep -n '^sts assume-role ' "$calls_file" | cut -d: -f1)"
  write_line="$(grep -n '^configure set ' "$calls_file" | head -n 1 | cut -d: -f1)"
  (( assume_line < write_line )) || fail_test "Setup wrote the profile before it proved role trust"
  assert_contains "Setup complete" "$output" "new profile result"
}

run_assume_role_failure_does_not_write() {
  local calls_file="$TEST_ROOT/assume-role-failure-calls"
  local output
  : >"$calls_file"

  # Given source credentials without role trust,
  # when setup runs, then it fails before the first profile write.
  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      MOCK_ASSUME_ROLE_FAIL=1 \
      MOCK_CALLS="$calls_file" \
      MOCK_PROFILES=default \
      "$SETUP_SCRIPT" netrias 2>&1
  )"; then
    fail_test "Setup accepted a source profile that cannot assume the deployer role"
  fi

  assert_no_config_write "$calls_file"
  assert_contains "cannot assume" "$output" "role trust cause"
  assert_contains "No target profile settings were written" "$output" "role trust recovery"
}

run_customer_target_setup() {
  local fixture_repo="$TEST_ROOT/customer-repo"
  local fixture_infra="$fixture_repo/infra"
  local calls_file="$TEST_ROOT/customer-setup-calls"
  local output
  mkdir -p "$fixture_infra/targets"
  cp -R "$TEST_DIR/../scripts" "$fixture_infra/scripts"
  sed \
    -e 's/"target_slug": "netrias"/"target_slug": "government-customer"/' \
    -e 's/arn:aws:/arn:aws-us-gov:/g' \
    -e 's/us-east-2/us-gov-west-1/g' \
    "$TEST_DIR/../targets/netrias.json" >"$fixture_infra/targets/government-customer.json"
  : >"$calls_file"

  # Given a new GovCloud customer contract,
  # when the public setup script runs, then it derives the profile and partition from that contract.
  output="$(
    PATH="$MOCK_BIN:$PATH" \
      MOCK_CALLS="$calls_file" \
      MOCK_PARTITION=aws-us-gov \
      MOCK_PROFILES=default \
      "$fixture_infra/scripts/setup.sh" government-customer 2>&1
  )"

  assert_call "sts assume-role profile=default role=arn:aws-us-gov:iam::945365518758:role/foundation/datachord-deployer" "$calls_file"
  assert_call "configure set region us-gov-west-1 --profile datachord-government-customer" "$calls_file"
  assert_call "configure set role_arn arn:aws-us-gov:iam::945365518758:role/foundation/datachord-deployer --profile datachord-government-customer" "$calls_file"
  assert_call "sts identity profile=datachord-government-customer explicit=1" "$calls_file"
  assert_contains "Setup complete" "$output" "customer setup result"
}

run_existing_profile_setup() {
  local calls_file="$TEST_ROOT/existing-profile-calls"
  local output
  : >"$calls_file"

  output="$(
    PATH="$MOCK_BIN:$PATH" \
      MOCK_CALLS="$calls_file" \
      MOCK_PROFILES=$'default\ndatachord-netrias' \
      MOCK_ROLE_ARN=arn:aws:iam::945365518758:role/foundation/datachord-deployer \
      MOCK_SOURCE_PROFILE=default \
      MOCK_REGION=us-east-2 \
      "$SETUP_SCRIPT" netrias 2>&1
  )"

  assert_no_config_write "$calls_file"
  assert_contains "already has the required settings" "$output" "idempotent setup result"
  assert_contains "Setup complete" "$output" "existing profile result"
}

run_conflicting_profile_fails() {
  local calls_file="$TEST_ROOT/conflicting-profile-calls"
  local output
  : >"$calls_file"

  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      MOCK_CALLS="$calls_file" \
      MOCK_PROFILES=$'default\ndatachord-netrias' \
      MOCK_CREDENTIAL_SOURCE=Ec2InstanceMetadata \
      "$SETUP_SCRIPT" netrias 2>&1
  )"; then
    fail_test "Setup accepted a conflicting deployment profile"
  fi

  assert_no_config_write "$calls_file"
  assert_contains "conflicting credential_source" "$output" "conflict cause"
  assert_contains "Move or rename that profile" "$output" "conflict recovery"
}

run_missing_source_fails() {
  local calls_file="$TEST_ROOT/missing-source-calls"
  local output
  : >"$calls_file"

  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      MOCK_CALLS="$calls_file" \
      MOCK_PROFILES=other \
      "$SETUP_SCRIPT" netrias datachord 2>&1
  )"; then
    fail_test "Setup accepted a missing source profile"
  fi

  assert_no_config_write "$calls_file"
  assert_contains "Source AWS profile 'datachord' does not exist" "$output" "missing source cause"
  assert_contains "aws configure list-profiles" "$output" "missing source recovery"
}

run_source_auth_failure_is_explained() {
  local calls_file="$TEST_ROOT/source-auth-calls"
  local output
  : >"$calls_file"

  if output="$(
    PATH="$MOCK_BIN:$PATH" \
      MOCK_CALLS="$calls_file" \
      MOCK_PROFILES=default \
      MOCK_SOURCE_AUTH_FAIL=1 \
      "$SETUP_SCRIPT" netrias 2>&1
  )"; then
    fail_test "Setup accepted a source profile that cannot authenticate"
  fi

  assert_no_config_write "$calls_file"
  assert_contains "could not authenticate" "$output" "authentication cause"
  assert_contains "aws sso login --profile default" "$output" "authentication recovery"
}

run_new_profile_setup
run_assume_role_failure_does_not_write
run_existing_profile_setup
run_conflicting_profile_fails
run_missing_source_fails
run_source_auth_failure_is_explained
run_customer_target_setup

printf 'Deployment setup tests passed.\n'
