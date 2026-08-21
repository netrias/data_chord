# Data Chord AWS infrastructure

This directory owns two application infrastructure roots. The separate
[`datachord-infrastructure`](https://github.com/netrias/datachord-infrastructure)
repository owns the AWS account foundation.

See [../DEPLOYMENT.md](../DEPLOYMENT.md) for the operator procedure.

## Ownership

The account foundation provides:

- the versioned OpenTofu state bucket;
- one `/foundation/<name>-deployer` role;
- the deployer permission boundary;
- the application-role permission boundary; and
- the `/application/` IAM path.

The full root in `infra/` provides:

- the application VPC, load balancer, DNS record, certificate, and Cognito;
- ECS, ECR, CodeBuild, CloudWatch logs, and their IAM roles;
- the application workflow S3 bucket; and
- three DynamoDB tables for reference data, harmonization results, and CDE
  recommendations.

The foundation does not deploy application resources. The application
deployment does not create or change the foundation.

The `infra/customer-platform/` root creates only the shared data-plane module:

- one workflow S3 bucket;
- one S3 public-access block; and
- three DynamoDB tables.

It creates no IAM role, network, compute, registry, TLS, authentication, or
logging resource. It reads the foundation schema-v2 handoff directly and uses
`datachord/<target>/<stage>/customer-platform/tofu.tfstate`. Its outputs give
the customer the runtime environment and policies for customer-owned roles.

## Environment contract

Each deployment reads one strict file:

```text
environments/<target>/<stage>.json
```

The target and stage come from the path. The stage must be `dev`, `qa`,
`staging`, or `prod`. The state key is always:

```text
datachord/<target>/<stage>/tofu.tfstate
```

The environment file contains application inputs and selected foundation
outputs. It does not contain credentials, reference data, a state key, or a
CodeConnections ARN. `infra/scripts/environment.py` rejects missing and extra
fields.

The deployer role and application boundary must use the same foundation name
prefix. The deployment derives the deployer boundary from that prefix and
checks the live foundation before it plans.

## Mutation safety

The `plan` command validates the source commit, environment, AWS identity,
foundation, and state. It displays a read-only resource forecast and saves
`.plans/<target>-<stage>.json`. It does not apply resources or build an image.

The receipt binds the forecast to the environment file, Git commit, AWS
account, Region, state location, and state lineage and serial.

The `deploy` command has no confirmation prompt. It rejects a changed receipt
or state, then:

- marks the receipt in progress before the first AWS change;
- creates and checks a saved prerequisite plan;
- builds or reuses an image tagged with the full Git commit;
- creates and checks a saved application plan; and
- waits for a stable ECS service and healthy load-balancer targets.

Each internal plan must stay inside the reviewed forecast. An unexpected
resource, deletion, or replacement stops the deployment before apply. The only
allowed replacement is the normal ECS task-definition revision for a new
image.

If a deployment stops after its first AWS change, the receipt remains in
progress. Inspect the failure and run `plan` again before a retry.

## Data and users

Deployment creates an empty reference-data table. Loading reference data is a
separate controlled operation. It is not an OpenTofu input.

The deployment also creates the Cognito user pool. User invitations are a
separate operation.

See [../DEPLOYMENT.md](../DEPLOYMENT.md) for the supported reference-data and
user-invitation commands.

## Current limits

- The application deployment rejects AWS GovCloud because ALB Cognito
  authentication is unavailable there. The account foundation can still be
  created in GovCloud.
- A deployment requests a four-hour deployer-role session. Before apply, it
  checks that at least three hours remain.
- A CI job that starts with the deployer role must set
  `AWS_CREDENTIAL_EXPIRATION` to the ISO 8601 STS expiration time.
- The environment owner controls the AWS profile, DNS delegation, GitHub App,
  CodeConnections source credential, Bedrock access, and any controlled CI
  runner.
