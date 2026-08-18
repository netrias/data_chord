#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$INFRA_DIR/.." && pwd)"

log() {
  printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2
}

fail() {
  printf '[%s] ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

environment_path() {
  printf '%s/environments/%s/%s.json\n' "$REPO_DIR" "$1" "$2"
}

environment_value() {
  python3 "$SCRIPT_DIR/environment.py" get "$ENVIRONMENT_FILE" "$TARGET_NAME" "$STAGE_NAME" "$1"
}

validate_environment() {
  python3 "$SCRIPT_DIR/environment.py" validate "$ENVIRONMENT_FILE" "$TARGET_NAME" "$STAGE_NAME" ||
    fail "The deployment environment is invalid."
}

write_tofu_variables() {
  python3 "$SCRIPT_DIR/environment.py" tofu-vars \
    "$ENVIRONMENT_FILE" "$TARGET_NAME" "$STAGE_NAME" >"$1"
}

git_commit() {
  git -C "$REPO_DIR" rev-parse HEAD
}

require_deployable_git_state() {
  local commit dirty_status environment_relative repository remote_match untracked_infra
  commit="$(git_commit)"
  dirty_status="$(git -C "$REPO_DIR" status --porcelain --untracked-files=no)"
  [[ -z "$dirty_status" ]] || fail "The working tree has changes. Commit or remove them before plan."
  untracked_infra="$(git -C "$REPO_DIR" ls-files --others --exclude-standard -- infra)"
  [[ -z "$untracked_infra" ]] || fail "The infra directory has untracked files. Commit or remove them before plan."

  environment_relative="environments/$TARGET_NAME/$STAGE_NAME.json"
  git -C "$REPO_DIR" ls-files --error-unmatch -- "$environment_relative" >/dev/null 2>&1 ||
    fail "The environment file must be committed before plan."

  repository="$(environment_value application_repository_url)"
  remote_match="$(GIT_TERMINAL_PROMPT=0 git -C "$REPO_DIR" ls-remote "$repository" |
    awk -v expected="$commit" '$1 == expected { print $1; exit }')"
  [[ "$remote_match" == "$commit" ]] ||
    fail "Commit $commit is not the tip of a ref in $repository. Push it before plan."
  log "Deploy source: $commit"
}

assume_deployer_role() {
  local expected_account expected_role role_name partition identity account caller credentials expiration
  expected_account="$(environment_value account_id)"
  expected_role="$(environment_value deployer_role_arn)"
  role_name="$(environment_value deployer_role_name)"
  partition="$(environment_value partition)"
  identity="$(aws sts get-caller-identity --query '[Account,Arn]' --output text)" ||
    fail "AWS credentials are not available."
  read -r account caller <<<"$identity"

  if [[ "$account" == "$expected_account" && "$caller" == "arn:$partition:sts::$expected_account:assumed-role/$role_name/"* ]]; then
    log "The deployer role is already active."
  else
    credentials="$(aws sts assume-role \
      --role-arn "$expected_role" \
      --role-session-name "datachord-$TARGET_NAME-$STAGE_NAME" \
      --duration-seconds "${DEPLOYER_SESSION_SECONDS:-3600}" \
      --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken,Expiration]' \
      --output text)" || fail "Could not assume $expected_role."
    read -r AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN expiration <<<"$credentials"
    AWS_CREDENTIAL_EXPIRATION="$expiration"
    export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_CREDENTIAL_EXPIRATION
    unset AWS_SECURITY_TOKEN
  fi

  identity="$(aws sts get-caller-identity --query '[Account,Arn]' --output text)" ||
    fail "Could not verify the assumed deployer role."
  read -r account caller <<<"$identity"
  [[ "$account" == "$expected_account" ]] ||
    fail "The active AWS account is $account, not $expected_account."
  [[ "$caller" == "arn:$partition:sts::$expected_account:assumed-role/$role_name/"* ]] ||
    fail "The active AWS identity is not $expected_role."
  require_deployer_session_lifetime
}

require_deployer_session_lifetime() {
  local required_seconds="${DEPLOYER_REQUIRED_REMAINING_SECONDS:-0}"
  ((required_seconds > 0)) || return 0
  [[ -n "${AWS_CREDENTIAL_EXPIRATION:-}" ]] ||
    fail "Deploy cannot prove the active deployer-role session lifetime. Set AWS_CREDENTIAL_EXPIRATION or use source credentials that can assume the deployer role."
  python3 - "$AWS_CREDENTIAL_EXPIRATION" "$required_seconds" <<'PY' ||
import datetime
import sys

try:
    expiration = datetime.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
    remaining = expiration.timestamp() - datetime.datetime.now(datetime.timezone.utc).timestamp()
    required = int(sys.argv[2])
except (IndexError, TypeError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if remaining >= required else 1)
PY
    fail "The active deployer-role session has less than three hours left. Start a fresh four-hour session before deploy."
}

verify_foundation_contract() {
  local role_name role_details expected_boundary actual_path actual_boundary app_boundary
  role_name="$(environment_value deployer_role_name)"
  expected_boundary="$(environment_value deployer_boundary_arn)"
  role_details="$(aws iam get-role \
    --role-name "$role_name" \
    --query '[Role.Path,Role.PermissionsBoundary.PermissionsBoundaryArn]' \
    --output text)" || fail "Could not inspect the foundation deployer role."
  read -r actual_path actual_boundary <<<"$role_details"
  [[ "$actual_path" == "/foundation/" ]] ||
    fail "The deployer role path is $actual_path, not /foundation/."
  [[ "$actual_boundary" == "$expected_boundary" ]] ||
    fail "The deployer role does not use $expected_boundary."
  app_boundary="$(environment_value application_role_boundary_arn)"
  aws iam get-policy --policy-arn "$app_boundary" --query 'Policy.Arn' --output text >/dev/null ||
    fail "The application role boundary does not exist: $app_boundary"
}

init_tofu() {
  local bucket region state_key
  bucket="$(environment_value state_bucket_name)"
  region="$(environment_value region)"
  state_key="$(environment_value state_key)"
  export TF_DATA_DIR="$INFRA_DIR/.terraform-data/$TARGET_NAME/$STAGE_NAME"
  log "OpenTofu state: s3://$bucket/$state_key"
  tofu -chdir="$INFRA_DIR" init \
    -backend-config="bucket=$bucket" \
    -backend-config="key=$state_key" \
    -backend-config="region=$region" \
    -backend-config="encrypt=true" \
    -backend-config="use_lockfile=true" \
    -input=false \
    -reconfigure >/dev/null
}

pull_state() {
  local state_path="$1"
  local error_path="$2"
  if tofu -chdir="$INFRA_DIR" state pull >"$state_path" 2>"$error_path"; then
    if [[ ! -s "$state_path" ]]; then
      printf '%s\n' '-'
      return 0
    fi
    printf '%s\n' "$state_path"
    return 0
  fi
  if grep -Eq 'No state file|no state snapshot|State not found' "$error_path"; then
    printf '%s\n' '-'
    return 0
  fi
  fail "Could not read OpenTofu state: $(<"$error_path")"
}

tofu_output() {
  tofu -chdir="$INFRA_DIR" output -raw "$1" 2>/dev/null || true
}

required_tofu_output() {
  local output
  output="$(tofu -chdir="$INFRA_DIR" output -raw "$1" 2>&1)" ||
    fail "Could not read OpenTofu output '$1': $output"
  [[ -n "$output" ]] || fail "OpenTofu output '$1' is empty."
  printf '%s\n' "$output"
}
