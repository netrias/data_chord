# Data Chord AWS Deploy

This stack is intentionally small. It creates:

1. An internet-facing Application Load Balancer with Cognito login.

2. An ECS Fargate service for the app.

3. An S3 bucket for durable workflow storage.

4. An ECR repository for the Docker image.

5. A CodeBuild project that runs Python checks, builds the Docker image, and pushes an immutable commit tag to ECR.

The app uses local container disk only as scratch. Durable workflow files go to S3 through `DATA_CHORD_STORAGE=s3`.

## Environments

Deployment config is checked in under `infra/env`.

1. `infra/env/common.tfvars`

2. `infra/env/staging.tfvars`

3. `infra/env/prod.tfvars`

Each environment has a matching S3 backend config:

1. `infra/env/staging.backend.hcl`

2. `infra/env/prod.backend.hcl`

## Deploy

The normal app deploy path is:

```text
git commit -> immutable image tag -> OpenTofu task definition -> ECS rollout
```

CodeBuild does not update ECS directly. OpenTofu records the image tag in the
task definition, so OpenTofu state and ECS agree on what is running.

Use one command and specify the environment explicitly:

```bash
AWS_PROFILE=strides NETRIAS_API_KEY='replace-with-key' just deploy staging
```

or:

```bash
AWS_PROFILE=strides NETRIAS_API_KEY='replace-with-key' just deploy prod
```

`NETRIAS_API_KEY` is only required when the environment secret does not exist
yet, or when you want the deploy script to update it.

The deploy script is idempotent. It creates or updates:

1. The S3 OpenTofu state bucket.

2. The Secrets Manager secret for `NETRIAS_API_KEY`.

3. The OpenTofu-managed AWS infrastructure.

4. The Cognito user pool and app client.

5. The CodeBuild image build.

6. The OpenTofu-managed ECS task definition and rollout.

`just deploy <env>` and `just deploy-app <env>` both build the current pushed
Git commit, push only the short commit SHA image tag, apply OpenTofu with
`image_tag=<sha>`, and then watch the ECS rollout.

Use an app-only deploy for normal code changes:

```bash
AWS_PROFILE=strides just deploy-app staging
```

Use an infra-only deploy when the image should stay the same:

```bash
AWS_PROFILE=strides just deploy-infra staging
```

Infra-only deploys reuse the currently deployed ECS image tag. If there is no
current ECS service yet, set `DATA_CHORD_IMAGE_TAG` to an existing immutable ECR
tag:

```bash
AWS_PROFILE=strides DATA_CHORD_IMAGE_TAG=abc123def456 just deploy-infra staging
```

## Alerting

Each environment creates its own CloudWatch alarms, EventBridge failure rules,
and SNS topic. Alert names and messages include the environment name, for
example `STAGING` or `PROD`, so staging failures are easy to separate from
production failures.

Subscribe email recipients by setting `alert_email_addresses` in the matching
environment tfvars file or with a deploy-time variable. AWS sends each address a
confirmation email before it receives alerts.

Production is the only environment subscribed by default. Staging still has
alarms for manual inspection, but it does not send email unless a recipient is
explicitly added to `infra/env/staging.tfvars`.

Only outage and user-visible failure alarms publish to email. Warning alarms
such as high CPU, high memory, high response time, and app ERROR logs are kept
in CloudWatch without email actions. CloudWatch OK transitions also do not send
email, so deploys do not generate a recovery-message burst.

## Optional VPN Auth Bypass

The HTTPS listener normally requires Cognito login. An environment can also
trust specific VPN egress CIDRs by storing a JSON array in Secrets Manager:

```text
data-chord/<environment>/auth-bypass-cidrs
```

For example:

```json
["203.0.113.10/32"]
```

During deploy, the script loads this value into OpenTofu as
`auth_bypass_cidrs`. The CIDR list is not checked into git. Requests from those
source IPs are forwarded directly to the app; everyone else still uses Cognito.
Bypassed requests do not include the ALB OIDC identity header, so the app treats
them as the shared fallback local user.

## Deploy Source

CodeBuild builds the current Git commit, not your local working tree. App
deploys require:

1. A named branch, not a detached `HEAD`.

2. No uncommitted changes.

3. The current commit already pushed to `origin`.

If your local changes are unrelated to the deploy, you can bypass only the dirty
working-tree check:

```bash
DATA_CHORD_DEPLOY_ALLOW_DIRTY=1 just deploy staging
```

That still deploys the current committed `HEAD`. It does not upload or build
uncommitted files.

The deployed image tag is the first 12 characters of the deployed commit SHA.
The deploy path does not push or deploy `latest`.

## Manual Prerequisite: CodeBuild GitHub Access

CodeBuild needs read access to `netrias/data_chord` before it can clone the repo
and build the Docker image. This credential is a one-time AWS account and region
bootstrap step. It is not stored in OpenTofu, Secrets Manager, or this repo.

Current target:

1. AWS profile: `strides`

2. AWS account: `084828580051`

3. Region: `us-east-2`

4. Repo: `netrias/data_chord`

Create a GitHub personal access token with enough access to clone the private
repo. The current preferred short-term path is a classic token, because the
fine-grained token approval flow can block deployment. Use the smallest classic
scope that works for private repo clone access; GitHub commonly requires the
classic `repo` scope for private repositories, which is broader than ideal.

Import the token into CodeBuild:

```bash
AWS_PROFILE=strides aws codebuild import-source-credentials \
  --region us-east-2 \
  --server-type GITHUB \
  --auth-type PERSONAL_ACCESS_TOKEN \
  --should-overwrite \
  --token "$GITHUB_TOKEN"
```

Verify that CodeBuild sees the credential:

```bash
AWS_PROFILE=strides aws codebuild list-source-credentials \
  --region us-east-2 \
  --output table
```

Expected non-secret result:

```text
serverType: GITHUB
authType: PERSONAL_ACCESS_TOKEN
arn: arn:aws:codebuild:us-east-2:084828580051:token/github
```

When the GitHub token expires, create a replacement token and rerun the import
command. `--should-overwrite` replaces the existing CodeBuild source credential
for GitHub in this AWS account and region.

A pending fine-grained token named `CodeBuild data_chord source` may exist in
GitHub from the first setup attempt. Once the classic token is imported and a
CodeBuild clone succeeds, delete that pending token so there is only one active
credential path to maintain.

## Troubleshooting

Plan without deploying:

```bash
just deploy-plan staging
```

Inspect current deployment status:

```bash
just deploy-status staging
```

Show recent app and build logs:

```bash
just deploy-logs staging
```

Build and push the image without applying OpenTofu or rolling ECS:

```bash
just deploy-build staging
```

Redeploy a code change through OpenTofu:

```bash
just deploy-app staging
```

## Giving Users Access

Cognito is configured so only admins can create users. Invite a user with:

```bash
AWS_PROFILE=strides just invite-user staging user@example.com
```

For production, use `prod` instead of `staging`.

The command creates the Cognito user and Cognito emails them a temporary
password. The email includes the Data Chord URL, a short description of the
service, the username, and the temporary password.

You can also print the app URL directly:

```bash
tofu -chdir=infra output -raw app_url
```

If the user already exists and needs a fresh temporary password email, resend
the invitation with:

```bash
AWS_PROFILE=strides just resend-user-invite staging user@example.com
```

The resend command is only for users who are still in Cognito's temporary
password onboarding state. It refuses to resend for confirmed users so it does
not disturb an account that has already finished setup.

## Network

The Fargate tasks run in existing public subnets with a security group that only
allows inbound traffic from the load balancer. This avoids NAT gateways, VPC
endpoints, and VPC quota management for the first hosted version. Moving tasks
to private subnets is a good later hardening step if this becomes a shared
production deployment.

## Useful Outputs

```bash
tofu -chdir=infra output app_url
tofu -chdir=infra output workflow_bucket
tofu -chdir=infra output ecr_repository_url
tofu -chdir=infra output codebuild_project_name
```
