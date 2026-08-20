# Data Chord

Data harmonization workflow application. Upload CSV, TSV, or XLSX tabular data, review Bedrock CDE suggestions, run harmonization, and approve results before export.

CDE recommendation uses GPT-5.6 Luna through Amazon Bedrock Mantle. Value
harmonization uses the same AWS provider boundary.
Standard metadata and permissible values come from DynamoDB in the hosted
profile or SQLite in the portable profile.

XLSX uploads are treated as workbooks at the upload boundary. Stage 1 defaults
to the first worksheet and lets the user select another sheet before mapping.
Only the selected worksheet is harmonized, and downloads preserve the input
format.

For a detailed overview, see [app.md](app.md).

## Setup

1. Install [uv](https://docs.astral.sh/uv/) (manages Python and dependencies automatically):
   ```bash
   # macOS / Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Windows (PowerShell)
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   Restart your terminal after installing.

2. Clone and check out the latest release:
   ```bash
   git clone https://github.com/netrias/data_chord.git
   cd data_chord
   git checkout $(git describe --tags --abbrev=0)
   gh auth setup-git
   uv sync --frozen
   ```
   Data Chord installs two dependencies from pinned Git commits:
   `netrias/agentic_harmonization` and `netrias/netrias_client`. A licensed
   developer needs read access to the private `agentic_harmonization`
   repository and an authenticated Git client. `netrias_client` is public. The
   frozen install uses the committed lock file instead of resolving new
   versions.

3. Configure the populated reference-data table, both application cache tables,
   and the AWS region:
   ```bash
   cp .env.example .env
   # Edit .env with the DynamoDB table names and AWS_REGION.
   ```

4. Run:
   ```bash
   uv run python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
   ```
   Open http://localhost:8000.

## Updating

```bash
git fetch --tags
git checkout $(git describe --tags --abbrev=0 origin/main)
uv sync --frozen
```

## Development

With [just](https://github.com/casey/just) installed, run `just --list` for shortcuts. Key commands:

```bash
just sync        # Install locked Python and JavaScript development dependencies
just app-reload  # Dev server with auto-reload
just test        # Run tests
just lint        # Lint
just typecheck   # Type check
```

Pull requests from forks run infrastructure, JavaScript syntax, and JavaScript
unit checks. Python, TypeScript, and browser checks need read access to the
private `netrias/agentic_harmonization` dependency. They also install the public
`netrias/netrias_client` dependency. GitHub does not give the private credential
to forks. Before an internal test, a maintainer must review the
external source, workflow files, package scripts, and test commands. The
maintainer can then test the change on a branch in this repository.

### Performance Journeys

Use the local journey for browser/render timing while developing:

```bash
just perf-e2e
```

Use the staging journey for deployed user-experience timing once you are on the
company VPN and the timing instrumentation has been deployed:

```bash
just perf-staging
# or pass an explicit URL:
just perf-staging https://your-staging-host.example.com
```

The staging journey drives the real app flow and prints upload, analyze,
harmonize, Stage 4, Stage 5, and download timings.
Set `PERF_REMOTE_ROWS=50` to change the generated CSV size.

## AWS Hosting

OpenTofu deploys the application to ECS Fargate behind an Application Load
Balancer and Cognito. It stores workflow data in S3 and reference data in
DynamoDB.

See [DEPLOYMENT.md](DEPLOYMENT.md) for the complete environment and deployment
procedure. See [infra/README.md](infra/README.md) for infrastructure ownership
and deployment safety rules.

## Portable container

The portable profile needs one Docker image, one persistent `/data` volume,
and AWS credentials that can call the required Bedrock models. The customer
owns TLS, authentication, and network access to the container.

Load an approved reference-data export into the volume before the first run:

```bash
REFERENCE_SHA256=$(shasum -a 256 approved-reference-data.json | awk '{print $1}')
docker run --rm \
  --mount type=volume,src=data-chord,dst=/data \
  --mount type=bind,src="$(pwd)/approved-reference-data.json",dst=/import/reference.json,readonly \
  data-chord:VERSION \
  python -m scripts.reference_data load-sqlite \
    --input /import/reference.json \
    --expected-sha256 "$REFERENCE_SHA256" \
    --database /data/standards.sqlite
```

Run the application with the same volume:

```bash
docker run --rm \
  --mount type=volume,src=data-chord,dst=/data \
  --env DATA_CHORD_PROFILE=portable \
  --env DATA_CHORD_WORKFLOW_STORAGE_LIMIT_GB=10 \
  --env AWS_REGION=us-east-2 \
  --publish 8000:8000 \
  data-chord:VERSION
```

The AWS SDK uses its standard credential chain. On AWS, use a workload role.
For local Docker testing, pass short-lived `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` values from the host. Never
put credentials in the image.

Portable workflow files are temporary. After a successful upload, the app
checks workflow storage in the background. At 80% of
`DATA_CHORD_WORKFLOW_STORAGE_LIMIT_GB`, it removes the least recently accessed
workflows until use is at or below 70%. A workflow accessed during the last 24
hours is not removed. The default limit is 10 GB. Cleanup does not remove
`/data/standards.sqlite`.

To publish a changed standard, give it a new external version and run the same
`load-sqlite` command. Existing versions cannot be replaced with different
content.
