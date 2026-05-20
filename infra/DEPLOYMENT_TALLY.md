# Deployment Tally

This tracks what is still between this branch and a real hosted deployment.

## Current Target

1. AWS profile: `strides`

2. AWS account: `084828580051`

3. Region: `us-east-2`

4. Hosted zone found: `netriasbdf.cloud`

5. Current hostname candidate: `netrias-data-chord.netriasbdf.cloud`

## Tally

1. AWS account and region

   Status: mostly resolved.

   Current decision: deploy to `AWS_PROFILE=strides` in `us-east-2`.

   Remaining work: run `tofu plan` with real variables.

2. Domain and certificate

   Status: mostly resolved in OpenTofu.

   What is wrong: ALB Cognito authentication needs an HTTPS listener, and an HTTPS listener needs a certificate that browsers trust. The generated ALB DNS name is under `amazonaws.com`, so we cannot get a normal public ACM certificate for it.

   Current implementation: `domain_label = "netrias-data-chord"` uses `netrias-data-chord.netriasbdf.cloud`. OpenTofu requests an ACM certificate in `us-east-2`, creates the DNS validation record, and creates an alias record to the ALB. If `domain_label` is left empty, it falls back to a generated hostname under `netriasbdf.cloud`.

   Remaining work: confirm this hostname is acceptable. Read-only AWS checks found no existing Route 53 record or ACM certificate for it.

3. Netrias API key secret

   Status: scripted.

   What is needed: a Secrets Manager secret in `us-east-2` containing the plain `NETRIAS_API_KEY` value.

   Current implementation: `just deploy staging` and `just deploy prod` use separate Secrets Manager names. If `NETRIAS_API_KEY` is set, the script creates or updates the secret for the selected environment. If the secret already exists and no local value is set, deploy reuses it.

4. First image bootstrap

   Status: scripted.

   Current implementation: `just deploy <environment>` applies infrastructure, starts CodeBuild, watches the build, and watches the ECS rollout. The first run can still show a temporary unhealthy ECS service before the first image exists.

5. CodeBuild access to GitHub

   Status: configured.

   What is needed: if the GitHub repo is private, CodeBuild needs read access. The simplest path is to import GitHub source credentials into CodeBuild for the Strides account and `us-east-2`.

   Current state: CodeBuild has a classic GitHub personal access token credential imported at `arn:aws:codebuild:us-east-2:084828580051:token/github`.

   Remaining work: verify CodeBuild can clone the repo during `just deploy staging`.

   Follow-up cleanup: after the classic token works, delete the pending fine-grained token named `CodeBuild data_chord source` in GitHub.

6. OpenTofu state

   Status: scripted.

   Current implementation: checked-in backend config points OpenTofu state at S3 with encryption and native S3 locking. `infra/scripts/bootstrap-state.sh` creates or updates the bucket idempotently.

   Important split: project config lives in `infra/env/*.tfvars`; generated bootstrap values live in `infra/generated/`; actual state lives in S3.

7. Cognito users

   Status: acceptable manual step.

   Remaining work: after apply, invite users with `aws cognito-idp admin-create-user`.

8. Harmonization job behavior

   Status: likely okay.

   Current understanding: the app submits the harmonization request and polls for completion. No async job refactor is planned unless real deploy testing shows ALB/client timeout issues.

9. First AWS apply

   Status: not done.

   Remaining work: run `AWS_PROFILE=strides just deploy staging`, fix any account-specific issues, then rerun the same command until green.
