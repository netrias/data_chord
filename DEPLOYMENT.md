# Deploy Data Chord

This guide creates one Data Chord application environment. The AWS account
foundation must exist before you start.

## Prerequisites

You need:

- Git;
- the GitHub CLI;
- Python 3.13 or later;
- [uv](https://docs.astral.sh/uv/);
- [just](https://github.com/casey/just);
- the AWS CLI;
- OpenTofu 1.10 or later;
- an AWS profile that can assume the foundation deployer role; and
- local Git access to `netrias/agentic_harmonization`; and
- a GitHub App that can read that repository during the image build.

The build gets the application source through AWS CodeConnections. A GitHub
App supplies a short-lived token for the private Python dependency.

Clone the repository and install the locked Python dependencies:

```bash
git clone https://github.com/netrias/data_chord.git
cd data_chord
gh auth status
gh auth setup-git
uv sync --frozen
```

If `gh auth status` fails, run `gh auth login` first. This is the human Git
credential for the local install. CodeBuild uses the separate GitHub App.

Normal installs use the committed lock file. Do not resolve new dependency
versions during a deployment.

## 1. Create the AWS foundation

Follow the
[DataChord foundation deployment guide](https://github.com/netrias/datachord-infrastructure/blob/main/DEPLOYMENT.md).
It creates the state bucket, deployment role, and permission boundaries.

The foundation command writes this handoff file:

```text
.plans/<target>-foundation-handoff.json
```

Keep the handoff file. It contains resource identifiers, but it contains no
secret values.

## 2. Create the environment file

Each application environment has one file:

```text
environments/<target>/<stage>.json
```

`<target>` is the foundation target. `<stage>` must be `dev`, `qa`, `staging`,
or `prod`.

Copy these values from the foundation handoff:

| Foundation handoff | Application environment |
| --- | --- |
| `account_id` | `account_id` |
| `region` | `region` |
| `state_bucket_name` | `state_bucket_name` |
| `deployer_role_arn` | `deployer_role_arn` |
| `application_role_boundary_arn` | `application_role_boundary_arn` |
| `application_role_path` | `application_role_path` |

Use `<target>` as the directory name. Confirm that `state_key_prefix` is
`datachord/<target>/`.

Do not copy `schema_version`, `partition`, `protected_state_bucket_name`,
`state_key_prefix`, `deployer_boundary_arn`, or
`assume_role_policy_statement` into the application file. The deployment
derives or checks those values.

Add these application values:

- `domain_name`: the hosted zone plus one lowercase DNS label;
- `hosted_zone_name`: the public Route 53 zone that contains the host name;
- `application_repository_url`: a credential-free HTTPS Git URL that ends in
  `.git` and has no query or fragment; and
- `github_app_secret_name`: the name of the build credential in Secrets
  Manager.

Example:

```json
{
  "account_id": "123456789012",
  "region": "us-east-2",
  "state_bucket_name": "example-datachord-state-123456789012-us-east-2",
  "deployer_role_arn": "arn:aws:iam::123456789012:role/foundation/example-datachord-deployer",
  "application_role_boundary_arn": "arn:aws:iam::123456789012:policy/example-datachord-application-role-boundary",
  "application_role_path": "/application/",
  "domain_name": "data-chord-staging.apps.example.org",
  "hosted_zone_name": "apps.example.org",
  "application_repository_url": "https://github.com/netrias/data_chord.git",
  "github_app_secret_name": "data-chord/build/github-app"
}
```

The file accepts only these ten fields. The deployment stores OpenTofu state
at:

```text
datachord/<target>/<stage>/tofu.tfstate
```

You can validate the file without AWS access:

```bash
python3 infra/scripts/environment.py validate environments/<target>/<stage>.json <target> <stage>
```

## 3. Prepare external AWS resources

Complete these tasks before the first plan:

- Create the public Route 53 hosted zone from `hosted_zone_name`. Complete its
  DNS delegation.
- Follow the AWS
  [GitHub App connection procedure](https://docs.aws.amazon.com/codebuild/latest/userguide/connections-github-app.html)
  to create and authorize a CodeConnections connection in the target account
  and Region. Register it as CodeBuild's default GitHub source credential.
- Create the Secrets Manager secret named by `github_app_secret_name`. Its JSON
  value must contain `app_id`, `installation_id`, and `private_key` for a
  read-only GitHub App that can read `netrias/agentic_harmonization`. Follow the
  AWS [secret creation procedure](https://docs.aws.amazon.com/secretsmanager/latest/userguide/create_secret.html)
  and use the **Other type of secret** option.
- Confirm that the account's default Bedrock Mantle project can use GPT-5.6
  Luna and has enough inference quota.
- Confirm that the selected AWS profile can assume the exact
  `deployer_role_arn`.

Do not put credentials or secret values in the environment file.

## 4. Plan the deployment

Commit the environment file and push the exact deployment commit. The command
rejects tracked changes, untracked files under `infra/`, and commits that are
not the tip of a remote ref in `application_repository_url`.

```bash
git add environments/<target>/<stage>.json
git commit -m "Add <target> <stage> environment"
git push --set-upstream origin HEAD
```

Run:

```bash
AWS_PROFILE=<source-profile> just plan <target> <stage>
```

`plan` validates the environment and foundation, reads the current state, and
shows a read-only resource forecast. It does not change application resources
or build an image. It saves this receipt:

```text
.plans/<target>-<stage>.json
```

Review every action. Do not continue if the forecast contains an unexpected
resource, deletion, or replacement.

## 5. Deploy

Run the deployment from the same commit and environment file:

```bash
AWS_PROFILE=<source-profile> just deploy <target> <stage>
```

The command has no confirmation prompt. It:

- checks the saved receipt and current state;
- applies the required build resources;
- tests and builds the exact Git commit in CodeBuild;
- applies the application plan; and
- waits for a stable ECS service and healthy load-balancer targets.

The image tag is the full Git commit. If the deployment changes AWS resources
and then stops, run `plan` again before a retry.

## 6. Load reference data

Deployment creates an empty reference-data table. The application is not ready
for a workflow until an approved canonical reference-data file is loaded.

First, calculate the approved file's SHA-256 digest.

On macOS:

```bash
shasum -a 256 <approved-reference-data.json>
```

On Linux:

```bash
sha256sum <approved-reference-data.json>
```

Then use an AWS profile that can write the environment's reference-data table:

```bash
AWS_PROFILE=<data-loader-profile> uv run python scripts/reference_data.py sync \
  --input <approved-reference-data.json> \
  --expected-sha256 <sha256> \
  --table data-chord-<stage>-reference-data \
  --region <region>
```

The command checks the digest, loads all model versions, reads them back, and
verifies the published model count. Reference data is not part of the
environment JSON or the OpenTofu deployment.

An authorized operator can create a canonical file from the legacy service.
Load `NETRIAS_API_KEY` from the approved secret source without putting its
value in shell history. Then run:

```bash
uv run python scripts/reference_data.py export \
  --environment <staging-or-prod> \
  --output <approved-reference-data.json>
```

Treat the export as controlled data. Review and approve it before sync.

## 7. Invite a user and test the workflow

Invite one Cognito user:

```bash
AWS_PROFILE=<source-profile> infra/scripts/invite-cognito-user.sh \
  <target> <stage> <user-email>
```

The user receives a temporary password by email. Open the application URL
printed by `deploy`, then verify one complete workflow:

- sign in;
- upload a small supported file;
- select a model and review column mappings;
- run harmonization and review the results; and
- download and open the final ZIP file.

ECS and load-balancer health checks prove only that the web service responds.
The workflow test also checks reference-data access, Bedrock access, durable
storage, and download generation.

## Current limit

The foundation supports commercial AWS and AWS GovCloud. The current
application deployment does not support GovCloud because its Application Load
Balancer uses Cognito authentication.
