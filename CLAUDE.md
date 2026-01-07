# Data Chord - Project Instructions

## Architecture

### Stage Independence Rule

**Stages must only depend on `domain`, never on each other.**

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

**What belongs in `domain/`:**
- Shared data models and TypedDicts
- Shared HTTP request/response schemas (used by multiple stages)
- Service clients (HarmonizeService, MappingService)
- Storage abstractions (FileStore, UploadStorage)
- Constants and enums (SessionKey, ChangeType, CDEField)
- Dependency injection getters

**What belongs in stages:**
- HTTP route handlers
- Stage-specific request/response schemas (used only by that stage)
- Template rendering
- Static assets

### Enforcement

A pre-commit hook (`no-cross-stage-imports`) enforces this rule. It will fail if any `src/stage_X` module imports from another `src/stage_Y` module.

To run manually:
```bash
grep -rn "from src\.stage_[0-9]" src/stage_* --include="*.py" | grep -v "from src\.stage_\([0-9]\).*src/stage_\1"
```

This should return no results.

## Domain Rules

### Whitespace is Semantically Significant

In ontological data harmonization, whitespace differences matter. Values like `"Lung Cancer"` vs `"Lung Cancer "` (trailing space) or `"Lung  Cancer"` (double space) may map to different ontology terms or indicate data quality issues. Do not trim or normalize whitespace when comparing original vs harmonized values.
