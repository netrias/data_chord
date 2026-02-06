# Data Chord - Project Instructions

## Tech Stack

- **Backend**: FastAPI + Uvicorn (port 8000)
- **Templates**: Jinja2 (server-rendered HTML)
- **Frontend**: Vanilla JavaScript + HTMX, TypeScript for type checking
- **Testing**: pytest, pytest-asyncio, Playwright (E2E), Hypothesis (property-based)
- **Code quality**: Ruff (linting/formatting), basedpyright (type checking), bandit (security)
- **Package management**: uv (Python), npm (JS/Playwright)

## Running the App

```bash
just sync              # Install dependencies (uv sync --extra dev)
just app-reload        # Dev server with auto-reload (port 8000)
just app               # Production server (port 8000)
just test              # Python tests (excludes E2E)
just test-e2e          # E2E tests (Playwright)
just lint              # Ruff linting
just typecheck         # basedpyright
just js-check          # JS syntax check
```

## Environment Variables

The `.env` file is gitignored and contains API keys — never commit it. Symlinked from `~/.config/data_chord/.env`.

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

A pre-commit hook (`no-cross-stage-imports`) enforces the stage independence rule.

## Function Comments (Project-Specific Examples)

See global CLAUDE.md for full guidelines. Project-specific examples:

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
