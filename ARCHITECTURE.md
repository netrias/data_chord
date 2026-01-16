# Data Chord Architecture

## Overview

Data Chord follows a **stage-based architecture** where each workflow stage is an independent module that communicates only through a shared domain layer. This ensures stages remain independently testable while sharing common data models and services.

## Directory Structure

```
src/
├── domain/                    # Shared business logic, models, services
│   ├── manifest/              # Harmonization manifest I/O (Parquet)
│   ├── storage/               # File storage abstractions
│   ├── data_model_client.py   # HTTP client for Data Model Store API
│   ├── data_model_cache.py    # Session-scoped CDE/PV caching
│   ├── harmonize.py           # Netrias harmonization client wrapper
│   ├── mapping_service.py     # Column-to-CDE recommendations
│   ├── pv_validation.py       # Permissible value validation
│   └── change.py              # Change type enums
├── stage_1_upload/            # File upload and column analysis
├── stage_2_review_columns/    # Column-to-CDE mapping review
├── stage_3_harmonize/         # Harmonization pipeline execution
├── stage_4_review_results/    # Human review of transformations
├── stage_5_review_summary/    # Final summary and export
└── shared/                    # Cross-stage templates and utilities
```

## Stage Independence Rule

**Stages depend only on `domain/`, never on each other.**

```
                    ┌─────────────────┐
                    │     DOMAIN      │
                    │                 │
                    │  - models       │
                    │  - schemas      │
                    │  - services     │
                    │  - storage      │
                    └────────┬────────┘
                             │
         ┌───────┬───────┬───┴───┬───────┬───────┐
         │       │       │       │       │       │
      Stage 1  Stage 2  Stage 3  Stage 4  Stage 5
```

This constraint is enforced by a pre-commit hook (`no-cross-stage-imports`).

Benefits:
- No circular dependencies between stages
- Clear contracts via shared types in domain
- Domain code has no web framework dependencies
- Domain logic reusable outside the web app

## Data Flow

1. **Upload** (Stage 1): CSV → `UploadStorage` → file_id
2. **Mapping** (Stage 2): file_id → `MappingService` → column mappings (JSON)
3. **Harmonize** (Stage 3): mappings → `HarmonizeService` → manifest (Parquet)
4. **Review** (Stage 4): manifest + manual overrides → reviewed manifest
5. **Export** (Stage 5): manifest → harmonized CSV + JSON manifest (human-readable audit trail)

## Storage Architecture

Storage uses a layered abstraction:

```
UploadStorage (high-level typed API)
       │
       ▼
StorageBackend + Serializer (format conversion)
       │
       ▼
LocalStorageBackend (filesystem)
```

**File Types:**
- `ORIGINAL_CSV` - User's uploaded file
- `UPLOAD_META` - Column metadata (JSON)
- `COLUMN_MAPPING` - Stage 2 mapping decisions (JSON)
- `HARMONIZATION_MANIFEST` - All transformation data (Parquet)
- `PV_MANIFEST` - Permissible values and column-CDE mappings (JSON)
- `REVIEW_OVERRIDES` - Manual overrides from Stage 4 review (JSON)
- `HARMONIZED_CSV` - Final export

## Manifest Structure

The harmonization manifest captures every transformation decision:

| Field | Description |
|-------|-------------|
| `column_key` | Target CDE column |
| `to_harmonize` | Original value |
| `top_harmonization` | AI's best suggestion |
| `confidence_score` | Model confidence (0-1) |
| `alternatives` | Other suggestions |
| `manual_overrides` | User corrections with timestamps |
| `row_indices` | Source rows containing this value |

## Key Design Decisions

See `adr/` for architectural decision records. Summary:

- **Server-side rendering** with HTMX for interactivity
- **Parquet for manifest** - efficient columnar storage, schema evolution
- **Graceful degradation** - missing PV data doesn't block validation
- **Whitespace-sensitive** - whitespace differences are semantically meaningful
- **PV Override Protection** - valid original values are never replaced by AI (ADR 004)

## Module Patterns

### Domain Submodules

For complex domain concepts:

```
domain/manifest/
├── __init__.py    # Public API exports
├── models.py      # Dataclasses and types
├── reader.py      # Read operations
└── writer.py      # Write operations
```

### Stage Modules

Each stage follows:
```
stage_N_name/
├── __init__.py
├── router.py          # FastAPI routes
├── templates/         # Jinja2 HTML templates
└── static/            # CSS, JS
```

## Technology Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI (Python 3.13+) |
| Templates | Jinja2 |
| Interactivity | HTMX |
| Data Processing | Pandas, PyArrow |
| Package Management | uv |
