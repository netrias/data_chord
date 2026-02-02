# Data Chord

Data harmonization workflow application. Upload tabular data, review AI-suggested column mappings, run harmonization, and approve results before export.

For a comprehensive overview of what Data Chord does and why, see [app.md](app.md).

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (Astral's Python package manager)
- AWS credentials configured (for harmonization API access)

## Quick Start

```bash
# Install dependencies
uv sync

# Create .env file with required keys
cp .env.example .env  # Then edit with your API keys

# Run the application
uv run uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 in your browser.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NETRIAS_API_KEY` | Yes | API key for Netrias harmonization and Data Model Store |
| `DATA_MODEL_KEY` | Yes | Target data model identifier (e.g., `CCDI`) |
| `DATA_MODEL_STORE_API_KEY` | No | Separate API key for Data Model Store (falls back to `NETRIAS_API_KEY`) |

## Docker

```bash
# Build
docker build -t data-chord .

# Run
docker run -p 8000:8000 --env-file .env data-chord
```

## Development

```bash
# Install with dev dependencies
uv sync --extra dev

# Run tests
uv run pytest

# Lint
uv run ruff check src backend tests

# Type check
uv run basedpyright
```

## Workflow Stages

1. **Upload** - Upload CSV file and analyze columns
2. **Review Mappings** - Review/override AI-suggested column-to-model mappings
3. **Harmonize** - Execute harmonization pipeline
4. **Review Results** - Inspect and approve harmonized values
5. **Export** - Download harmonized dataset and audit artifacts

## CDE Endpoint Configuration

Data Chord uses `netrias-client==0.1.0` for CDE discovery and harmonization. See [ADR 005](adr/ADR_005_cde_lambda_migration.md) for migration details and rollback procedure.
