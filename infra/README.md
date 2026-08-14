# Data Chord AWS Deploy

This repository owns the Data Chord application infrastructure and all final
application resource names. The stack creates the load balancer, Cognito login,
ECS service, dedicated public VPC, application IAM roles, workflow bucket,
image repository, CodeBuild project, and build and runtime logs.

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

BDF production keeps its existing live key:

```text
data-chord/prod/tofu.tfstate
```

This compatibility rule is limited to that known state. The backend uses
the native S3 lock file. It does not use a DynamoDB lock table.

## One-time foundation onboarding

For each AWS account:

1. Apply the target in `netrias/datachord-infrastructure` by its documented
   process.

2. Copy the target outputs into the matching file in `infra/targets`.

3. Run `just setup <target> [source-profile]`. It creates the local
   `datachord-<target>` profile and confirms that it assumes the output
   `deployer_role_arn`. The source profile defaults to `default`.

4. Add the target application values under `infra/env/<target>`.

5. Complete service prerequisites that are outside this stack. CodeBuild needs
   the read-only GitHub App secret described below. Managed DNS needs the
   configured public Route 53 hosted zone.

The foundation application permission-boundary policy must exist at the
configured ARN. AWS validates it when this stack creates or replaces an
application IAM role. That boundary must allow
`secretsmanager:GetSecretValue` for the build role and
`bedrock-mantle:CallWithBearerToken` for the task role. The application stack
still limits each role to its own required action.

Before a Data Chord plan or BDF handoff, verify these durable prerequisites:

- The account-access foundation exists, including the deployer role,
  application-role boundary, application IAM path, and state bucket with
  versioning and the required bucket policy.
- The deployer can use `secretsmanager:DescribeSecret`,
  `secretsmanager:GetSecretValue`, `secretsmanager:CreateSecret`, and
  `secretsmanager:PutSecretValue` for the stage API secret.
- The read-only GitHub App is installed for the agentic harmonization repository.
- The matching public Route 53 `hosted_zone_name` exists.
- Public DNS is delegated to the configured zone or records. External DNS
  delegation remains an operator responsibility.

Any change to DNS, certificate, domain, or another deployment input must be
committed and pushed before deploy. Deploy commands reject dirty worktrees and
commits that do not match the selected branch on `origin`.

Each target and stage owns one small application VPC. It contains two public
subnets in separate Availability Zones, one internet gateway, and one public
route table. The ALB and Fargate task use those subnets. The task receives a
public IP, but its security group accepts application traffic only from the ALB.
The application network has no NAT gateway, private subnet, VPC endpoint, or
database route.

## Local AWS setup

Run setup once on each operator machine:

```bash
just setup netrias
# Or select another existing login or SSO profile:
just setup netrias company-sso
```

Setup writes only `role_arn`, `source_profile`, and `region` to the local
`datachord-<target>` AWS profile. It never writes credentials or tokens. It
stops rather than overwrite a profile that has conflicting settings.

Deployment commands use `datachord-<target>` when it exists. Otherwise, they
keep an existing `AWS_PROFILE` or use ambient role credentials for CI, EC2, or
OIDC environments. This prevents an unrelated local profile from overriding a
target that has completed setup. Every path must still pass the exact account
and deployer-role check.

Setup verifies local role access. The plan remains the authoritative check for
the state backend, stage secret, hosted zone, GitHub App secret, and other AWS
dependencies.

## Plan and deploy

The normal operator interface has three commands:

```bash
just plan netrias staging
just deploy netrias staging
just status netrias staging
```

`plan` creates and displays a saved plan under `build/plans`. It disables state
locking because it is read-only. It does not apply infrastructure or start a
build.

`deploy` accepts only a named branch with a clean worktree and a commit that
matches the branch on `origin`. CodeBuild builds that exact commit. The command
creates and displays a saved final plan, then applies that exact plan file. It
does not run a direct auto-approved apply. It waits for a stable ECS rollout
and healthy load-balancer targets.

The workflow S3 bucket contains durable application data. Every normal plan
stops if OpenTofu would delete or replace that bucket.

`status` reads the current OpenTofu outputs and ECS status. It does not apply
infrastructure.

The normal application flow is:

```text
foundation onboarding
  -> assume the shared deployer role
  -> prepare the stage application API secret
  -> Data Chord selects its target and stage files
  -> Data Chord initializes its S3 state key
  -> save and display the Data Chord plan
  -> create the Data Chord application VPC
  -> reconcile the CodeBuild project with a saved targeted plan
  -> CodeBuild builds the immutable image
  -> save, display, and apply the exact final plan
  -> verify ECS and load-balancer target health
```

A plan uses the currently deployed image tag when one exists. For an empty
state, it uses the current short commit SHA as the proposed first image tag.
`DATA_CHORD_IMAGE_TAG` can select another existing immutable image for a plan.

Each deployment creates, displays, and applies one saved plan that targets the
CodeBuild project. This makes the project current before it builds the image.
The final saved plan then reconciles the full stack, including ECR.

Removing an existing ALB access-log bucket takes two saved plans. First, keep
`aws_s3_bucket.alb_logs` in configuration with `force_destroy = true`, remove
the load balancer access-log block, and apply that saved plan. Then remove the
bucket resource in a later change. The normal workflow stops a one-stage
deletion while the saved state still has `force_destroy = false`.

If a previous attempt already pushed the same immutable commit image, a retry
reuses that image and continues with the full apply. It does not try to overwrite
or rebuild an immutable tag.

Before the first plan, prepare the stage API secret. This is a separate,
infrequent operation:

```bash
AWS_PROFILE=datachord-bdf NETRIAS_API_KEY='replace-with-key' \
  infra/scripts/bootstrap-secrets.sh bdf staging ensure
```

The secret helper is idempotent. Plan and deploy only verify the secret. They
never write it. After local setup, the target profile name follows the
`datachord-<target>` convention.

## State and IAM handoff safety

The BDF staging and production state files contain application resources only,
and this repository keeps their existing keys.

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
Deploy and build write modes inspect state without refresh and stop before
apply if any legacy handoff address remains. Plan mode stays available for
review.

Use this sequence once for `staging` and once for `prod`:

1. Confirm that the BDF foundation and application boundary exist.

2. Record the current ECS task-definition ARN and CodeBuild service-role ARN.

3. Initialize the existing BDF state key with the privileged profile. Pull a
   local state backup. Record the current S3 state-object version.

4. Read the current `deployed_image_tag` output.

5. Create a saved plan with normal refresh, review it, and apply that exact
   saved plan only after explicit confirmation:

```bash
bash <<'BDF_HANDOFF'
set -Eeuo pipefail
stage=staging
operator_profile='<approved-privileged-profile>'
umask 077
migration_dir='<absolute-path-in-approved-protected-storage>'
if [[ "$migration_dir" != /* ]]; then
  printf 'migration_dir must be an operator-supplied absolute protected path.\n' >&2
  exit 1
fi
if [[ -e "$migration_dir" ]]; then
  printf 'migration_dir already exists: %s\n' "$migration_dir" >&2
  exit 1
fi
mkdir -m 700 "$migration_dir"
source infra/scripts/lib.sh
require_configured_deployment bdf "$stage"
activate_aws_profile "$operator_profile"
expected_account="$(target_value bdf expected_account_id)"
identity="$(aws sts get-caller-identity --query '[Account,Arn]' --output text)"
read -r account_id caller_arn <<<"$identity"
if [[ "$account_id" != "$expected_account" ]]; then
  fail "Privileged profile resolved to account '$account_id', not BDF account '$expected_account'. Current caller: $caller_arn"
fi

state_key="$(state_key_for bdf "$stage")"
TF_DATA_DIR="$migration_dir/tofu-data"
export TF_DATA_DIR

tofu -chdir=infra init \
  -backend-config="bucket=$(target_value bdf state_bucket_name)" \
  -backend-config="key=$state_key" \
  -backend-config="region=$(target_value bdf aws_region)" \
  -backend-config="encrypt=true" \
  -backend-config="use_lockfile=true" \
  -input=false \
  -reconfigure

tofu -chdir=infra state pull \
  >"$migration_dir/bdf-$stage-before.tfstate"
aws s3api list-object-versions \
  --bucket "$(target_value bdf state_bucket_name)" \
  --prefix "$state_key" \
  --query "Versions[?Key=='$state_key' && IsLatest].VersionId | [0]" \
  --output text >"$migration_dir/bdf-$stage-state-version.txt"
image_tag="$(tofu -chdir=infra output -raw deployed_image_tag)"

tofu -chdir=infra plan \
  -input=false \
  -out="$migration_dir/bdf-$stage.tfplan" \
  -var-file="$INFRA_DIR/env/bdf/common.tfvars" \
  -var-file="$INFRA_DIR/env/bdf/$stage.tfvars" \
  -var="expected_account_id=$(target_value bdf expected_account_id)" \
  -var="aws_region=$(target_value bdf aws_region)" \
  -var="application_role_boundary_arn=$(target_value bdf application_role_boundary_arn)" \
  -var="application_role_path=$(target_value bdf application_role_path)" \
  -var="deployment_target=bdf" \
  -var="environment=$stage" \
  -var="image_tag=$image_tag"

tofu -chdir=infra show "$migration_dir/bdf-$stage.tfplan"

printf '\nSTOP unless the plan forgets all eight legacy addresses without destroy, creates the three bounded application roles, and has no unexplained destroy or replacement.\n\n'
read -r -p "Type apply to apply this exact saved plan: " confirmation </dev/tty
if [[ "$confirmation" != "apply" ]]; then
  printf 'Saved plan was not applied.\n' >&2
  exit 1
fi

tofu -chdir=infra apply \
  -input=false "$migration_dir/bdf-$stage.tfplan"

printf '\nHandoff evidence: %s\nRetain this directory in approved protected storage through the rollback period.\n' "$migration_dir"
BDF_HANDOFF
```

6. Use the normal foundation-role `deploy` command for a new commit. Confirm
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
set -Eeuo pipefail
stage=staging
old_task_definition='<recorded-task-definition-arn>'
operator_profile='<approved-privileged-profile>'
role_prefix="data-chord-$stage"
source infra/scripts/lib.sh
activate_aws_profile "$operator_profile"
expected_account="$(target_value bdf expected_account_id)"
identity="$(aws sts get-caller-identity --query '[Account,Arn]' --output text)"
read -r account_id caller_arn <<<"$identity"
if [[ "$account_id" != "$expected_account" ]]; then
  fail "Privileged profile resolved to account '$account_id', not BDF account '$expected_account'. Current caller: $caller_arn"
fi

aws ecs deregister-task-definition \
  --region "$(target_value bdf aws_region)" \
  --task-definition "$old_task_definition"

aws iam delete-role-policy \
  --role-name "$role_prefix-codebuild" \
  --policy-name "$role_prefix-codebuild"
aws iam delete-role \
  --role-name "$role_prefix-codebuild"

aws iam delete-role-policy \
  --role-name "$role_prefix-task" \
  --policy-name "$role_prefix-workflow-storage"
aws iam delete-role \
  --role-name "$role_prefix-task"

aws iam delete-role-policy \
  --role-name "$role_prefix-task-exec" \
  --policy-name "$role_prefix-task-secrets"
aws iam detach-role-policy \
  --role-name "$role_prefix-task-exec" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam delete-role \
  --role-name "$role_prefix-task-exec"
```

After this cleanup, rollback application code or images through the current
Data Chord configuration so the bounded application roles stay in use.
After both BDF stages complete the handoff, rollback period, and old-role
cleanup, remove `migration-handoff.tf` in a separate reviewed change.

## CodeBuild GitHub access

Create one read-only GitHub App secret named `data-chord/build/github-app` in
each deployment account. Its JSON value must contain `app_id`,
`installation_id`, and `private_key`. Install the app only for the agentic
harmonization repository and grant read-only Contents permission.

CodeBuild creates a short-lived installation token for each build. It passes
that token to Docker as a build secret. The token and private key do not enter
an image layer. Do not commit either credential to this repository or a target
contract.

Trusted GitHub Actions jobs use the same read-only App. Configure these values
in the public Data Chord repository:

- Repository variable `DATA_CHORD_BUILD_APP_ID`: the App ID.
- Repository secret `DATA_CHORD_BUILD_APP_PRIVATE_KEY`: the App private key.

The workflow requests a short-lived token for only
`netrias/agentic_harmonization`. Python and browser jobs do not run for pull
requests from forks because GitHub does not expose private credentials to those
jobs. The public infrastructure and JavaScript checks still run.

## Diagnostics

The stack retains CloudWatch logs for the application and CodeBuild. Start with
`just status <target> <stage>` when a deployment fails. Deployment errors state
the failed check, its likely cause, and the next operator action. The stack does
not create alarms, email subscriptions, EventBridge alert rules, or ALB access
logs.
