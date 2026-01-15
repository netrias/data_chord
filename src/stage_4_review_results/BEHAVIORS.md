# Stage 4 Review - Behavior Tracking

Running document of implemented and planned behaviors for the Stage 4 verification interface.

---

## Value Card States

### Override States
- **No override**: Card shows original value → AI recommendation normally
- **Has override**:
  - AI suggestion shows with strikethrough
  - Override value shows below in magenta
  - Original value and AI suggestion become clickable revert links

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

## Clickable Revert Links (Implemented)

When a card has an override that differs from the AI suggestion:

1. **Original value** becomes clickable (dashed underline)
   - Click → sets override to original value
   - Clears the input/combobox (override was set via click, not typed)
   - Triggers conformance check (may show warning if non-conformant)

2. **AI suggestion** (strikethrough) becomes clickable
   - Click → clears override entirely
   - Clears the input/combobox
   - Returns card to normal AI-corrected state

Both links only appear when there's an active non-matching override.

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
- Row mode: 2, 4, 6, 8 rows per batch

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
