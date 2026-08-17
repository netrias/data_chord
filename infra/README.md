# Data Chord AWS deployment

This repository owns the Data Chord application and its application resources.
The foundation repository owns only the shared deployment access for one AWS
account.

## Ownership

The foundation owner provides:

- the versioned OpenTofu state bucket;
- `/foundation/datachord-deployer`;
- the deployer permission boundary;
- the application-role permission boundary; and
- the `/application/` IAM path.

This repository provides:

- the application VPC, load balancer, DNS record, certificate, and Cognito;
- ECS, ECR, CodeBuild, and their logs and IAM roles;
- the application workflow S3 bucket; and
- the DynamoDB reference-data table.

The foundation does not deploy application resources. Data Chord does not
create or change the foundation.

## One environment file

Each deployment has one strict file:

```text
environments/<target>/<stage>.json
```

Example:

```json
{
  "account_id": "945365518758",
  "region": "us-east-2",
  "state_bucket_name": "netrias-datachord-state-945365518758-us-east-2",
  "deployer_role_arn": "arn:aws:iam::945365518758:role/foundation/datachord-deployer",
  "application_role_boundary_arn": "arn:aws:iam::945365518758:policy/datachord-application-role-boundary",
  "application_role_path": "/application/",
  "domain_name": "data-chord-staging.apps.netrias.com",
  "hosted_zone_name": "apps.netrias.com",
  "application_repository_url": "https://github.com/netrias/data_chord.git",
  "github_app_secret_name": "data-chord/build/github-app"
}
```

The file accepts no other fields. The target and stage come from its path. The
AWS partition comes from the region. The state key is always:

```text
datachord/<target>/<stage>/tofu.tfstate
```

The role names, role path, and permission-boundary name must match the
foundation conventions. The command checks the live foundation before it
plans.

## Environment-owner setup

Before the first plan, the environment owner must:

1. Apply the account foundation from `netrias/datachord-infrastructure`.

2. Create the public Route 53 hosted zone named in the environment file and
   complete external DNS delegation.

3. Create the Secrets Manager secret named by `github_app_secret_name`. Its
   JSON value must contain `app_id`, `installation_id`, and `private_key` for a
   read-only GitHub App that can read `netrias/data_chord`.

4. Give the selected AWS profile permission to assume the exact
   `deployer_role_arn`.

5. Add the environment JSON to the deployment branch.

6. Push the exact deployment commit. The commands reject local-only commits
   and a dirty working tree.

## Two commands

Run:

```bash
AWS_PROFILE=default just plan netrias staging
AWS_PROFILE=default just deploy netrias staging
```

`plan`:

- validates the environment file;
- assumes and verifies the foundation deployer role;
- opens the exact state bucket and derived state key;
- reads the current state lineage and serial;
- displays a read-only OpenTofu forecast; and
- saves `.plans/<target>-<stage>.json`.

The receipt binds the forecast to the environment file, Git commit, AWS
account, region, state location, and state lineage and serial. `plan` does not
apply resources or build an image.

The forecast is a resource and action boundary. It is not the final OpenTofu
plan. A fresh deployment must create build prerequisites before it can build
the image.

`deploy` has no prompt and reads no stdin. It:

1. repeats every validation and rejects a changed receipt or state;
2. marks the receipt in progress before the first AWS resource change;
3. displays, checks, and applies one saved prerequisite plan;
4. builds or reuses the image tagged with the full 40-character Git commit;
5. displays, checks, and applies one saved full application plan; and
6. waits for a stable ECS service and healthy load-balancer targets.

Each internal plan must remain inside the approved forecast. Prerequisite work
has a smaller allow-list. A delete, replacement, or extra resource stops the
deployment before apply.

Deployment creates the empty DynamoDB reference-data table. Loading or changing
the table data is a separate operation. `plan` and `deploy` do not accept a data
export and do not write table data.

If the prerequisite plan first enables versioning on the workflow bucket, AWS
can need 15 minutes before the bucket is safe for application writes. Image
work runs during this period. If time remains, `deploy` prints the exact wait
and finishes it before the application starts.

If a deployment stops after its first resource change, its receipt stays in
progress. Inspect the failure and run `plan` again before a retry.

## Current limits

- BDF is rejected before AWS or OpenTofu runs. Its legacy state needs a manual
  handoff before it can use this process.
- The current application authentication uses an ALB Cognito action. AWS
  GovCloud does not provide this action. A GovCloud deployment branch must
  replace that application authentication design before it can plan. The
  foundation repository already supports the `aws-us-gov` ARN partition.
- The environment owner controls the AWS profile, DNS delegation, GitHub App,
  and any controlled CI runner. The two commands work the same on a workstation
  or in CI.
- When it starts from another identity, `deploy` requests a four-hour
  deployer-role session. The deployer role must allow this session length.
  A CI job that starts with the deployer role must set
  `AWS_CREDENTIAL_EXPIRATION` to the ISO 8601 expiry from STS. `deploy` stops
  before apply unless it can prove that at least three hours remain. This
  avoids the one-hour role-chaining limit.
