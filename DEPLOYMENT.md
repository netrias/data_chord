# Deploy Data Chord

Data Chord has three deployment offers:

| Offer | Data profile | Data Chord creates | Customer creates |
| --- | --- | --- | --- |
| Portable | `portable` | One container image | Compute, one `/data` volume, network, TLS, and access control |
| Customer platform | `hosted` | Workflow S3 storage and three DynamoDB tables | Registry, compute, network, TLS, authentication, logs, and workload roles |
| Full AWS | `hosted` | The complete AWS application stack | The account foundation and external prerequisites |

Portable does not use the foundation repository. Customer platform and full
AWS use the state bucket and deployer role from the foundation handoff. Do not
use customer platform and full AWS for the same target and stage.

## Portable container

Use this offer when the customer owns the container platform and wants to keep
reference data and workflow files on one persistent volume.

The portable profile needs one Docker image, one persistent `/data` volume,
and AWS credentials that can call the required Bedrock models. The customer
owns TLS, authentication, and network access to the container.

Run exactly one container replica with one Uvicorn worker. Portable workflow
files and locks do not support concurrent application processes.

### Get the image

Use an image for an exact, reviewed Git commit. The application provider can
supply this image. To build it from source instead, check out the reviewed
commit and run:

```bash
git checkout <full-git-commit>
GITHUB_TOKEN=$(gh auth token)
export GITHUB_TOKEN
docker buildx build \
  --secret id=github_token,env=GITHUB_TOKEN \
  --tag data-chord:<full-git-commit> \
  --load .
```

The GitHub account must have read access to the private
`netrias/agentic_harmonization` repository. The token is a build secret and is
not stored in an image layer.

### Load reference data

Load an approved reference-data export into the volume before the first run:

```bash
# macOS
shasum -a 256 approved-reference-data.json

# Linux
sha256sum approved-reference-data.json
```

Compare the result with the approved digest. Then copy that digest into the
load command:

```bash
docker run --rm \
  --mount type=volume,src=data-chord,dst=/data \
  --mount type=bind,src="$(pwd)/approved-reference-data.json",dst=/import/reference.json,readonly \
  data-chord:<full-git-commit> \
  python -m scripts.reference_data load-sqlite \
    --input /import/reference.json \
    --expected-sha256 <approved-sha256> \
    --database /data/standards.sqlite
```

### Run the application

On AWS, use a workload role. Run the application with the same volume:

```bash
docker run --rm \
  --mount type=volume,src=data-chord,dst=/data \
  --env DATA_CHORD_PROFILE=portable \
  --env DATA_CHORD_WORKFLOW_STORAGE_LIMIT_GB=10 \
  --env AWS_REGION=us-east-2 \
  --publish 8000:8000 \
  data-chord:<full-git-commit>
```

The AWS SDK uses its standard credential chain. For local Docker testing, use
short-lived credentials from the host:

```bash
docker run --rm \
  --mount type=volume,src=data-chord,dst=/data \
  --env DATA_CHORD_PROFILE=portable \
  --env DATA_CHORD_WORKFLOW_STORAGE_LIMIT_GB=10 \
  --env AWS_REGION=us-east-2 \
  --env AWS_ACCESS_KEY_ID \
  --env AWS_SECRET_ACCESS_KEY \
  --env AWS_SESSION_TOKEN \
  --publish 8000:8000 \
  data-chord:<full-git-commit>
```

Do not put credentials in the image.

### Use local harmonization models

Local harmonization is a separate method from agentic harmonization. The image
must be built with `--build-arg DATA_CHORD_INCLUDE_LOCAL_INFERENCE=true`. This
keeps the large Torch and NVIDIA libraries out of images that use only agentic
harmonization. Use the same checked build command above with this one additional
argument.

The local-model image
contains `/app/config/local_models.json`. Edit `config/local_models.json` in the
repository and build a new image when model assignments or inference settings
change. Put only the large model directories on the mounted `/models` volume.
The model path in the JSON file is relative to `/models` and is also its identity:

```json
{
  "models": [
    {
      "path": "gpt2-cell-type-v1",
      "cdes": ["cell_type"],
      "batch_size": 8,
      "strong_confidence": 0.9
    },
    {
      "path": "biobert-disease-v1",
      "cdes": ["human_diseases", "medical_history"],
      "batch_size": 16,
      "strong_confidence": 0.85
    }
  ]
}
```

Use the exact CDE keys from the standard. One CDE can use only one model. One
model can own many CDEs. The application finds the model type from its Hugging
Face configuration. GPT-2 and BERT sequence-classification exports are
supported. Their output labels must be exact permissible values. A model can
also contain the `NO_MATCH` label. Batch size and the threshold for a strong
match can differ for each model.

Each model receives one string in this exact form:

```text
CDE: <CDE key>
Source value: <uploaded value>
```

The model training input must use the same form. A model trained with a different
input form can load correctly but return incorrect results.

Mount the same directory and set one variable when the application runs:

```bash
docker run --rm \
  --mount type=volume,src=data-chord,dst=/data \
  --mount type=bind,src="$(pwd)/models",dst=/models,readonly \
  --env DATA_CHORD_PROFILE=portable \
  --env DATA_CHORD_HARMONIZATION_METHOD=local \
  --env AWS_REGION=us-east-2 \
  --publish 8000:8000 \
  data-chord:<full-git-commit>
```

All mounted model files must be readable by the image's non-root `appuser`.

Before delivery, run `just verify-local-inference-container`. This builds both
image forms and runs a complete local-model job with generated GPT-2 and BERT
models. It does not use Bedrock or store test models in the repository.

The application converts the complete JSON file to typed configuration and
checks every model directory at startup. During Stage 3 it groups all terms for
the same model, loads that model once, runs the group, and releases the model
before it loads the next one. Local mode does not fall back to Bedrock. A CDE
without a model assignment causes the harmonization job to fail.

Portable workflow files are temporary. After a successful upload, the app
checks workflow storage in the background. At 80% of
`DATA_CHORD_WORKFLOW_STORAGE_LIMIT_GB`, it removes the least recently accessed
workflows until use is at or below 70%. A workflow accessed during the last 24
hours is not removed. The default limit is 10 GB. Cleanup does not remove
`/data/standards.sqlite`.

Run the same `load-sqlite` command to add a new standard version. To correct an
existing model version, add `--replace-existing`. Replacement is
transactional. New reference-data reads use the correction immediately. A
completed harmonization keeps its saved permissible-value snapshot. Rerun
harmonization to apply the correction to an existing workflow.

## Customer-platform deployment

Use this offer when the end provider already owns its application platform and
authentication system.

### AWS resources created

The customer-platform OpenTofu root creates exactly five managed AWS
resources. They use only two AWS services:

- Amazon S3: one private workflow bucket and its public-access block.
- Amazon DynamoDB: one reference and permissible-values table, one
  harmonization-cache table, and one CDE-recommendation-cache table.

It does not create IAM roles or policies, ECR, ECS, EC2, Lambda, VPC resources,
load balancers, API Gateway, Route 53, ACM, Cognito, Secrets Manager,
CodeBuild, or CloudWatch log groups. It outputs policy JSON. The customer can
attach this JSON to customer-owned roles.

### Plan and deploy the data plane

Create the foundation first. Use its schema-v2 handoff file directly:

```bash
AWS_PROFILE=example-admin just customer-plan \
  example staging ../datachord-infrastructure/.plans/example-foundation-handoff.json

AWS_PROFILE=example-admin just customer-deploy \
  example staging ../datachord-infrastructure/.plans/example-foundation-handoff.json
```

The state key is
`datachord/example/staging/customer-platform/tofu.tfstate`. The deploy command
stops if full-deployment state exists at
`datachord/example/staging/tofu.tfstate`.

After deployment, read the saved
`.plans/example-staging-customer-platform-outputs.json` file. It contains:

- `runtime_environment`: the complete non-secret container configuration;
- `runtime_data_policy_json`: S3 and DynamoDB access for the workload role;
- `bedrock_policy_json`: optional Bedrock Mantle access; and
- `reference_loader_policy_json`: write access for a separate data-loader role.

### Host and run the image

The end provider should host the image in a registry that its compute platform
can read. In AWS, use a customer-owned ECR repository. Kubernetes can also use
a customer-owned private OCI registry. The registry does not need network
access to S3 or DynamoDB. The running workload needs that access.

Build the exact reviewed commit and push it to the customer registry:

```bash
GITHUB_TOKEN=$(gh auth token)
export GITHUB_TOKEN
docker buildx build \
  --secret id=github_token,env=GITHUB_TOKEN \
  --tag 123456789012.dkr.ecr.us-east-2.amazonaws.com/data-chord:<full-git-commit> \
  --push .
```

Use short-lived registry credentials. The token is a build secret and must not
be stored in an image layer.

Run one container replica with one Uvicorn worker. The customer must provide:

- a workload role with the output policies;
- network routes or private endpoints for S3, DynamoDB, STS, and required
  Bedrock services;
- writable temporary storage;
- TLS, request limits, logs, monitoring, and restart behavior; and
- an authentication proxy that removes any client-supplied
  `X-Data-Chord-User-ID` header, authenticates the request, and sets exactly
  one trusted header value. The value must be a stable, immutable subject, not
  an email address or display name.

The container must accept inbound application traffic only from this proxy. A
client must not have any direct network path to the container.

The application uses `DATA_CHORD_IDENTITY_SOURCE=trusted_proxy`. It rejects a
missing, blank, duplicate, comma-separated, or control-character identity.
Only `/healthz` works without identity.

The customer owns S3 backup and recovery. The two durable DynamoDB tables use
point-in-time recovery and deletion protection. The CDE cache uses expiry and
can be rebuilt.

Load and verify the approved reference data with the command in section 6
before you expose the service. Use a separate customer-owned loader role and
the table name from `runtime_environment`.

## Full AWS deployment

The rest of this guide describes the full AWS offer. It uses one complete
example so that you can see each file name and command.

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
- A Secrets Manager secret for the programmatic API key. Store one random plain
  string with at least 32 characters. Do not store JSON. Use the default
  AWS-managed Secrets Manager encryption key. The example name is
  `data-chord/staging/programmatic-api-key`.
- Access to GPT-5.6 Luna in the account's default Bedrock Mantle project, with
  enough inference quota for Data Chord.
- An AWS profile that can assume the `deployer_role_arn` in the foundation
  handoff.

The CodeConnections credential gets the Data Chord source. The GitHub App
secret lets the image build install the private dependency. ECS reads the API
key secret when it starts the application. Do not put a credential or secret
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

Add the domain, hosted zone, repository, and secret names. The complete file
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
  "github_app_secret_name": "data-chord/build/github-app",
  "programmatic_api_key_secret_name": "data-chord/staging/programmatic-api-key"
}
```

The file accepts only these eleven fields. The domain must add one lowercase DNS
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
AWS_PROFILE=example-data-loader uv run python -m scripts.reference_data sync \
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
uv run python -m scripts.reference_data export \
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
