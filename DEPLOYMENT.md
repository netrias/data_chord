# Deploy Data Chord

This guide deploys one Data Chord environment. It uses one complete example so
that you can see each file name and command.

The example uses:

| Name | Example value |
| --- | --- |
| Foundation target | `example` |
| Application stage | `staging` |
| AWS Region | `us-east-2` |
| Operator AWS profile | `example-admin` |
| Application URL | `data-chord-staging.apps.example.org` |

Replace these values with the values for your environment.

## 1. Prepare AWS

Create the AWS foundation first. Follow the
[foundation deployment guide](https://github.com/netrias/datachord-infrastructure/blob/main/DEPLOYMENT.md).
Keep the handoff file in the foundation repository:

```text
.plans/example-foundation-handoff.json
```

Prepare these account resources before the first application plan:

- A public Route 53 hosted zone with working DNS delegation. The example uses
  `apps.example.org`.
- A CodeConnections GitHub connection in `us-east-2`. Give the connection read
  access to `netrias/data_chord`, then register it as the default GitHub source
  credential for CodeBuild. Follow the AWS
  [GitHub App connection procedure](https://docs.aws.amazon.com/codebuild/latest/userguide/connections-github-app.html).
- A Secrets Manager secret named `data-chord/build/github-app`. Its JSON value
  must contain `app_id`, `installation_id`, and `private_key`. The GitHub App
  must have read access to the private `netrias/agentic_harmonization`
  repository. The build also downloads the public `netrias/netrias_client`
  repository. Follow the AWS
  [secret creation procedure](https://docs.aws.amazon.com/secretsmanager/latest/userguide/create_secret.html)
  and select **Other type of secret**.
- Access to GPT-5.6 Luna in the account's default Bedrock Mantle project, with
  enough inference quota for Data Chord.
- An AWS profile that can assume the `deployer_role_arn` in the foundation
  handoff.

The CodeConnections credential gets the Data Chord source. The secret lets the
image build install the private dependency. Do not put a credential or secret
value in an environment file.

## 2. Install the deployment tools

Install Git, the GitHub CLI, Python 3.13 or later,
[uv](https://docs.astral.sh/uv/), [just](https://github.com/casey/just), the AWS
CLI, and OpenTofu 1.10 or later.

Your GitHub account needs read access to Data Chord and
`netrias/agentic_harmonization`. The install also downloads the public
`netrias/netrias_client` repository. Then run:

```bash
git clone https://github.com/netrias/data_chord.git
cd data_chord
gh auth status
gh auth setup-git
uv sync --frozen
```

If `gh auth status` fails, run `gh auth login` and try again. Keep the committed
lock file during deployment. Do not update dependency versions.

## 3. Add the environment file

Create this file for the example:

```text
environments/example/staging.json
```

Copy six values from the foundation repository's
`.plans/example-foundation-handoff.json` file:

| Handoff field | Environment field |
| --- | --- |
| `account_id` | `account_id` |
| `region` | `region` |
| `state_bucket_name` | `state_bucket_name` |
| `deployer_role_arn` | `deployer_role_arn` |
| `application_role_boundary_arn` | `application_role_boundary_arn` |
| `application_role_path` | `application_role_path` |

Add the domain, hosted zone, repository, and secret name. The complete file
looks like this:

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

The file accepts only these ten fields. The domain must add one lowercase DNS
label to the hosted zone. The repository URL must be an HTTPS URL that ends in
`.git` and contains no credential.

Validate the file without AWS access:

```bash
python3 infra/scripts/environment.py validate environments/example/staging.json example staging
```

The deployment stores OpenTofu state at
`datachord/example/staging/tofu.tfstate`.

## 4. Commit and plan

The deployment builds the exact Git commit that you push. Use a clean worktree.
Commit the environment file first:

```bash
git add environments/example/staging.json
git commit -m "Add example staging environment"
git push --set-upstream origin HEAD
```

Create the plan:

```bash
AWS_PROFILE=example-admin just plan example staging
```

This command validates the environment and foundation. It then shows the AWS
resource changes and saves `.plans/example-staging.json`. It does not build an
image or change application resources.

Read every change. Stop if the plan contains an unexpected resource, deletion,
or replacement.

## 5. Deploy

Deploy the same commit and environment file:

```bash
AWS_PROFILE=example-admin just deploy example staging
```

There is no confirmation prompt. The command applies the build resources, tests
and builds the exact commit in CodeBuild, applies the application plan, and
waits for healthy ECS and load-balancer targets. The image tag is the full Git
commit.

If the command changes AWS and then stops, run
`AWS_PROFILE=example-admin just plan example staging` again before you retry
the deployment.

## 6. Load reference data

The deployment creates an empty reference-data table. Data Chord cannot run a
workflow until you load an approved canonical reference-data file.

Calculate the file's SHA-256 digest.

On macOS:

```bash
shasum -a 256 approved-reference-data.json
```

On Linux:

```bash
sha256sum approved-reference-data.json
```

Use a profile that can write to the staging reference-data table:

```bash
AWS_PROFILE=example-data-loader uv run python scripts/reference_data.py sync \
  --input approved-reference-data.json \
  --expected-sha256 <sha256-from-the-command-above> \
  --table data-chord-staging-reference-data \
  --region us-east-2
```

The command checks the digest, loads the data, reads it back, and checks the
published model count.

If an authorized operator must create the canonical file from the legacy
service, load `NETRIAS_API_KEY` from the approved secret source. Do not type its
value in the command. Then run:

```bash
uv run python scripts/reference_data.py export \
  --environment staging \
  --output approved-reference-data.json
```

Review and approve the exported file before you sync it.

## 7. Invite a user and test one workflow

After you have approval to add a user, invite one Cognito user:

```bash
AWS_PROFILE=example-admin infra/scripts/invite-cognito-user.sh \
  example staging user@example.org
```

Open the application URL printed by the deployment. Sign in with the temporary
password, then:

1. Upload a small CSV, TSV, or XLSX file.
2. Select a model and review the column mappings.
3. Run harmonization and review the results.
4. Download and open the final ZIP file.

This workflow checks more than the web health check. It checks reference data,
Bedrock, durable storage, and download generation.

## Use another target or stage

Use the foundation target as the environment directory name. The stage must be
`dev`, `qa`, `staging`, or `prod`. Replace `example`, `staging`, the profile,
Region, domain, ARNs, bucket, and table name in the examples above.

The foundation supports commercial AWS and AWS GovCloud. The current Data Chord
application deployment supports commercial AWS only because its Application
Load Balancer uses Cognito authentication.
