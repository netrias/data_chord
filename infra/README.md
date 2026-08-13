# Data Chord AWS deployment

This directory owns one complete Data Chord application deployment. OpenTofu
creates these application resources:

- A small public VPC with two public subnets.
- An Application Load Balancer with Cognito authentication and managed TLS.
- One ECS Fargate service.
- One versioned and encrypted S3 workflow bucket.
- One ECR repository and one CodeBuild project.
- The application IAM roles and CloudWatch log groups.

The stack does not create NAT gateways, private subnets, VPC endpoints,
databases, alarms, email subscriptions, EventBridge alert rules, or ALB access
logs.

The separate `netrias/datachord-infrastructure` repository owns account-level
resources. These include the state bucket, deployer role, application-role
permission boundary, IAM path, and state-access policy.

## Target contract

Each target file under `infra/targets` contains the account-level outputs that
this stack consumes:

- AWS account and region.
- State bucket.
- Deployer-role ARN.
- Application permission-boundary ARN and IAM path.

Environment files under `infra/env/<target>` contain application settings such
as the DNS label and health-check timing.

The configured deployments are:

- `bdf/staging`
- `bdf/prod`
- `netrias/staging`

The Netrias staging files define a possible deployment. They do not prove that
a live deployment exists.

## State

Canonical state uses one visible key convention:

```text
datachord/<target>/<stage>/tofu.tfstate
```

The S3 backend uses OpenTofu native lock files. It does not use a DynamoDB lock
table.

BDF staging has moved to the canonical key:

```text
datachord/bdf/staging/tofu.tfstate
```

BDF production keeps `data-chord/prod/tofu.tfstate` until its own reviewed
migration. No other legacy key is supported.

## Account prerequisites

Before the first deployment:

1. Apply the target foundation in `netrias/datachord-infrastructure`.
2. Copy its outputs into the matching file under `infra/targets`.
3. Configure an AWS profile that assumes the target `deployer_role_arn`.
4. Create `data-chord/<stage>/netrias-api-key` in Secrets Manager.
5. Confirm that the public Route 53 hosted zone exists.
6. Authorize CodeBuild to read `netrias/data_chord` through the approved AWS
   account setup.

The deploy script rejects a direct IAM user, the wrong account, and the wrong
role.

Each target and stage owns its own VPC. The ALB and Fargate task use two public
subnets in separate Availability Zones. The task receives a public IP, but its
security group accepts application traffic only from the ALB.

## Operator commands

The normal interface has three commands. Pass the AWS profile explicitly:

```bash
just plan bdf staging datachord-bdf
just deploy bdf staging datachord-bdf
just status bdf staging datachord-bdf
```

`plan` initializes the correct state key, creates a saved plan under
`build/plans`, displays it, and checks durable-storage safety. It does not apply
infrastructure or start a build.

`deploy` accepts only a named branch with a clean worktree and a commit that
matches the branch on `origin`. It then:

1. Creates missing ECR and CodeBuild prerequisites.
2. Builds or reuses the immutable image for that commit.
3. Creates and displays a saved final plan.
4. Applies that exact saved plan.
5. Waits for a stable ECS rollout and healthy load-balancer target.

`status` reads the current OpenTofu outputs, ECS status, and target health. It
does not change AWS.

Prepare or replace the stage API secret only when needed:

```bash
AWS_PROFILE=datachord-bdf NETRIAS_API_KEY='replace-with-key' \
  infra/scripts/bootstrap-secrets.sh bdf staging ensure
```

The secret helper is idempotent. Normal plan and deploy commands only verify
that the secret exists.

## Safety rules

The workflow bucket contains durable application data. Every normal plan stops
if OpenTofu would delete or replace it.

Retiring an old ALB access-log bucket takes two saved plans. First disable ALB
logging and set the old bucket to allow removal. Apply that plan. Remove the
bucket resource in a later plan. The deployment script rejects a one-step
retirement.

The application IAM roles use the foundation-owned path and permission
boundary. `migration-handoff.tf` forgets the old unbounded role addresses
without deleting those roles. A deployment stops if a legacy handoff address
still exists in state.

BDF production still needs its own reviewed state and IAM handoff before the
new shared contract can be applied there. Do not copy or restore the BDF
staging state for production.

## CodeBuild source access

CodeBuild reads `https://github.com/netrias/data_chord.git`. Source
authorization is an account prerequisite. Do not put a GitHub token in
OpenTofu variables, state, source files, or Docker build arguments.

## Diagnostics

The stack keeps application and CodeBuild logs in CloudWatch for 14 days.
Start with:

```bash
just status <target> <stage> <profile>
```

Then inspect the named ECS service or CodeBuild log group. The stack does not
create alerts or notification infrastructure.
