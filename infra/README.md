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

The foundation has not yet been proved by a live apply in either target. The
Netrias account also did not contain the configured `apps.netrias.com` hosted
zone during the read-only investigation. Complete these operator steps before
the first Netrias plan.

## Plan and deploy

Set `AWS_PROFILE` to the profile that assumes the target deployer role. Then
select the target and stage explicitly:

```bash
AWS_PROFILE=datachord-bdf just deploy-plan bdf staging
AWS_PROFILE=datachord-bdf just deploy-app bdf staging
AWS_PROFILE=datachord-bdf just deploy-infra bdf staging
```

The normal application flow is:

```text
foundation onboarding
  -> assume the shared deployer role
  -> Data Chord selects its target and stage files
  -> Data Chord initializes its S3 state key
  -> Data Chord plans or applies its application stack
```

An app deploy requires a named branch, a clean worktree, and a matching commit
on `origin`. CodeBuild builds that commit. OpenTofu records its short commit SHA
as the immutable ECS image tag and then watches the ECS rollout.

`NETRIAS_API_KEY` is required only when the stage secret does not exist or when
you want to replace its value:

```bash
AWS_PROFILE=datachord-bdf NETRIAS_API_KEY='replace-with-key' just deploy bdf staging
```

An infrastructure-only deploy reuses the current ECS image. For a first deploy,
set an existing immutable tag:

```bash
AWS_PROFILE=datachord-bdf DATA_CHORD_IMAGE_TAG=abc123def456 just deploy-infra bdf staging
```

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
new task definition, and then updates ECS and CodeBuild. Review every plan and
do not apply a plan with an unexplained destroy or replace action.

Before the first apply, record the current ECS task-definition ARN and the old
CodeBuild role ARN. Keep the old root-path roles through a rollback period. If
the new roles fail, an approved privileged operator can return the ECS service
to the recorded task definition and CodeBuild project to the recorded role.
Do not revert to source that manages root-path roles through the foundation
deployer because that role cannot create or manage them.

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

Use this sequence once for `staging` and once for `prod`. Set the profile to an
approved identity that can manage the old root-path roles:

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
production state layout. It does not prove BDF dev or qa, any Netrias stage, or
all four stages in live AWS.

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
