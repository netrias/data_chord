# Data Chord deployment

Data Chord owns its application infrastructure. The OpenTofu root creates the
VPC, public subnets, load balancer, Cognito login, ECS service, application IAM
roles, storage, CodeBuild project, logs, and alerts.

The foundation owns the account-level state bucket, deployment role,
application-role boundary, IAM path, and optional public DNS zone. It publishes
these values in SSM parameter `/datachord/foundation/deployment-contract`.
Data Chord does not read files from the foundation repository.

## Operator commands

The AWS profile selects the account and region.

```bash
just plan <target> <stage> <profile>
just deploy <target> <stage> <profile>
just status <target> <stage> <profile>
```

`plan` validates the foundation contract before state or secret access. It
uses native S3 locking configuration, but disables locking for the read-only
plan. It saves and shows the final plan under `build/plans`. It does not create
resources, start CodeBuild, or apply.

`deploy` creates and shows a saved prerequisite plan for the public GitHub
CodeBuild path. It applies that exact plan, builds the pushed Git commit, then
creates and shows a fresh full plan. It applies that exact full plan and waits
for a stable ECS service. Immediately before each apply, it re-reads the SSM
contract and rejects a changed canonical contract digest. It does not use a
direct full `apply -auto-approve`.

`status` reads the ECS service status. It does not initialize OpenTofu or read
state or secrets.

State uses the native OpenTofu S3 lock file and this service-owned key:

```text
datachord/<target>/<stage>/tofu.tfstate
```

The target and stage are separate values. Stages are `dev`, `qa`, `staging`,
and `prod`.

## New customer sequence

1. Apply the customer target in `data_chord_infra`. Confirm that it publishes
   deployment contract schema version 1.

2. Configure an AWS profile for the customer account. Set its region to the
   region in the foundation contract. The operator can start with a configured
   operator identity. The command assumes
   `role/foundation/datachord-deployer` when needed.

3. Add `infra/env/<target>/<stage>.tfvars`. Put application choices in this
   file. Do not copy account IDs, regions, role ARNs, state buckets, VPC IDs, or
   subnets into the repository.

   A managed-DNS staging file can be as small as:

   ```hcl
   domain_label       = "data-chord-staging"
   netrias_environment = "staging"
   ```

4. Store the Netrias API key in Secrets Manager as
   `data-chord/<stage>/netrias-api-key`. If trusted networks must bypass
   Cognito, store a JSON array of CIDRs as
   `data-chord/<stage>/auth-bypass-cidrs`. Do not put secret values in tfvars.

5. Push the clean Data Chord commit to the public GitHub repository.

6. Run `just plan <target> <stage> <profile>` and review the complete saved
   plan.

7. Run `just deploy <target> <stage> <profile>`. Then run
   `just status <target> <stage> <profile>`.

If the foundation contract supplies `application_dns_zone_name`, Data Chord
creates its application record and certificate in that public zone. If the
field is null, the stage file must supply either a managed hosted-zone name or
an external certificate and domain.

## Existing BDF state

BDF staging and production existed before the target-aware state key. Follow
the exact [foundation state-migration procedure](https://github.com/netrias/datachord-infrastructure/blob/main/docs/state-operations.md#move-existing-service-state)
to copy each state object from `data-chord/<stage>/tofu.tfstate` to
`datachord/bdf/<stage>/tofu.tfstate` before this command can plan or deploy.
The command checks the destination object before backend initialization.

`migration-handoff.tf` is the current one-time application-role handoff for
the existing BDF stacks. A configured operator must complete that saved-plan
handoff before the normal foundation deployment role can manage a stage. Do
not remove the handoff until both existing stages have completed it and their
state no longer contains the legacy role addresses.

Follow the exact saved-plan procedure in
[BDF_MIGRATION.md](BDF_MIGRATION.md). It starts from the configured operator,
assumes the deployment role once, backs up state, checks both the displayed-plan
digest and foundation-contract digest, and applies that exact plan. The one-time
plan disables refresh because the deployment role cannot read the old root-path
IAM roles. It proves that all ten migration addresses use the `forget` action
before apply. This includes the two retained legacy endpoint resources, which
it does not delete or detach. These resources are a temporary BDF exception. Do
not clean them up until the actual external client owner takes them over or
proves that it is independent.

## Environment limits

The plan and deploy source check reads the live public
`netrias/data_chord` GitHub repository. CodeBuild reads the same repository
without a CodeConnection or GitHub credential. These paths do not work in a
disconnected environment. The build also needs public package sources.

The contract and IAM ARNs support the `aws` and `aws-us-gov` partitions.
GovCloud deployment is not yet proved. Confirm regional service support for
Cognito, ALB authentication, Route 53, CodeBuild, and each configured upstream
service before a GovCloud deploy.
