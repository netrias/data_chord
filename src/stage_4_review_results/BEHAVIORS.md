# Stage 4 Review - Behavior Tracking

Running document of implemented and planned behaviors for the Stage 4 verification interface.

---

## Value Card States

### Override States
- **No override**: Card shows "was:" (original) → "now:" (AI recommendation)
- **Has override**:
  - Card shows "was:" (original) → "now:" (override value)
  - Revert button restores the AI suggestion

### PV Conformance States
- **PV-conformant**: Green header (`--netrias-100` background, `--netrias-300` border)
- **PV-non-conformant**: Warning icon (⚠) in header, default gray header
- Conformance is checked against the *active* value (override if present, else AI suggestion)

### Confidence Indicators
- High: Up caret (▲) - AI is confident in this transformation
- Medium: Dash (–) - Reasonable match, review suggested
- Low: Down caret (▼) - AI is uncertain, manual review recommended

Confidence indicators show a tooltip on hover explaining the confidence level.

---

## Revert Button

When a card has an override that differs from the AI suggestion, a revert button appears in the "was:" context area. Clicking it restores the original value into the input, triggering a conformance check.

---

## Settings Modal

### Review Mode
- Column: Groups entries by column, batches unique original values
- Row: Shows rows with changes, one row per card group

### Sort Mode
- Original order
- Confidence · Lowest first
- Confidence · Highest first

### Grid Size
- Column mode: 3×3, 4×4, 5×5 grids
- Row mode: 5, 10, 15 rows per batch

### Filters
- [x] Hide case-only changes (default: checked)

---

## Navigation

- Batch progress pills show current position
- Previous/Next batch buttons
- State persisted to server (review mode, sort, position, overrides)

---

## Planned / To Investigate

- [ ] (Add future behaviors here as they come up)

---

## Edge Cases

- When original value === AI suggestion: clicking original does nothing (no-op)
- Empty original values are skipped (nothing to review)
- Whitespace is semantically significant (not trimmed)
- Warning icon is always present in DOM when PVs exist (hidden when conformant) to allow dynamic show/hide on revert
