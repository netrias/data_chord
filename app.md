# Data Chord: Application Overview

## What It Is

Data Chord is a web-based data harmonization application that transforms tabular data (CSV files) into standardized formats through a guided, human-in-the-loop workflow. It bridges the gap between raw research data and the Common Data Elements (CDEs) required by data repositories and regulatory bodies.

## The Problem It Solves

Research institutions and data curators face a persistent challenge: submitted datasets use inconsistent terminology. A clinical diagnosis might appear as "melanoma," "malignant melanoma," "MM," or "melanoma, primary" depending on the source institution, investigator, or data entry conventions. This inconsistency makes data aggregation, querying, and analysis difficult or impossible.

Traditional approaches to this problem fall into two camps:

1. **Manual curation** - Domain experts manually review and correct each value. Accurate but slow, expensive, and prone to fatigue-related errors on large datasets.

2. **Automated normalization** - Algorithms map values to controlled vocabularies. Fast but opaque, with inevitable errors that require expert review anyway.

Data Chord combines both approaches. It uses machine learning to suggest standardized values with confidence scores, then surfaces low-confidence suggestions for human review. The result: curators focus their expertise where it matters most while automation handles the routine mappings.

## How It Works

### Stage 1: Upload

The user uploads a CSV file. Data Chord profiles each column, detecting data types and sampling values. When the user clicks "Analyze Columns," the application calls the CDE Recommendation API to determine which columns map to which target schema fields.

### Stage 2: Review Column Mappings

Data Chord presents the AI's column-to-CDE suggestions with confidence indicators. Users can:

- Accept suggestions as-is
- Override mappings manually when the AI guessed wrong
- Mark columns to skip (not every column needs harmonization)

The interface shows sample data from each column so users can verify the AI understood the column's semantics.

### Stage 3: Harmonize

With mappings confirmed, Data Chord executes the harmonization pipeline via the Netrias client SDK. Each cell value in the mapped columns is transformed to its standardized equivalent. The process produces:

- Harmonized values
- Confidence scores for each transformation
- Alternative suggestions when the model was uncertain
- The original values (preserved for audit)

### Stage 4: Review Results

A batch-oriented review interface presents the harmonization results, sorted by confidence (lowest first). For each value, users see:

- Original input
- Suggested harmonization
- Confidence score (bucketed as low/medium/high)
- Alternative suggestions
- Manual override entry field

Users can approve high-confidence batches in bulk, then focus manual attention on uncertain mappings. The interface tracks review progress toward completion.

### Stage 5: Export

Once review is complete, users download:

- **Harmonized dataset** - The original file with harmonized values replacing or augmenting the source columns
- **Audit bundle** - JSON containing the column mapping plan, all manual overrides with timestamps, confidence distributions, and model versions

## Key Design Decisions

### Server-Side Rendering with HTMX

Rather than a heavyweight SPA framework, Data Chord uses FastAPI with Jinja2 templates and HTMX for interactivity. This keeps the codebase simple, reduces JavaScript complexity, and allows rapid iteration. The application can still evolve to a React frontend later if needed.

### Stage-Based Architecture

Each workflow stage lives in its own module (`stage_1_upload/`, `stage_2_review_columns/`, etc.) with its own router, templates, and static assets. Stages share code only through the `domain/` module, preventing circular dependencies and keeping each stage independently testable.

### Manifest as Source of Truth

The harmonization manifest (stored as Parquet) captures every transformation decision: original values, harmonized outputs, confidence scores, and manual overrides. This manifest persists across stages and serves as the audit trail. Any downstream process can reconstruct exactly what happened during harmonization.

## Technical Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI (Python 3.13+) |
| Templates | Jinja2 |
| Interactivity | HTMX |
| Data Processing | Pandas, PyArrow, Parquet |
| Harmonization API | Netrias Client SDK |
| CDE Recommendations | External REST API |
| Deployment | Docker (single container) |

## Who Should Use It

Data Chord serves two primary user types:

1. **Data Curators** - Staff at research institutions who prepare datasets for submission to repositories. They need to transform heterogeneous submissions into repository-compliant formats.

2. **Research Coordinators** - Project leads who receive data from multiple sites and need to harmonize terminology before analysis.

Both groups benefit from the same workflow: automated suggestions for routine mappings, human oversight for uncertain cases, and complete audit trails for regulatory compliance.

## What It Does Not Do

Data Chord focuses specifically on value-level harmonization to controlled vocabularies. It does not:

- Perform schema transformations (changing table structure)
- Handle file format conversions beyond CSV
- Integrate directly with repository submission APIs (yet)
- Support real-time streaming data

These capabilities may be added in future versions or handled by complementary tools in the data submission pipeline.

## Getting Started

See [README.md](README.md) for installation and quick start instructions.

For architectural details, see [ARCHITECTURE.md](ARCHITECTURE.md).

For the complete workflow specification, see [workflow.md](workflow.md).
