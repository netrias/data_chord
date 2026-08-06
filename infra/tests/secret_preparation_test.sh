#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRET_SCRIPT="$TEST_DIR/../scripts/bootstrap-secrets.sh"
TEST_ROOT="$(mktemp -d)"
MOCK_BIN="$TEST_ROOT/bin"
MOCK_CALLS="$TEST_ROOT/calls"
mkdir -p "$MOCK_BIN"
: >"$MOCK_CALLS"

fail_test() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

cat >"$MOCK_BIN/aws" <<'MOCK_AWS'
#!/usr/bin/env bash
set -Eeuo pipefail

[[ -z "${MOCK_AWS_STARTED_FILE:-}" ]] || touch "$MOCK_AWS_STARTED_FILE"

case "${1:-} ${2:-}" in
  "sts get-caller-identity")
    printf '945365518758\tarn:aws:sts::945365518758:assumed-role/datachord-deployer/test\n'
    ;;
  "secretsmanager describe-secret")
    [[ "${MOCK_SECRET_EXISTS:-1}" == "1" ]] || exit 254
    ;;
  "secretsmanager get-secret-value")
    printf '{"VersionId":"%s","SecretString":"%s"}\n' \
      "$MOCK_CURRENT_VERSION_ID" "$MOCK_CURRENT_SECRET_VALUE"
    ;;
  "secretsmanager put-secret-value" | "secretsmanager create-secret")
    action="$2"
    shift 2
    token=""
    while (( $# > 0 )); do
      if [[ "$1" == "--client-request-token" ]]; then
        token="${2:-}"
        break
      fi
      shift
    done
    [[ -n "$token" ]] || exit 25
    printf '%s %s\n' "$action" "$token" >>"$MOCK_CALLS"
    ;;
  *)
    printf 'Unexpected aws call: %s\n' "$*" >&2
    exit 2
    ;;
esac
MOCK_AWS
chmod +x "$MOCK_BIN/aws"

prepare_secret() {
  local desired_value="$1"
  local secret_exists="$2"
  local current_value="${3:-}"
  local current_version_id="${4:-}"

  PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    MOCK_CALLS="$MOCK_CALLS" \
    MOCK_CURRENT_SECRET_VALUE="$current_value" \
    MOCK_CURRENT_VERSION_ID="$current_version_id" \
    MOCK_SECRET_EXISTS="$secret_exists" \
    NETRIAS_API_KEY="$desired_value" \
    "$SECRET_SCRIPT" netrias staging ensure >/dev/null 2>&1
}

prepare_secret first-secret-value 1 first-secret-value version-1
[[ ! -s "$MOCK_CALLS" ]] || fail_test "Same current secret value created a new version"

prepare_secret changed-secret-value 1 first-secret-value version-1
prepare_secret first-secret-value 0
prepare_secret first-secret-value 1 changed-secret-value version-2

[[ "$(wc -l <"$MOCK_CALLS" | tr -d ' ')" == "3" ]] || fail_test "Expected one changed-value write, one create, and one revert write"

changed_token="$(sed -n '1s/^[^ ]* //p' "$MOCK_CALLS")"
create_token="$(sed -n '2s/^[^ ]* //p' "$MOCK_CALLS")"
revert_token="$(sed -n '3s/^[^ ]* //p' "$MOCK_CALLS")"

[[ "$(sed -n '1s/ .*//p' "$MOCK_CALLS")" == "put-secret-value" ]] || fail_test "Changed existing value did not request an update"
[[ "$(sed -n '2s/ .*//p' "$MOCK_CALLS")" == "create-secret" ]] || fail_test "Missing secret did not request creation"
[[ "$(sed -n '3s/ .*//p' "$MOCK_CALLS")" == "put-secret-value" ]] || fail_test "Reverted value did not request an update"
[[ "$changed_token" =~ ^[0-9a-f]{64}$ ]] || fail_test "Secret version token is not a 64-character SHA-256 value"
[[ "$create_token" != "$revert_token" ]] || fail_test "Reverting to a previous value reused its old version token"

invalid_mode_output=""
aws_started_file="$TEST_ROOT/aws-started"
if invalid_mode_output="$(
  PATH="$MOCK_BIN:$PATH" \
    MOCK_AWS_STARTED_FILE="$aws_started_file" \
    "$SECRET_SCRIPT" netrias staging invalid 2>&1
)"; then
  fail_test "Invalid secret mode succeeded"
fi
[[ "$invalid_mode_output" == *"Choose a secret mode: ensure or check"* ]] || fail_test "Invalid secret mode did not produce a useful error"
[[ ! -e "$aws_started_file" ]] || fail_test "Invalid secret mode reached AWS"

check_secret_value="must-not-appear-in-output"
check_output="$(
  PATH="$MOCK_BIN:$PATH" \
    AWS_PROFILE=mock \
    MOCK_CALLS="$MOCK_CALLS" \
    MOCK_SECRET_EXISTS=1 \
    NETRIAS_API_KEY="$check_secret_value" \
    "$SECRET_SCRIPT" netrias staging check 2>&1
)"
[[ "$check_output" != *"$check_secret_value"* ]] || fail_test "Secret check printed the secret value"
[[ "$(wc -l <"$MOCK_CALLS" | tr -d ' ')" == "3" ]] || fail_test "Secret check wrote a secret value"

if PATH="$MOCK_BIN:$PATH" \
  AWS_PROFILE=mock \
  MOCK_CALLS="$MOCK_CALLS" \
  MOCK_SECRET_EXISTS=0 \
  NETRIAS_API_KEY=must-not-create-during-check \
  "$SECRET_SCRIPT" netrias staging check >/dev/null 2>&1; then
  fail_test "Secret check accepted a missing secret"
fi
[[ "$(wc -l <"$MOCK_CALLS" | tr -d ' ')" == "3" ]] || fail_test "Missing-secret check created a secret"

printf 'Secret preparation tests passed.\n'
