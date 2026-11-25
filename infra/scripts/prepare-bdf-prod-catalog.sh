#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

export DEPLOYMENT_ROOT=full
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TARGET_NAME=bdf
STAGE_NAME=prod
ENVIRONMENT_FILE="$(environment_path "$TARGET_NAME" "$STAGE_NAME")"
mode="${1:-}"
plan="${2:-}"
[[ $# == 2 && "$plan" == /* && ( "$mode" == plan || "$mode" == apply ) ]] ||
  fail "Use: $0 plan|apply /absolute/path/to/preparation.tfplan"

for command in aws git jq python3 tofu; do
  require_command "$command"
done
validate_environment
[[ "$(environment_value reference_catalog)" == prepare_flat ]] ||
  fail "BDF production must select prepare_flat."
require_deployable_git_state
export AWS_REGION="$(environment_value region)"
export AWS_DEFAULT_REGION="$AWS_REGION"
assume_deployer_role
verify_foundation_contract
init_tofu

# Keep the production image already recorded in managed state.
active_image="$(required_tofu_output deployed_image_tag)"
[[ "$active_image" =~ ^[0-9a-f]{40}$ ]] || fail "The active production image is invalid."
[[ "$(required_tofu_output reference_data_table)" == data-chord-prod-reference-data ]] ||
  fail "BDF production state must still select the original catalog."
account="$(environment_value account_id)"
former_versioning=module.data_plane.aws_s3_bucket_versioning.workflow
managed_versioning=aws_s3_bucket_versioning.managed_workflow

check_plan() {
  tofu -chdir="$INFRA_DIR" show -json "$plan" |
    jq -e --arg account "$account" --arg image "$active_image" --arg region "$AWS_REGION" \
      --arg former_versioning "$former_versioning" --arg managed_versioning "$managed_versioning" '
      .variables.expected_account_id.value == $account and
      .variables.aws_region.value == $region and
      .variables.deployment_target.value == "bdf" and
      .variables.environment.value == "prod" and
      .variables.reference_catalog.value == "prepare_flat" and
      .variables.image_tag.value == $image and
      all(.resource_changes[] | select(.mode == "managed");
        .change.importing == null and (
          .previous_address == null or (
            .previous_address == $former_versioning and .address == $managed_versioning
            and .change.actions == ["no-op"]
          )
        )
      ) and
      ([.resource_changes[] | select(.mode == "managed" and .change.actions != ["no-op"])]
        | length == 1 and all(
        .address == "aws_dynamodb_table.flat_reference_catalog[0]" and
        .change.actions == ["create"] and
        .change.after.name == "data-chord-prod-reference-data-flat" and
        .change.after.hash_key == "pk" and .change.after.range_key == "sk" and
        (.change.after.attribute | sort_by(.name)) == [{"name": "pk", "type": "S"}, {"name": "sk", "type": "S"}] and
        .change.after.deletion_protection_enabled == true and
        .change.after.point_in_time_recovery[0].enabled == true
      ))
    ' >/dev/null || fail "The saved plan must only create the protected parallel BDF production catalog."
}

if [[ "$mode" == plan ]]; then
  [[ ! -e "$plan" ]] || fail "Choose a new plan filename."
  vars="$(mktemp -d "$(dirname "$plan")/.reference-catalog.XXXXXX")/environment.tfvars.json"
  write_tofu_variables "$vars"
  targets=(-target='aws_dynamodb_table.flat_reference_catalog[0]')
  state_addresses="$(tofu -chdir="$INFRA_DIR" state list)"
  if grep -Fxq -- "$former_versioning" <<<"$state_addresses"; then
    # Target both addresses of the existing state-only move.
    targets+=("-target=$former_versioning" "-target=$managed_versioning")
  fi
  tofu -chdir="$INFRA_DIR" plan -lock=false -input=false -var-file="$vars" \
    -var="image_tag=$active_image" "${targets[@]}" -out="$plan"
  check_plan
else
  [[ -f "$plan" ]] || fail "The saved preparation plan does not exist."
  check_plan
  tofu -chdir="$INFRA_DIR" apply -input=false "$plan"
fi
