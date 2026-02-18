# ADR 007: AI Output Whitespace Trimming

## Status

Accepted

## Context

The AI harmonization service occasionally returns values with leading/trailing
whitespace (e.g., `" Lung Cancer "` instead of `"Lung Cancer"`). This artifact
whitespace causes two problems:

1. **Silent conformance failure**: `check_value_conformance()` uses exact set
   membership (`value in pv_set`), so `" Lung Cancer "` fails against PV
   `"Lung Cancer"`. The value appears non-conformant.

2. **Invisible in the UI**: HTML `<input>` elements visually trim whitespace,
   so Stage 4 shows "Lung Cancer" with a non-conformant warning icon — but the
   user cannot see why. The whitespace is there but invisible.

### Core domain rule preserved

All character differences are semantically significant (per project CLAUDE.md).
Whitespace in *user data* (`to_harmonize`) is meaningful. Whitespace in *AI
output* is an artifact, not a semantic distinction. The fix targets only AI
output.

## Decision

### 1. Strip AI output at the manifest reader boundary

In `_extract_row()` in `src/domain/manifest/reader.py`, strip
`top_harmonization` and each item in `top_harmonizations` after extraction from
parquet. This is the earliest shared boundary — all stages read through
`read_manifest_parquet()`, so both new and legacy data are fixed.

`to_harmonize` (user data) is NOT stripped.

### 2. Show whitespace markers in the UI

In the Stage 4 "was:" display, render leading/trailing whitespace as visible
middle-dot characters (`·`) using `<span class="ws-marker">` elements. This
lets users see why their original value differs from the corrected value.

### What stays unchanged

- `validate_against_pvs()` — exact match
- `check_value_conformance()` — exact match
- `compute_pv_adjustment()` — unchanged; works correctly with clean AI input
- `find_conformant_suggestion()` — unchanged
- `card-state.js` — client-side conformance stays exact

The fix is upstream (clean the data at the reader boundary), not downstream
(loosen the matching logic).

## Known Limitations

Whitespace markers in the UI render only space characters as `·`. Other
whitespace characters (tabs, newlines) are stripped by `.strip()` at the reader
but are not individually marked in the UI. This is acceptable because AI output
whitespace is virtually always spaces.

## Alternatives Considered

- **Trim at the writer**: Would fix new data but not legacy manifests already
  persisted with whitespace.
- **Trim in generic `_get_string` helper**: Would also trim `to_harmonize`,
  `column_name`, etc. — violates the domain rule that user data whitespace is
  significant.
- **CSS `white-space: pre`**: Preserves whitespace visually but is too subtle —
  users may not notice an extra gap.
- **Trimmed fallback in conformance checking**: Would make whitespace-padded
  values appear conformant, hiding the data quality issue instead of fixing it.
