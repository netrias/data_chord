# Data Chord Architecture

Data Chord is a web-based data harmonization application that transforms tabular
CSV data into standardized Common Data Element (CDE) formats through a guided,
human-in-the-loop workflow. See [app.md](app.md) for the product overview.

---

## Directory Structure

```
src/
├── domain/                    # Shared domain layer (no stage dependencies)
│   ├── cde.py                 # CDEInfo, column mapping types
│   ├── change.py              # ChangeType, RecommendationType enums
│   ├── config.py              # Build-level config validation (NETRIAS_API_KEY)
│   ├── session.py             # Browser sessionStorage key constants, UILabel
│   ├── schemas.py             # Cross-stage API request/response models
│   ├── harmonize.py           # HarmonizeService (Netrias client wrapper)
│   ├── mapping_service.py     # MappingDiscoveryService (CDE suggestion)
│   ├── data_model_adapter.py  # Adapter: SDK types → domain types (CDEs, PVs, data models)
│   ├── data_model_cache.py    # Session-scoped CDE/PV caching
│   ├── pv_validation.py       # Permissible value conformance checking
│   ├── pv_persistence.py      # PV manifest disk persistence
│   ├── paths.py               # Centralized project path resolution
│   ├── dependencies.py        # Lazy-initialized service singletons
│   ├── manifest/              # Harmonization manifest I/O
│   │   ├── models.py          # ManifestRow, ManualOverride
│   │   ├── reader.py          # read_manifest_parquet
│   │   └── writer.py          # add_manual_overrides_batch, apply_pv_adjustments_batch
│   └── storage/               # Typed file storage abstraction
│       ├── file_types.py      # FileType enum, file naming convention
│       ├── backends.py        # StorageBackend ABC, LocalStorageBackend
│       ├── serializers.py     # JSON / Parquet / RawBytes serializers
│       ├── file_store.py      # FileStore facade (save/load with serialization)
│       └── upload_storage.py  # UploadStorage (file persistence + constraints)
├── stage_1_upload/            # File upload and data model selection
├── stage_2_review_columns/    # Column-to-CDE mapping review
├── stage_3_harmonize/         # Harmonization execution via Netrias SDK
├── stage_4_review_results/    # Batch review and manual overrides
├── stage_5_review_summary/    # Summary metrics and export/download
└── shared/                    # Shared static assets (CSS tokens, JS modules)
```

---

## Stage Independence Rule

**Stages depend on `domain/`, never on each other.**

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

This ensures:
- **No circular dependencies** between stages
- **Clear contracts** — shared types live in domain
- **Testability** — domain code has no web framework dependencies
- **Reusability** — domain logic can be used outside the web app

A pre-commit hook (`no-cross-stage-imports`) enforces this rule. It will fail if
any `src/stage_X` module imports from a different `src/stage_Y` module.

### Placement Guidelines

| Code characteristic | Location |
|---|---|
| Used by multiple stages (types, services, storage) | `src/domain/` |
| Used by one stage only (schemas, templates, JS/CSS) | That stage's module |
| Shared frontend tokens (colors, spacing) | `src/shared/static/` |

---

## Application Wiring

`backend/app/main.py` contains the `create_app()` factory:

1. Loads `.env` for local secrets (shell env vars take precedence)
2. Validates required config (`NETRIAS_API_KEY`) via `validate_required_config()`
3. Configures logging format
4. Creates the `FastAPI` instance with lifespan manager for graceful shutdown
5. Adds `CORSMiddleware` (configurable via `CORS_ALLOW_ORIGINS`)
6. Includes each stage router (`/stage-1` through `/stage-5`)
7. Mounts static file directories (`/assets/shared`, `/assets/stage-{N}`)
8. Adds favicon route and root redirect (`/` → `/stage-1`)

### Service Initialization

Services are lazily initialized via module-level singletons in
`domain/dependencies.py`. A single `NetriasClient` instance (via
`get_netrias_client()`) is shared across `HarmonizeService`,
`MappingDiscoveryService`, and the data model adapter. Routers import getter
functions (`get_upload_storage()`, `get_file_store()`, `get_mapping_service()`,
`get_harmonize_service()`) which construct and cache instances on first call.
`cleanup_services()` is called on shutdown.

---

## Data Flow

1. **Upload** (Stage 1): CSV → `UploadStorage` → file_id → data model selection
2. **Mapping** (Stage 2): file_id → `MappingService` → column-to-CDE mappings
3. **Harmonize** (Stage 3): mappings → `HarmonizeService` → manifest (Parquet) + PV adjustments
4. **Review** (Stage 4): manifest + PV combobox overrides → reviewed manifest with audit trail
5. **Export** (Stage 5): manifest → harmonized CSV + JSON manifest (human-readable audit trail)

---

## Workflow Stages

### Stage 1: Upload

**Purpose:** Accept a CSV file, select data model, and discover CDE column mappings.

**Endpoints:**
- `GET /stage-1` — Render upload page
- `POST /stage-1/upload` — Store file via `UploadStorage`, return `file_id`
- `POST /stage-1/analyze` — Profile columns, call `MappingDiscoveryService.discover()`
- `GET /stage-1/data-models` — Fetch available data models from Data Model Store API

**Flow:** User drops CSV → file stored to disk → user selects data model and
version in modal → navigates to Stage 2 with `file_id` query parameter.

### Stage 2: Review Column Mappings

**Purpose:** Review and override AI-suggested column-to-CDE mappings.

**Endpoints:**
- `GET /stage-2` — Render mapping review page

**Flow:** Page reads analysis results from `sessionStorage` (populated by
Stage 1). User reviews dropdowns per column (populated from session cache CDEs),
overrides as needed, then clicks "Harmonize" which saves mappings to
`sessionStorage` and navigates to Stage 3.

This stage is read-only on the server — no files are written.

### Stage 3: Harmonize

**Purpose:** Execute the harmonization pipeline via the Netrias SDK and apply PV adjustments.

**Endpoints:**
- `GET /stage-3` — Render progress page (with animated loading state)
- `POST /stage-3/harmonize` — Run harmonization, fetch PVs, apply adjustments, return summary

**Flow:** Receives `HarmonizeRequest` with `file_id`, column overrides, and
target schema. Runs harmonization and PV fetching in parallel. Applies PV
adjustments to the manifest (preferring original values when already conformant).
Persists PV manifest to disk for session recovery. Returns column-level
breakdown metrics.

### Stage 4: Review Results

**Purpose:** Batch review harmonized values with PV conformance feedback;
collect manual overrides via PV combobox.

**Endpoints:**
- `GET /stage-4` — Render review page
- `POST /stage-4/rows` — Fetch manifest rows for review
- `POST /stage-4/overrides` — Save overrides + review state
- `GET /stage-4/overrides/{file_id}` — Fetch saved overrides and progress
- `DELETE /stage-4/overrides/{file_id}` — Clear review session
- `GET /stage-4/non-conformant/{file_id}` — Fetch non-conformant values for gating
- `POST /stage-4/row-context` — Fetch original row data by indices
- `POST /stage-4/term-row-indices` — Fetch full row indices for a term (when truncated in initial response)

**Flow:** Loads original CSV and harmonization manifest. Builds comparison rows
with PV conformance indicators. UI displays batches with confidence sorting.
PV combobox enforces conformant-only selection. Row context popup provides
original CSV context. State persistence saves review mode, sort, filters,
position, and all overrides.

**Two review modes:**
- **Column mode** — 3×3 to 5×5 grid of value cards per batch
- **Row mode** — 5–20 tabular rows per batch

### Stage 5: Export & Summary

**Purpose:** Display harmonization summary metrics with transformation history
and download final artifacts.

**Endpoints:**
- `GET /stage-5` — Render summary page
- `POST /stage-5/summary` — Aggregate per-column change counts with history
- `POST /stage-5/download` — Generate and stream ZIP file

**Flow:** Summary classifies each row as `UNCHANGED`, `AI_HARMONIZED`, or
`MANUAL_OVERRIDE`. Displays segmented bar visualization per column. Shows
transformation history (Original → AI → User). Non-conformant banner
warns of values not in target PVs. Download applies review overrides to the
harmonized CSV, bundles final CSV and JSON manifest into a ZIP, then clears
session cache.

---

## Domain Layer

### CDE Metadata (`cde.py`)

`CDEInfo` dataclass holds CDE metadata (cde_id, cde_key, description,
version_label) fetched dynamically from the Data Model Store API.
`ColumnMappingSet` is the typed container for all column-to-CDE assignments.

### Data Model Integration (`data_model_adapter.py`, `data_model_cache.py`)

`data_model_adapter` is a thin adapter that converts `netrias_client` SDK types
to domain types (`DataModelSummary`, `CDEInfo`, PV frozensets). It uses the
shared `NetriasClient` singleton from `dependencies.py`. `SessionCache` provides
session-scoped, thread-safe caching of CDEs and PV sets (frozenset for O(1)
membership testing), with disk persistence via `pv_persistence.py` for server
restart recovery.

### PV Validation (`pv_validation.py`)

Pure functions for whitespace-sensitive PV conformance checking. Priority chain:
original value (if conformant) → AI suggestion → top suggestions → non-conformant.
Valid original values are never replaced by AI suggestions (ADR 004).

### Change Classification (`change.py`)

`ChangeType` enum (`UNCHANGED`, `AI_HARMONIZED`, `MANUAL_OVERRIDE`),
`RecommendationType` enum (`AI_CHANGED`, `AI_UNCHANGED`, `NO_RECOMMENDATION`),
and confidence thresholds.

### Manifest Submodule (`manifest/`)

The harmonization manifest is the **single source of truth** for every
transformation decision. It is a Parquet file containing one row per
original-value-to-harmonized-value pair, with:

- Original and harmonized values
- Confidence scores
- Row indices (which source CSV rows share this mapping)
- Manual override audit trail (user, timestamp, value)
- PV adjustment records (source, adjusted value, timestamp)

`reader.py` parses parquet into `ManifestSummary`. `writer.py` appends user
edits via `add_manual_overrides_batch()` and applies PV adjustments via
`apply_pv_adjustments_batch()`.

### Storage Submodule (`storage/`)

A layered file storage abstraction:

```
FileStore (facade)
  └─ StorageBackend (ABC)
       └─ LocalStorageBackend (filesystem)
  └─ Serializer (ABC)
       ├─ JSONSerializer
       ├─ ParquetSerializer
       └─ RawBytesSerializer
```

`FileType` enum defines the semantic file types and their naming conventions.
Files are named `{file_id}_{suffix}.{extension}` (e.g.,
`abc123_overrides.json`, `abc123_harmonization.parquet`).

`UploadStorage` handles uploaded CSV persistence, constraints validation
(file size, type, extension), and metadata tracking via `UploadedFileMeta`.

### Services

| Service | Responsibility |
|---|---|
| `HarmonizeService` | Wraps `NetriasClient.harmonize()` with manifest merging and error handling |
| `MappingDiscoveryService` | Wraps `NetriasClient.discover_mapping_from_csv()` (confidence threshold 0.7) |
| `data_model_adapter` | Thin adapter: SDK types → domain types (data model list, CDEs, PVs) |

Services degrade gracefully: missing API keys or client init failures are
logged and produce stub results so the workflow can continue.

### Cross-Stage Schemas (`schemas.py`)

`HarmonizeRequest` and `HarmonizeResponse` are defined here because they are
used across stages (Stage 2 constructs the request, Stage 3 processes it).
Stage-specific schemas (e.g., `StageFourResultsRequest`) live in their own
stage's `schemas.py`.

---

## File Storage Layout

All persistent data lives under a single base directory (configurable via
`UPLOAD_BASE_DIR` in `dependencies.py`).

```
uploads/
├── files/
│   ├── {file_id}.csv                    # Original uploaded CSV
│   └── {file_id}_harmonized.csv         # Harmonized CSV from Netrias
├── meta/
│   └── {file_id}.json                   # UploadedFileMeta (name, size, timestamp)
└── manifests/
    ├── {file_id}.json                   # Stored manifest payload (JSON)
    ├── {file_id}_harmonization.parquet  # Harmonization manifest (Parquet)
    ├── {file_id}_mapping.json           # Column mappings
    ├── {file_id}_overrides.json         # Review overrides from Stage 4
    ├── {file_id}_pv_manifest.json       # PV sets and column-CDE mappings
    └── {file_id}_meta.json              # Upload metadata (FileStore copy)
```

---

## Frontend Architecture

### Rendering

Server-side rendered Jinja2 templates — one HTML file per stage. No SPA
framework. Each template loads shared design tokens from
`/assets/shared/tokens.css` and stage-specific CSS/JS from `/assets/stage-{N}/`.

### JavaScript

Vanilla ES6 modules with direct DOM manipulation. No bundler. Key patterns:

- **Session pass-through:** Stages 1–3 pass payloads via `sessionStorage`
  (keys centralized in `storage-keys.js`)
- **Debounced auto-save:** Stage 4 batches user edits and POSTs to the server
  after a delay to avoid request spam
- **Modular review modes:** Stage 4 delegates rendering to
  `review_mode_column.js` or `review_mode_row.js` depending on user selection
- **Pure state functions:** `card-state.js` and `row-state.js` derive display
  state without DOM access (testable in isolation)

### Styling

- `src/shared/static/tokens.css` — Design tokens (colors, spacing, typography)
- Stage-specific CSS files for layout and components
- 3D button styling with layered shadow elements

---

## External Integrations

### Netrias Client SDK (`netrias-client 0.3.x`)

| Method | Used by |
|---|---|
| `discover_mapping_from_csv()` | `MappingDiscoveryService` (CDE recommendations) |
| `harmonize()` | `HarmonizeService` |
| `list_data_models()` | `data_model_adapter` (data model list) |
| `list_cdes()` | `data_model_adapter` (CDE metadata) |
| `get_pv_set_async()` | `data_model_adapter` (permissible values) |

Configuration: `NETRIAS_API_KEY` environment variable (loaded from `.env`).
The client is initialized with `Environment.PROD` which resolves all service
URLs (harmonization, discovery, Data Model Store) from a built-in registry.

---

## Key Design Decisions

See `adr/` for architectural decision records:

- [ADR 001](adr/ADR_001_transformation_history.md) — Transformation history
- [ADR 002](adr/ADR_002_row_context_popup.md) — Row context popup
- [ADR 003](adr/ADR_003_pv_manifest_persistence.md) — PV manifest persistence
- [ADR 004](adr/ADR_004_pv_override_protection.md) — PV override protection
- [ADR 005](adr/ADR_005_cde_lambda_migration.md) — CDE Lambda migration (initial netrias-client SDK adoption)
- [ADR 006](adr/ADR_006_env_simplification.md) — Environment simplification and SDK migration
- [ADR 007](adr/ADR_007_ai_whitespace_trimming.md) — AI output whitespace trimming at reader boundary
- [ADR 008](adr/ADR_008_release_strategy.md) — Release strategy (git tags + GitHub Releases)

**Key principles:**

- **Server-side rendering** with HTMX for interactivity
- **Parquet for manifest** — efficient columnar storage, schema evolution
- **Graceful degradation** — missing PV data doesn't block validation
- **Character significance** — all character differences (case, whitespace, punctuation) are semantically meaningful
- **PV Override Protection** — valid original values are never replaced by AI (ADR 004)
- **Confidence threshold** — CDE discovery only accepts mappings with confidence ≥ 0.7; incomplete entries (missing `cde_id` or `targetField`) are filtered

---

## Cross-Cutting Concerns

### Error Handling

Upload errors are mapped to HTTP status codes at the router boundary:
- `UnsupportedUploadError` → 415 (Unsupported Media Type)
- `UploadTooLargeError` → 413 (Request Entity Too Large)
- Missing file or manifest → 404 (Not Found)

Services catch client SDK exceptions internally and log with context.

### Logging

Format: `%(asctime)s | %(levelname)s | %(name)s | %(message)s`

Set globally in `_configure_logging()` at app startup.

### Row Indexing Convention

Row keys are **1-indexed** throughout Stages 4 and 5, matching the UI row
numbers displayed to users.

---

## Module Patterns

### Domain Submodules

For complex domain concepts, use a submodule with an `__init__.py` that
re-exports public symbols:

```
domain/manifest/
├── __init__.py    # Public API exports
├── models.py      # Dataclasses and types
├── reader.py      # Read operations (queries)
└── writer.py      # Write operations (commands)
```

```python
from src.domain.manifest import ManifestRow, read_manifest_parquet
```

### Stage Modules

Each stage follows a consistent structure:

```
stage_N_name/
├── __init__.py
├── router.py          # FastAPI routes
├── schemas.py         # Stage-specific request/response models (optional)
├── templates/         # Jinja2 HTML templates
└── static/            # CSS, JS, images
```

### Adding New Shared Code

1. **Ask:** Is this domain logic or stage-specific?
2. **If domain:** Add to `src/domain/` (possibly a new submodule)
3. **If stage-specific:** Keep in the relevant stage module

Examples:
- `ManifestRow` dataclass → `domain/manifest/models.py` (used by stages 3, 4, 5)
- `StageFourCell` → `stage_4_review_results/router.py` (stage-specific response model)

---

## External Dependencies

| Dependency | Purpose |
|------------|---------|
| `netrias-client` (0.3.x) | CDE discovery, harmonization, and Data Model Store access via Netrias SDK |

## Technology Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI (Python 3.13+) |
| Templates | Jinja2 |
| Interactivity | HTMX |
| Data Processing | PyArrow |
| Package Management | uv |

