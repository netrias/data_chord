# Riga Architecture

## Directory Structure

```
src/
├── domain/                    # Core domain models and utilities (no stage dependencies)
│   ├── __init__.py
│   ├── manifest/              # Harmonization manifest data models and I/O
│   │   ├── models.py          # ManifestRow, ManualOverride, ManifestSummary
│   │   ├── reader.py          # read_manifest_parquet
│   │   └── writer.py          # add_manual_override
│   ├── storage.py             # Generic file storage abstraction
│   └── ...
├── stage_1_upload/            # File upload and metadata
├── stage_2_mapping/           # Column mapping to CDEs
├── stage_3_harmonize/         # Harmonization execution
├── stage_4_review_results/    # Manual review and override UI
├── stage_5_review_summary/    # Final summary and export
└── assets/                    # Shared static assets
```

## Dependency Rules

**Stages depend on domain, never on each other.**

```
┌─────────────────────────────────────────────────────────┐
│                        domain/                          │
│  (models, manifest, storage, constants, utilities)      │
└─────────────────────────────────────────────────────────┘
        ▲           ▲           ▲           ▲
        │           │           │           │
   stage_1     stage_2     stage_3     stage_4/5
```

This ensures:
- **No circular dependencies** between stages
- **Clear contracts** - shared types live in domain
- **Testability** - domain code has no web framework dependencies
- **Reusability** - domain logic can be used outside the web app

## Adding New Shared Code

When code is used by multiple stages:

1. **Ask**: Is this domain logic or stage-specific?
2. **If domain**: Add to `src/domain/` (possibly a new submodule)
3. **If stage-specific**: Keep in the relevant stage module

Examples:
- `ManifestRow` dataclass → `domain/manifest/models.py` (used by stages 3, 4, 5)
- `StageFourCell` → `stage_4_review_results/router.py` (stage-specific response model)
- `UploadStorage` → Could argue either way; currently in `stage_1_upload/services.py`

## Module Patterns

### Domain Submodules

For complex domain concepts, use a submodule pattern:

```
domain/manifest/
├── __init__.py    # Public API exports
├── models.py      # Dataclasses and types
├── reader.py      # Read operations
└── writer.py      # Write operations
```

The `__init__.py` re-exports public symbols for clean imports:
```python
from src.domain.manifest import ManifestRow, read_manifest_parquet
```

### Stage Modules

Each stage follows a similar structure:
```
stage_N_name/
├── __init__.py
├── router.py          # FastAPI routes
├── schemas.py         # Request/response models (if stage-specific)
├── templates/         # Jinja2 HTML templates
└── static/            # CSS, JS, images
```
