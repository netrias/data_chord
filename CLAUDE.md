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

### All Character Differences Are Semantically Significant

**This is the core value proposition of the application.** In ontological data harmonization, the exact character sequence matters for mapping to ontology terms. Never normalize, trim, or perform case-insensitive comparisons when checking value conformance.

**Semantically significant differences include:**
- **Case**: `"Lung Cancer"` vs `"lung cancer"` are different values
- **Whitespace**: `"Lung Cancer"` vs `"Lung Cancer "` (trailing space) vs `"Lung  Cancer"` (double space)
- **Punctuation**: `"Lung-Cancer"` vs `"Lung Cancer"` vs `"Lung, Cancer"`
- **Diacritics**: `"café"` vs `"cafe"`

**Implementation requirements:**
- Use strict equality (`===` in JS, `==` in Python) for value comparisons
- Use `Set.has()` (JS) or `in frozenset` (Python) for PV conformance checks - both are case-sensitive
- Never use `.toLowerCase()`, `.upper()`, `.strip()`, or similar normalization when checking conformance
- The only exception is UI search/filtering within dropdowns, where case-insensitive matching improves UX

**Why this matters:**
A value like `"Lobular and ductal carcinoma"` is NOT the same as the PV `"Lobular And Ductal Carcinoma"`. If the user reverts to the original (lowercase) value, it MUST show as non-conformant with a warning icon, because the ontology requires the exact canonical form.
