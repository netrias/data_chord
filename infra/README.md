# Data Chord AWS Deploy

This repository owns the Data Chord application infrastructure and all final
application resource names. The stack creates the load balancer, Cognito login,
ECS service, application IAM roles, workflow bucket, image repository,
CodeBuild project, logs, and alerts.

The `netrias/datachord-infrastructure` repository owns the one-per-account
foundation. That foundation contains the OpenTofu state bucket, deployer role,
deployer permissions, application-role permission boundary, application IAM
path, and state access. A Data Chord deployment does not clone, call, or read
files from the foundation repository.

## Target onboarding contract

Each AWS target has one checked-in contract under `infra/targets`. It contains
only these foundation outputs and deployment selectors:

1. Target name and expected AWS account.

2. AWS region and exact state bucket name.

3. Deployer-role ARN.

4. Application permission-boundary ARN and IAM path.

The current targets are `bdf` and `netrias`. The supported stage names are
`dev`, `qa`, `staging`, and `prod`. Application settings are separate from the
onboarding contract under `infra/env/<target>`.

Supported stage names and configured deployments are different contracts. This
repository currently has application configuration for:

- `bdf/staging`
- `bdf/prod`
- `netrias/staging`

The Netrias staging configuration selects the first intended Netrias
deployment. It is not evidence of a live deployment.

The checked-in contracts are copies of foundation outputs. They do not create
foundation resources. Update a target contract only after the foundation
outputs for that AWS account change.

## State keys

New state uses this key:

```text
datachord/<target>/<stage>/tofu.tfstate
```

BDF staging and production keep their existing live keys:

```text
data-chord/staging/tofu.tfstate
data-chord/prod/tofu.tfstate
```

This compatibility rule is limited to those two known states. The backend uses
the native S3 lock file. It does not use a DynamoDB lock table.

## One-time foundation onboarding

For each AWS account:

1. Apply the target in `netrias/datachord-infrastructure` by its documented
   process.

2. Copy the target outputs into the matching file in `infra/targets`.

3. Configure a local AWS profile that assumes the output
   `deployer_role_arn`. The deploy scripts reject a direct IAM user, a different
   role, or a different account.

4. Add the target application values under `infra/env/<target>`.

5. Complete service prerequisites that are outside this stack. CodeBuild needs
   GitHub source access. Managed DNS needs the configured public Route 53 hosted
   zone.

The foundation deployment policy must also let the deployer read the exact
application permission-boundary policy with `iam:GetPolicy` and
`iam:GetPolicyVersion`. Data Chord reads that policy during planning. This
stops role creation when the boundary does not exist. The permission must name
only the foundation output `application_role_boundary_arn`; it does not permit
changes to the policy.

The account-access foundation has not yet been applied in either target.
Read-only IAM checks found neither target's `/foundation/datachord-deployer`
role nor its `datachord-application-role-boundary` policy. Apply and verify that
foundation layer before a Data Chord plan or BDF handoff.

For Netrias, read-only checks confirmed account `945365518758`, default VPC
`vpc-08c111f13ad3e8b44`, three public subnets in separate availability zones,
an active internet-gateway route, and a GitHub CodeConnections source
credential for CodeBuild. The checked-in Netrias subnet IDs match that evidence.

- `us-east-2a`: `subnet-048dc758402e95744`
- `us-east-2b`: `subnet-0d468bc4f14a6ac33`
- `us-east-2c`: `subnet-0aa3311feda432c84`

Each subnet maps public IPv4 addresses. The main route table has an active
`0.0.0.0/0` route through internet gateway `igw-0f563ac40978db572`.

The Netrias account has no Route 53 hosted zones. Public DNS for `netrias.com`
uses external name servers, and `apps.netrias.com` is not delegated. An
operator must provide the required public DNS zone or explicit certificate and
domain inputs before a live Netrias staging plan. This PR does not create or
delegate DNS.

The Netrias state bucket exists but is empty. Foundation onboarding must enable
and verify versioning and the required bucket policy before Data Chord uses it.
These DNS and state controls remain external gates for the first Netrias
deployment.

## Plan and deploy

Set `AWS_PROFILE` to the profile that assumes the target deployer role. Then
select the target and stage explicitly:

```bash
AWS_PROFILE=datachord-bdf just deploy-plan bdf staging
AWS_PROFILE=datachord-bdf just deploy bdf staging
AWS_PROFILE=datachord-bdf just deploy-infra bdf staging
```

The normal application flow is:

```text
foundation onboarding
  -> assume the shared deployer role
  -> Data Chord selects its target and stage files
  -> Data Chord initializes its S3 state key
  -> first deploy creates missing build prerequisites in the same state
  -> CodeBuild builds the immutable image
  -> Data Chord applies its complete application stack
```

An app deploy requires a named branch, a clean worktree, and a matching commit
on `origin`. CodeBuild builds that commit. OpenTofu records its short commit SHA
as the immutable ECS image tag and then watches the ECS rollout.

On the first deploy, the `deploy` command cannot start CodeBuild until its build
resources exist. It therefore applies one target,
`aws_codebuild_project.app_image`. The dependency graph creates the Data
Chord-owned ECR repository, CodeBuild log group, bounded build role and policy,
and CodeBuild project. It then starts the image build and runs the full apply.
The targeted apply uses the same root, backend, and state as the full stack. It
does not create a second stack.

If a previous attempt already pushed the same immutable commit image, a retry
reuses that image and continues with the full apply. It does not try to overwrite
or rebuild an immutable tag.

`NETRIAS_API_KEY` is required only when the stage secret does not exist or when
you want to replace its value:

```bash
AWS_PROFILE=datachord-bdf NETRIAS_API_KEY='replace-with-key' just deploy bdf staging
```

An infrastructure-only deploy reuses the current ECS image. It is not the
first-deploy command. Run `just deploy <target> <stage>` first when no image has
been deployed.

Other operational commands use the same target-stage order:

```bash
AWS_PROFILE=datachord-bdf just deploy-status bdf staging
AWS_PROFILE=datachord-bdf just deploy-logs bdf staging
AWS_PROFILE=datachord-bdf just deploy-build bdf staging
AWS_PROFILE=datachord-bdf just invite-user bdf staging user@example.com
```

## State and IAM handoff safety

The removed state-bucket bootstrap script was not represented in Data Chord
OpenTofu state. Removing it cannot plan bucket destruction. The live BDF
staging and production state files contain application resources only, and this
repository keeps their current keys.

The existing BDF application roles use the IAM root path and no permission
boundary. The foundation deployer cannot delete those root-path roles. OpenTofu
therefore uses `removed` blocks with `destroy = false` to forget the legacy IAM
and task-definition addresses without deleting AWS resources. It creates new
roles under the foundation path, applies the foundation boundary, registers a
new task definition, and then updates ECS and CodeBuild.

The normal foundation deployer cannot refresh the legacy IAM objects. Do not
use the normal deploy command for the first BDF handoff. Do not use
`-refresh=false` for the planned handoff. An approved BDF foundation
administrator must create and apply a saved plan with normal refresh.

Use this sequence once for `staging` and once for `prod`:

1. Confirm that the BDF foundation and application boundary exist.

2. Record the current ECS task-definition ARN and CodeBuild service-role ARN.

3. Initialize the existing BDF state key with the privileged profile. Pull a
   local state backup. Record the current S3 state-object version.

4. Read the current `deployed_image_tag` output. If the optional auth-bypass
   secret exists, export its JSON array as `TF_VAR_auth_bypass_cidrs`. Otherwise,
   confirm that it is absent and use `[]`.

5. Create a saved plan with normal refresh and all explicit deployment inputs:

```bash
stage=staging
operator_profile='<approved-privileged-profile>'
umask 077
migration_dir="$(mktemp -d)"
source infra/scripts/lib.sh
require_configured_deployment bdf "$stage"

state_key="$(state_key_for bdf "$stage")"
TF_DATA_DIR="$migration_dir/tofu-data"
export TF_DATA_DIR

AWS_PROFILE="$operator_profile" tofu -chdir=infra init \
  -backend-config="bucket=$(target_value bdf state_bucket_name)" \
  -backend-config="key=$state_key" \
  -backend-config="region=$(target_value bdf aws_region)" \
  -backend-config="encrypt=true" \
  -backend-config="use_lockfile=true" \
  -input=false \
  -reconfigure

AWS_PROFILE="$operator_profile" tofu -chdir=infra state pull \
  >"$migration_dir/bdf-$stage-before.tfstate"
AWS_PROFILE="$operator_profile" aws s3api list-object-versions \
  --bucket "$(target_value bdf state_bucket_name)" \
  --prefix "$state_key" \
  --query "Versions[?Key=='$state_key' && IsLatest].VersionId | [0]" \
  --output text >"$migration_dir/bdf-$stage-state-version.txt"
image_tag="$(AWS_PROFILE="$operator_profile" tofu -chdir=infra output -raw deployed_image_tag)"

AWS_PROFILE="$operator_profile" tofu -chdir=infra plan \
  -input=false \
  -out="$migration_dir/bdf-$stage.tfplan" \
  -var-file="$INFRA_DIR/env/bdf/common.tfvars" \
  -var-file="$INFRA_DIR/env/bdf/$stage.tfvars" \
  -var="expected_account_id=$(target_value bdf expected_account_id)" \
  -var="aws_region=$(target_value bdf aws_region)" \
  -var="application_role_boundary_arn=$(target_value bdf application_role_boundary_arn)" \
  -var="application_role_path=$(target_value bdf application_role_path)" \
  -var="environment=$stage" \
  -var="netrias_api_key_secret_name=$(netrias_api_key_secret_name_for "$stage")" \
  -var="image_tag=$image_tag"

tofu -chdir=infra show "$migration_dir/bdf-$stage.tfplan"
```

6. Stop unless the plan forgets all eight legacy addresses without destroy,
   creates the three bounded application roles, and has no unexplained destroy
   or replacement. Apply only the saved plan:

```bash
AWS_PROFILE="$operator_profile" tofu -chdir=infra apply \
  -input=false "$migration_dir/bdf-$stage.tfplan"
```

7. Use the normal foundation-role `deploy` command for a new commit. Confirm
   the CodeBuild build succeeds and ECS is stable.

Keep the old root-path roles through a rollback period. Before cleanup, a
privileged operator can restore the recorded ECS task definition and CodeBuild
service role. Do not restore an old state version after the new bounded roles
exist. Do not return to source that manages the root-path roles.

After the new deployment is healthy and its CodeBuild project has completed a
build, an approved privileged operator must remove the old roles. The shared
deployer cannot do this cleanup. For each migrated BDF stage, delete these
resources in this order, where `<stage>` is `staging` or `prod`:

1. Deregister the recorded old ECS task-definition revision.

2. Inline policy `data-chord-<stage>-codebuild` and role
   `data-chord-<stage>-codebuild`.

3. Inline policy `data-chord-<stage>-workflow-storage` and role
   `data-chord-<stage>-task`.

4. Inline policy `data-chord-<stage>-task-secrets`, the
   `AmazonECSTaskExecutionRolePolicy` attachment, and role
   `data-chord-<stage>-task-exec`.

After the rollback period, set the profile to an approved identity that can
manage the old root-path roles:

```bash
stage=staging
old_task_definition='<recorded-task-definition-arn>'
operator_profile='<approved-privileged-profile>'
role_prefix="data-chord-$stage"

AWS_PROFILE="$operator_profile" aws ecs deregister-task-definition \
  --region us-east-2 \
  --task-definition "$old_task_definition"

AWS_PROFILE="$operator_profile" aws iam delete-role-policy \
  --role-name "$role_prefix-codebuild" \
  --policy-name "$role_prefix-codebuild"
AWS_PROFILE="$operator_profile" aws iam delete-role \
  --role-name "$role_prefix-codebuild"

AWS_PROFILE="$operator_profile" aws iam delete-role-policy \
  --role-name "$role_prefix-task" \
  --policy-name "$role_prefix-workflow-storage"
AWS_PROFILE="$operator_profile" aws iam delete-role \
  --role-name "$role_prefix-task"

AWS_PROFILE="$operator_profile" aws iam delete-role-policy \
  --role-name "$role_prefix-task-exec" \
  --policy-name "$role_prefix-task-secrets"
AWS_PROFILE="$operator_profile" aws iam detach-role-policy \
  --role-name "$role_prefix-task-exec" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
AWS_PROFILE="$operator_profile" aws iam delete-role \
  --role-name "$role_prefix-task-exec"
```

After this cleanup, rollback application code or images through the current
Data Chord configuration so the bounded application roles stay in use.

No live apply, import, state mutation, IAM write, or bucket write was used to
prepare this change. Live proof exists only for the current BDF staging and
production state layout. It does not prove Netrias staging or any unconfigured
target-stage pair in live AWS.

## Optional VPN auth bypass

Store trusted source CIDRs as a JSON array in this stage secret:

```text
data-chord/<stage>/auth-bypass-cidrs
```

The deploy script passes the list to OpenTofu without storing it in a tfvars
file. Requests from those CIDRs bypass Cognito. All other requests use Cognito.

## CodeBuild GitHub access

CodeBuild source credentials are an account and region prerequisite. Import
them through the approved account onboarding process. Do not store a GitHub
token in OpenTofu, Secrets Manager values committed to this repository, or the
target contract.

## Alerts

Each stage owns its CloudWatch alarms, EventBridge failure rules, and SNS topic.
Production has an email subscriber by default. Other stages keep alarms without
email unless their stage file adds subscribers.
