# Data Chord

Data harmonization workflow application. Upload CSV, TSV, or XLSX tabular data, review AI-suggested column mappings, run harmonization, and approve results before export.

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
   uv sync --frozen
   ```
   The frozen install is an important supply-chain security control: normal setup uses the committed lockfile instead of resolving newly published packages.

3. Configure your API key:
   ```bash
   cp .env.example .env
   # Edit .env and add your Netrias API key (contact Netrias for access)
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

## AWS Hosting

The app has an OpenTofu starter stack in [infra/README.md](infra/README.md). It deploys ECS Fargate behind ALB + Cognito, stores workflow artifacts in S3, and uses CodeBuild to test, build, push, and redeploy the Docker image.
