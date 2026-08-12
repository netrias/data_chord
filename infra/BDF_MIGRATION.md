# Existing BDF application-role handoff

This is a one-time procedure for BDF staging and production. Use it only when
the migrated service state still contains an address from
`migration-handoff.tf`.

The normal `just deploy` command must not perform this one-time handoff. A
configured operator must start the procedure and assume the foundation
deployment role once. The command stops until this procedure is complete.

## Before the handoff

1. Apply the BDF foundation contract.

2. Move the state with the exact
   [foundation state-migration procedure](https://github.com/netrias/datachord-infrastructure/blob/main/docs/state-operations.md#move-existing-service-state).
   For this service, both backends use the contract state bucket and region.
   The source key is `data-chord/<stage>/tofu.tfstate`; the destination key is
   `datachord/bdf/<stage>/tofu.tfstate`. Stop both state writers first. Use the
   base profile only to get its region and caller identity and to assume
   `arn:<caller-partition>:iam::<caller-account>:role/foundation/datachord-deployer`
   once. Validate the assumed identity, remove `AWS_PROFILE`, and use only the
   materialized temporary credentials for both the source and destination
   backends. The role has explicit access to both key forms. Refuse an existing
   destination object. Back up the source, use OpenTofu's backend migration in
   an isolated `TF_DATA_DIR`, then record both object version IDs, state serials,
   and lineages. Do not refresh the migrated application state yet. The
   deployment role cannot read the old root-path IAM roles. The saved handoff
   below removes those addresses before the next normal-refresh plan.

3. Record the active ECS task-definition ARN and CodeBuild service-role ARN.
   Keep the old roles until the new ECS deployment and CodeBuild project are
   proved.

4. Record the two retained endpoint resources. They are a temporary BDF legacy
   exception, not part of the new Data Chord network:

   - `aws_security_group.secrets_endpoint[0]`
   - `aws_vpc_endpoint_security_group_association.secretsmanager_tasks[0]`

   The handoff must forget these addresses without deleting the security group
   or detaching it from the endpoint. Do not clean them up until the actual
   external client owner takes them over or proves that it is independent.

5. Select a protected directory for the state backup and saved plan. Do not
   use the repository or a shared temporary directory.

## Saved-plan handoff

Run this block from a clean, pushed Data Chord checkout. Replace the three
values at the top. Use `staging` first. Repeat for `prod` only after staging is
proved.

```bash
set -Eeuo pipefail

stage=staging
base_profile='<configured-operator-profile>'
migration_dir='<absolute-protected-directory>'

test "$stage" = staging -o "$stage" = prod
test "${migration_dir#/}" != "$migration_dir"
test ! -e "$migration_dir"
install -d -m 700 "$migration_dir"

# Reject ambient OpenTofu and AWS session overrides before the first AWS call.
while IFS='=' read -r name _; do
  case "$name" in
    TF_VAR_* | TF_CLI_ARGS* | TF_WORKSPACE | TF_DATA_DIR | \
      AWS_PROFILE | AWS_DEFAULT_PROFILE | AWS_REGION | AWS_DEFAULT_REGION | \
      AWS_ACCESS_KEY_ID | AWS_SECRET_ACCESS_KEY | AWS_SESSION_TOKEN | AWS_SECURITY_TOKEN)
      unset "$name"
      ;;
  esac
done < <(env)

region="$(aws configure get region --profile "$base_profile")"
test -n "$region"

base_identity="$(aws sts get-caller-identity \
  --output json \
  --profile "$base_profile" \
  --region "$region")"
account_id="$(printf '%s' "$base_identity" | jq -er .Account)"
base_arn="$(printf '%s' "$base_identity" | jq -er .Arn)"
partition="$(printf '%s' "$base_arn" | cut -d : -f 2)"
test "$(printf '%s' "$base_arn" | cut -d : -f 1)" = arn
test "$(printf '%s' "$base_arn" | cut -d : -f 5)" = "$account_id"
test "$partition" = aws -o "$partition" = aws-us-gov
[[ "$account_id" =~ ^[0-9]{12}$ ]]
case "$base_arn" in
  "arn:$partition:iam::$account_id:"* | "arn:$partition:sts::$account_id:"*) ;;
  *) false ;;
esac

deployment_role_arn="arn:$partition:iam::$account_id:role/foundation/datachord-deployer"
assumption="$(aws sts assume-role \
  --role-arn "$deployment_role_arn" \
  --role-session-name data-chord-bdf-handoff \
  --output json \
  --profile "$base_profile" \
  --region "$region")"
assumed_arn="$(printf '%s' "$assumption" | jq -er .AssumedRoleUser.Arn)"
case "$assumed_arn" in
  "arn:$partition:sts::$account_id:assumed-role/datachord-deployer/"*) ;;
  *) false ;;
esac

export AWS_ACCESS_KEY_ID="$(printf '%s' "$assumption" | jq -er .Credentials.AccessKeyId)"
export AWS_SECRET_ACCESS_KEY="$(printf '%s' "$assumption" | jq -er .Credentials.SecretAccessKey)"
export AWS_SESSION_TOKEN="$(printf '%s' "$assumption" | jq -er .Credentials.SessionToken)"
export AWS_REGION="$region"
export AWS_DEFAULT_REGION="$region"
unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_SECURITY_TOKEN

effective_identity="$(aws sts get-caller-identity --output json --region "$region")"
test "$(printf '%s' "$effective_identity" | jq -er .Account)" = "$account_id"
test "$(printf '%s' "$effective_identity" | jq -er .Arn)" = "$assumed_arn"

contract="$(aws ssm get-parameter \
  --name /datachord/foundation/deployment-contract \
  --query Parameter.Value \
  --output text \
  --region "$region")"
contract_fields="$(printf '%s' "$contract" | jq -c 'keys')"
expected_contract_fields='["application_dns_zone_name","application_role_boundary_arn","application_role_path","aws_account_id","aws_partition","aws_region","data_model_store_url","deployment_role_arn","schema_version","state_bucket_name","target_slug"]'
test "$contract_fields" = "$expected_contract_fields"
test "$(printf '%s' "$contract" | jq -er .schema_version)" = 1
test "$(printf '%s' "$contract" | jq -er .target_slug)" = bdf
test "$(printf '%s' "$contract" | jq -er .aws_account_id)" = "$account_id"
test "$(printf '%s' "$contract" | jq -er .aws_partition)" = "$partition"
test "$(printf '%s' "$contract" | jq -er .aws_region)" = "$region"
test "$(printf '%s' "$contract" | jq -er .deployment_role_arn)" = "$deployment_role_arn"

state_bucket="$(printf '%s' "$contract" | jq -r .state_bucket_name)"
role_path="$(printf '%s' "$contract" | jq -r .application_role_path)"
boundary_arn="$(printf '%s' "$contract" | jq -r .application_role_boundary_arn)"
data_model_url="$(printf '%s' "$contract" | jq -r .data_model_store_url)"
dns_zone="$(printf '%s' "$contract" | jq -r '.application_dns_zone_name // empty')"
contract_digest="$(printf '%s' "$contract" | jq -cS . | shasum -a 256 | cut -d ' ' -f 1)"

export TF_DATA_DIR="$migration_dir/tofu"

tofu -chdir=infra init \
  -input=false \
  -reconfigure \
  -backend-config="bucket=$state_bucket" \
  -backend-config="key=datachord/bdf/$stage/tofu.tfstate" \
  -backend-config="region=$region" \
  -backend-config=encrypt=true \
  -backend-config=use_lockfile=true

tofu -chdir=infra workspace select default

tofu -chdir=infra state pull > "$migration_dir/state-before.json"
chmod 600 "$migration_dir/state-before.json"

image_tag="$(tofu -chdir=infra output -raw deployed_image_tag)"

# Export the current optional bypass value as a JSON array. Use [] only after
# you confirm that data-chord/$stage/auth-bypass-cidrs does not exist.
export TF_VAR_auth_bypass_cidrs='<current-json-array-or-[]>'

dns_argument=()
if test -n "$dns_zone"; then
  dns_argument=(-var="hosted_zone_name=$dns_zone")
fi

plan="$migration_dir/bdf-$stage-handoff.tfplan"
tofu -chdir=infra plan \
  -input=false \
  -refresh=false \
  -var-file="env/bdf/$stage.tfvars" \
  -var=target_slug=bdf \
  -var="environment=$stage" \
  -var="aws_partition=$partition" \
  -var="expected_account_id=$account_id" \
  -var="aws_region=$region" \
  -var="application_role_path=$role_path" \
  -var="application_role_boundary_arn=$boundary_arn" \
  -var="data_model_store_url=$data_model_url" \
  -var="netrias_api_key_secret_name=data-chord/$stage/netrias-api-key" \
  -var="image_tag=$image_tag" \
  -var="auth_bypass_cidrs=$TF_VAR_auth_bypass_cidrs" \
  "${dns_argument[@]}" \
  -out="$plan"

tofu -chdir=infra show "$plan"
shown_plan_digest="$(shasum -a 256 "$plan" | cut -d ' ' -f 1)"

# This one-time plan cannot refresh the old root-path IAM roles. Prove that all
# migration addresses are forgotten from recorded state. A missing address or
# any action other than forget stops the handoff.
handoff_actions="$(tofu -chdir=infra show -json "$plan" | jq -c '
  [.resource_changes[]
    | select(.address == "aws_iam_role.task_execution"
      or .address == "aws_iam_role_policy_attachment.task_execution"
      or .address == "aws_iam_role_policy.task_execution_secrets"
      or .address == "aws_iam_role.task"
      or .address == "aws_iam_role_policy.task_workflow_storage"
      or .address == "aws_iam_role.codebuild"
      or .address == "aws_iam_role_policy.codebuild"
      or .address == "aws_ecs_task_definition.app"
      or .address == "aws_security_group.secrets_endpoint[0]"
      or .address == "aws_vpc_endpoint_security_group_association.secretsmanager_tasks[0]")
    | {address, actions: .change.actions}]
  | sort_by(.address)
')"
expected_handoff_actions='[{"address":"aws_ecs_task_definition.app","actions":["forget"]},{"address":"aws_iam_role.codebuild","actions":["forget"]},{"address":"aws_iam_role.task","actions":["forget"]},{"address":"aws_iam_role.task_execution","actions":["forget"]},{"address":"aws_iam_role_policy.codebuild","actions":["forget"]},{"address":"aws_iam_role_policy.task_execution_secrets","actions":["forget"]},{"address":"aws_iam_role_policy.task_workflow_storage","actions":["forget"]},{"address":"aws_iam_role_policy_attachment.task_execution","actions":["forget"]},{"address":"aws_security_group.secrets_endpoint[0]","actions":["forget"]},{"address":"aws_vpc_endpoint_security_group_association.secretsmanager_tasks[0]","actions":["forget"]}]'
test "$handoff_actions" = "$expected_handoff_actions"

printf 'Type APPLY bdf/%s to continue: ' "$stage"
read -r confirmation
test "$confirmation" = "APPLY bdf/$stage"

current_contract="$(aws ssm get-parameter \
  --name /datachord/foundation/deployment-contract \
  --query Parameter.Value \
  --output text \
  --region "$region")"
current_contract_digest="$(printf '%s' "$current_contract" | jq -cS . | shasum -a 256 | cut -d ' ' -f 1)"
test "$current_contract_digest" = "$contract_digest"
test "$(shasum -a 256 "$plan" | cut -d ' ' -f 1)" = "$shown_plan_digest"

tofu -chdir=infra apply -input=false "$plan"
```

## After the handoff

1. Confirm that `tofu -chdir=infra state list` contains none of the addresses
   in `migration-handoff.tf`.

2. Run `just status bdf <stage> <normal-bdf-profile>`.

3. Prove the login, upload, harmonization, review, and download workflow.

4. Confirm that ECS and CodeBuild use the new boundary-controlled application
   roles.

5. Delete the old task definition and root-path IAM roles only in a separate,
   approved cleanup. Use the recorded ARNs. Do not make name-based deletions.
