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

## Module Docstrings

A module docstring answers: **"Does my new code belong here?"**

Name the **axis of change** (what requirements cause this to change?) and the **constraint** (what property is preserved?). Use specific nouns/adjectives that embed the boundary test. See user CLAUDE.md for detailed examples and boundary test methodology.

| Vague | Bounded |
|-------|---------|
| "Storage utilities" | "JSON-serialized typed storage for review state" |
| "Validation helpers" | "Pure functions for validating against PV sets" |

Don't list what the module doesn't do - the boundary test handles exclusion implicitly.

## Function Comments (Project-Specific Examples)

See user CLAUDE.md for full function comment guidelines. Project-specific examples:

| Good (why) | Bad (what) |
|------------|------------|
| `"""Whitespace-sensitive comparison (per domain rules)."""` | `"""Get a CDE by its ID."""` |
| `"""Row keys are 1-indexed to match Stage 4 UI numbering."""` | `"""Convert table rows to ManifestRow."""` |
| `"""Returns frozenset for O(1) membership testing."""` | `"""Fetch PVs and return them."""` |
| `"""Continues on individual failures (graceful degradation)."""` | `"""Loop through CDE keys."""` |

## Domain Rules

### Whitespace is Semantically Significant

In ontological data harmonization, whitespace differences matter. Values like `"Lung Cancer"` vs `"Lung Cancer "` (trailing space) or `"Lung  Cancer"` (double space) may map to different ontology terms or indicate data quality issues. Do not trim or normalize whitespace when comparing original vs harmonized values.
