# Data Chord: Application Overview

## What It Is

Data Chord is a web-based data harmonization tool that transforms tabular data (CSV files) into standardized formats through a guided, human-in-the-loop workflow. It bridges raw research data and the Common Data Elements (CDEs) required by data repositories.

## The Problem

Research datasets use inconsistent terminology — a diagnosis might appear as "melanoma," "malignant melanoma," "MM," or "melanoma, primary" depending on the source. This makes data aggregation and analysis difficult.

Manual curation is accurate but slow. Automated normalization is fast but opaque, with errors that need expert review anyway. Data Chord combines both: ML suggests standardized values with confidence scores, and curators focus their expertise on low-confidence suggestions while automation handles routine mappings.

## Workflow Stages

1. **Upload** — User uploads a CSV. Data Chord profiles columns and calls the CDE Recommendation API to suggest column-to-schema mappings.
2. **Review Column Mappings** — Users accept, override, or skip the AI's column-to-CDE suggestions, with sample data shown for verification.
3. **Harmonize** — The Netrias client SDK transforms each cell value to its standardized equivalent, producing harmonized values, confidence scores, and alternatives.
4. **Review Results** — Batch-oriented review sorted by confidence (lowest first). Users approve high-confidence batches in bulk and manually review uncertain mappings.
5. **Export** — Download the harmonized dataset and an audit bundle (column mapping plan, manual overrides, confidence distributions, model versions).

## Key Design Decisions

- **Server-side rendering with HTMX** — FastAPI + Jinja2 templates + HTMX for interactivity, keeping the codebase simple with minimal JavaScript.
- **Stage-based architecture** — Each stage is its own module with its own router, templates, and assets. Stages share code only through `domain/`, preventing circular dependencies.
- **Manifest as source of truth** — The harmonization manifest (Parquet) captures every transformation decision and serves as the audit trail.

## Technical Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI (Python 3.13+) |
| Templates | Jinja2 |
| Interactivity | HTMX |
| Data Processing | PyArrow (Parquet) |
| Harmonization | Netrias Client SDK |
| Deployment | Docker |

## See Also

- [README.md](README.md) — Setup and quick start
- [ARCHITECTURE.md](ARCHITECTURE.md) — Architectural details
- [workflow.md](workflow.md) — Complete workflow specification
