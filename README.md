# Data Chord

Data harmonization workflow application. Upload CSV, TSV, or XLSX tabular data, review Bedrock CDE suggestions, run harmonization, and approve results before export.

CDE recommendation uses GPT-5.6 Luna through Amazon Bedrock Mantle. Value
harmonization uses the same AWS provider boundary.
Standard metadata and permissible values come from a dedicated DynamoDB table.

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
   Data Chord uses the private agentic harmonization dependency. A licensed
   developer needs read access to `netrias/agentic_harmonization` and an
   authenticated Git client before the frozen install can succeed.
   The frozen install is an important supply-chain security control: normal setup uses the committed lockfile instead of resolving newly published packages.

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
just sync        # Install with dev dependencies
just app-reload  # Dev server with auto-reload
just test        # Run tests
just lint        # Lint
just typecheck   # Type check
```

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

OpenTofu deploys the app to ECS Fargate behind an ALB and Cognito. It stores
workflow data in S3 and reference data in DynamoDB. Each environment has one
checked-in JSON file. Operators use two deployment commands:

```bash
AWS_PROFILE=default just plan netrias staging
AWS_PROFILE=default just deploy netrias staging
```

`plan` does not change AWS resources. `deploy` has no interactive prompt and
uses the exact saved receipt from `plan`. See [infra/README.md](infra/README.md)
for environment setup and ownership boundaries.
