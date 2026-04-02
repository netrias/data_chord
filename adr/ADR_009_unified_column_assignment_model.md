# ADR 009: Canonical Column Assignment Model

## Status
Accepted

## Context

The application currently represents column-to-CDE decisions in several different ways:

- Stage 3 resolves `ManifestPayload` plus manual overrides into ad hoc dicts
- `SessionCache` stores primitive `dict[int, str]` column-to-CDE mappings
- PV persistence serializes those primitive mappings directly to disk
- Stage 4 and Stage 5 still mix stable `column_id` with name-based grouping and dedupe

This fragmentation makes duplicate-header behavior hard to reason about and encourages
new logic to pass column identity around as loose primitives.

## Decision

### 1. Introduce One Canonical Domain Model

Resolved column mapping decisions are represented by:

```python
@dataclass(frozen=True)
class ColumnAssignment:
    column_id: int
    column_name: str
    cde_key: str | None
```

This is the canonical in-memory representation for PV validation and review routing.

### 2. Keep the Canonical Model Minimal

`ColumnAssignment` intentionally does **not** include:

- `source` provenance such as `manifest` or `manual_override`
- `cde_id`

Provenance belongs to reporting and download artifacts, not PV validation routing.
`cde_id` remains derivable from the CDE cache when a downstream boundary needs it.
Keeping the assignment model to `column_id`, `column_name`, and `cde_key` keeps
manifest/override resolution as a pure transformation.

### 3. Treat Existing Payload Shapes as Boundary DTOs

The following remain boundary representations rather than canonical domain models:

- `ManifestPayload`
- `ColumnMappingEntry`
- Stage 4 and Stage 5 Pydantic response schemas

They are converted to or from `ColumnAssignment` at module boundaries.

### 4. Defer Retirement of `ColumnMappingSet`

`ColumnMappingSet` remains in place for the harmonizer input contract during this refactor.
Its retirement is explicitly deferred so this change can focus on column identity,
PV persistence, and review-stage correctness without widening scope into harmonizer API redesign.

### 5. Migrate Cache and Persistence Atomically

`SessionCache` and PV manifest persistence change in the same refactor step.
The persisted PV manifest moves from raw `column_to_cde_key` dicts to explicit
assignment snapshots, with a legacy reader for old manifests.

## Consequences

### Positive

- One source of truth for resolved column assignments
- Stable column identity flows through cache and review stages
- Fewer ad hoc conversions between manifest payloads, overrides, cache state, and persistence
- Cleaner duplicate-header handling in Stage 4 and Stage 5

### Negative

- Short-term compatibility code is needed for legacy PV manifests
- `ColumnMappingSet` remains temporarily, so the codebase still has one legacy mapping type at a boundary

## Follow-up

- Consider retiring `ColumnMappingSet` once the harmonizer input contract can accept canonical assignments directly
- Revisit whether review override persistence should also move to stable column-id keys end to end
