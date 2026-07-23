# Data Chord AWS Deploy

This stack is intentionally small. It creates:

1. An internet-facing Application Load Balancer with Cognito login.

2. An ECS Fargate service for the app.

3. An S3 bucket for durable workflow storage.

4. An ECR repository for the Docker image.

5. A CodeBuild project that runs Python checks, builds the Docker image, and pushes an immutable commit tag to ECR.

The app uses local container disk only as scratch. Durable workflow files go to S3 through `DATA_CHORD_STORAGE=s3`.

## Deployment targets and stages

A deployment is identified by two explicit values:

1. The **target** selects the AWS account, network, DNS zone, and state bucket.
   Checked-in targets are `strides` and `netrias`.

2. The **stage** selects application settings such as secret names, DNS labels,
   scaling, and alerts. Supported stages are `staging` and `prod`.

Configuration is grouped by target:

```text
infra/env/
  strides/
    common.tfvars
    staging.tfvars
    staging.backend.hcl
    prod.tfvars
    prod.backend.hcl
  netrias/
    common.tfvars
    staging.tfvars
    staging.backend.hcl
    prod.tfvars
    prod.backend.hcl
```

Each target's `common.tfvars` contains its expected AWS account ID. Deployment
scripts compare that value with `sts:GetCallerIdentity` before they access the
state bucket, secrets, or application infrastructure. `AWS_PROFILE` is required;
there is no implicit default account.

The Netrias target expects the public Route 53 zone `apps.netrias.com` to exist
and be delegated before its first deployment. The stack manages the application
records and ACM certificate beneath that zone.

## Deploy

The normal app deploy path is:

```text
git commit -> immutable image tag -> OpenTofu task definition -> ECS rollout
```

CodeBuild does not update ECS directly. OpenTofu records the image tag in the
task definition, so OpenTofu state and ECS agree on what is running.

Use one command and specify both target and stage:

```bash
AWS_PROFILE=strides NETRIAS_API_KEY='replace-with-key' just deploy strides staging
```

The equivalent Netrias deployment uses a profile that assumes the Netrias
`DataChordDeployer` role:

```bash
AWS_PROFILE=netrias-data-chord-deployer \
  NETRIAS_API_KEY='replace-with-key' \
  just deploy netrias staging
```

`NETRIAS_API_KEY` is only required when the stage secret does not exist
yet, or when you want the deploy script to update it.

The deploy script is idempotent. It creates or updates:

1. The S3 OpenTofu state bucket.

2. The Secrets Manager secret for `NETRIAS_API_KEY`.

3. The OpenTofu-managed AWS infrastructure.

4. The Cognito user pool and app client.

5. The CodeBuild image build.

6. The OpenTofu-managed ECS task definition and rollout.

`just deploy <target> <stage>` and `just deploy-app <target> <stage>` both build
the current pushed Git commit, push only the short commit SHA image tag, apply
OpenTofu with `image_tag=<sha>`, and then watch the ECS rollout.

Use an app-only deploy for normal code changes:

```bash
AWS_PROFILE=strides just deploy-app strides staging
```

Use an infra-only deploy when the image should stay the same:

```bash
AWS_PROFILE=strides just deploy-infra strides staging
```

Infra-only deploys reuse the currently deployed ECS image tag. If there is no
current ECS service yet, set `DATA_CHORD_IMAGE_TAG` to an existing immutable ECR
tag:

```bash
AWS_PROFILE=strides DATA_CHORD_IMAGE_TAG=abc123def456 \
  just deploy-infra strides staging
```

## Alerting

Each target and stage creates its own CloudWatch alarms, EventBridge failure
rules, and SNS topic. Alert names and messages include the stage name, for
example `STAGING` or `PROD`, so staging failures are easy to separate from
production failures.

Subscribe email recipients by setting `alert_email_addresses` in the matching
stage tfvars file or with a deploy-time variable. AWS sends each address a
confirmation email before it receives alerts.

Production is the only stage subscribed by default. Staging still has alarms for
manual inspection, but it does not send email unless a recipient is explicitly
added to the target's `staging.tfvars`.

Only outage and user-visible failure alarms publish to email. Warning alarms
such as high CPU, high memory, high response time, and app ERROR logs are kept
in CloudWatch without email actions. CloudWatch OK transitions also do not send
email, so deploys do not generate a recovery-message burst.

## Optional VPN Auth Bypass

The HTTPS listener normally requires Cognito login. A deployed stage can also
trust specific VPN egress CIDRs by storing a JSON array in Secrets Manager:

```text
data-chord/<stage>/auth-bypass-cidrs
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
AWS_PROFILE=strides DATA_CHORD_DEPLOY_ALLOW_DIRTY=1 \
  just deploy strides staging
```

That still deploys the current committed `HEAD`. It does not upload or build
uncommitted files.

The deployed image tag is the first 12 characters of the deployed commit SHA.
The deploy path does not push or deploy `latest`.

## Manual Prerequisite: CodeBuild GitHub Access

CodeBuild needs read access to `netrias/data_chord` before it can clone the repo
and build the Docker image. Source authorization is a one-time setup in each AWS
account and region; it is not stored in OpenTofu, Secrets Manager, or this repo.

Verify source authorization with the profile for the selected target:

```bash
AWS_PROFILE=strides aws codebuild list-source-credentials \
  --region us-east-2 \
  --output table
```

The Netrias account currently uses a GitHub CodeConnections credential. Before
the first deployment, confirm that connection can clone `netrias/data_chord`.

If an account does not have a usable connection, an account administrator can
import a GitHub token with the smallest scope that can clone this private
repository:

```bash
AWS_PROFILE=strides aws codebuild import-source-credentials \
  --region us-east-2 \
  --server-type GITHUB \
  --auth-type PERSONAL_ACCESS_TOKEN \
  --should-overwrite \
  --token "$GITHUB_TOKEN"
```

When the GitHub token expires, create a replacement token and rerun the import
command. `--should-overwrite` replaces the existing CodeBuild source credential
for GitHub in this AWS account and region.

## Troubleshooting

Plan without deploying:

```bash
AWS_PROFILE=strides just deploy-plan strides staging
```

Inspect current deployment status:

```bash
AWS_PROFILE=strides just deploy-status strides staging
```

Show recent app and build logs:

```bash
AWS_PROFILE=strides just deploy-logs strides staging
```

Build and push the image without applying OpenTofu or rolling ECS:

```bash
AWS_PROFILE=strides just deploy-build strides staging
```

Redeploy a code change through OpenTofu:

```bash
AWS_PROFILE=strides just deploy-app strides staging
```

## Giving Users Access

Cognito is configured so only admins can create users. Invite a user with:

```bash
AWS_PROFILE=strides just invite-user strides staging user@example.com
```

For another target or stage, change both explicit arguments.

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
AWS_PROFILE=strides just resend-user-invite strides staging user@example.com
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
