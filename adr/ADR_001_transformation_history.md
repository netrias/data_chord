# ADR 001: Transformation History and Save-on-Blur

## Status
Accepted

## Context

Stage 5 (Review Summary) displays a table of all value transformations that occurred during harmonization. Users needed to understand *how* a value was transformed - whether it was an AI suggestion, a manual override, or a system adjustment for PV conformance.

Additionally, Stage 4's save mechanism was triggering on every keystroke (debounced at 500ms), which created multiple redundant history entries in the manifest when users typed slowly or paused during input.

## Decision

### 1. Transformation History in Stage 5

We extended the Stage 5 summary API to include transformation history for each term mapping:

**New schema** (`TransformationStep`):
```python
class TransformationStep(BaseModel):
    value: str
    source: str  # "original", "ai", "user", "system"
    timestamp: str | None = None
    user_id: str | None = None
```

**Extended `TermMapping`**:
```python
class TermMapping(BaseModel):
    column: str
    original_value: str
    final_value: str
    is_pv_conformant: bool = True
    history: list[TransformationStep] = []  # NEW
```

The history is built from the manifest's existing data:
- `to_harmonize` → "original" step
- `top_harmonization` → "ai" step (if different from original; includes any PV adjustments applied in Stage 3)
- `manual_overrides` list → "user" steps with timestamps and user IDs

**Deduplication**: Consecutive overrides with the same value are collapsed to avoid cluttering the history with entries created by rapid saves.

### 2. Save-on-Blur in Stage 4

Changed the override save trigger from debounced keystrokes to blur events:

**Before**: `input` event → debounce 500ms → `saveOverrides()`
**After**: `input` event → update local state only; `blur` event → `saveOverrides()`

This ensures:
- One history entry per intentional edit (focus loss indicates user moved on)
- No duplicate entries from typing pauses
- Combobox selections still save immediately (intentional action)

### 3. Clickable History Dialog

Table rows in Stage 5 are now clickable, opening a modal dialog that shows:
- Visual timeline of transformation steps
- Color-coded indicators by source type
- Timestamps and user IDs for manual overrides
- Keyboard accessible (tabindex, Enter/Space handlers)

## Consequences

### Positive
- Users can audit the full transformation path for any value
- Manual overrides show who made them and when
- Reduced manifest bloat from duplicate override entries
- Improved accessibility with keyboard navigation

### Negative
- Slightly larger API response payload (history arrays)
- Additional complexity in Stage 5 router (`_build_history`, `_MappingInfo`)

### Risks Mitigated
- XSS: All user-controlled values are escaped before HTML insertion
- Invalid timestamps return null instead of being displayed raw

## Files Changed

**Backend**:
- `src/stage_5_review_summary/router.py` - TransformationStep schema, history building

**Frontend (Stage 5)**:
- `src/stage_5_review_summary/static/stage_5_review.js` - Dialog creation, click/keyboard handlers
- `src/stage_5_review_summary/static/stage_5_review.css` - Dialog and timeline styles

**Frontend (Stage 4)**:
- `src/stage_4_review_results/static/stage_4_review.js` - Removed debounce, pass onSave callback
- `src/stage_4_review_results/static/shared_review_utils.js` - Blur handler for text inputs
- `src/stage_4_review_results/static/review_mode_column.js` - Updated renderEntries signature
- `src/stage_4_review_results/static/review_mode_row.js` - Updated renderEntries signature
