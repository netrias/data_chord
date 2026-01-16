# ADR 004: PV Override Protection

## Status
Accepted

## Context

The Data Chord harmonization pipeline uses AI to suggest transformations for data values. During Stage 3, original values are mapped to harmonized values, and PV (Permissible Value) validation checks whether the result conforms to the target ontology's allowed values.

**Problem Discovered**: When the user's original value is already a valid PV, but the AI suggests a *different* valid PV (e.g., different casing like "lung cancer" vs "Lung Cancer"), the system would replace the original with the AI suggestion. This is incorrect behavior because:

1. **Data integrity**: The user's data was already correct; "correcting" it introduces unnecessary changes
2. **Semantic significance**: Per domain rules, character differences (case, whitespace) are semantically meaningful
3. **Audit trail**: Unnecessary changes make it harder to trace what actually changed vs what was original

**Example**:
- Original value: `"lung cancer"` (valid PV)
- AI suggestion: `"Lung Cancer"` (also valid PV)
- **Before**: System would use `"Lung Cancer"` (AI wins)
- **After**: System keeps `"lung cancer"` (original preserved)

## Decision

### 1. Original-First Validation

The `compute_pv_adjustment()` function now checks if the original value is valid **before** considering the AI suggestion:

```python
def compute_pv_adjustment(...) -> PVValidationResult:
    # NEW: Check original value first
    if validate_against_pvs(original_value, pv_set):
        if original_value != top_harmonization:
            return PVValidationResult(
                is_conformant=True,
                adjusted_value=original_value,
                adjustment_source=AdjustmentSource.PV_OVERRIDE,
            )
        # Original matches AI - no adjustment needed
        return PVValidationResult(is_conformant=True, adjusted_value=None)

    # Original is not valid - proceed with AI suggestion...
```

### 2. New Adjustment Source Enum

Added `PV_OVERRIDE` to `AdjustmentSource` enum to distinguish when we reverted to the original value vs other adjustment scenarios:

```python
class AdjustmentSource(str, Enum):
    TOP_SUGGESTIONS = "top_suggestions"  # AI alternative used
    ORIGINAL = "original"                 # User explicitly reverted
    PV_OVERRIDE = "pv_override"          # Original was valid, AI overruled
```

### 3. Priority Order

The validation now follows this priority:

1. **Original value valid** → Keep original (PV_OVERRIDE if AI differed)
2. **AI suggestion valid** → Use AI suggestion (no adjustment)
3. **Alternative suggestion valid** → Use first valid alternative (TOP_SUGGESTIONS)
4. **Nothing valid** → Mark as non-conformant

## Consequences

### Positive
- **Data preservation**: Valid user data is never silently replaced
- **Audit clarity**: `PV_OVERRIDE` source in manifest clearly shows when AI was overruled
- **Domain rule compliance**: Respects whitespace/case significance per CLAUDE.md

### Negative
- **Stage 3 Summary Impact**: When all values in a column are PV_OVERRIDE'd, the summary shows "0 items changed" (addressed with CSS fix for card sizing)
- **Additional complexity**: One more adjustment source to track

### Trade-offs Accepted
- We accept that the AI's suggestion might be "more canonical" in some cases, but preserving user data takes precedence
- Users who want the AI's version can still manually override via the Stage 4 review UI

## Files Changed

**Domain Layer**:
- `src/domain/pv_validation.py` - Added `PV_OVERRIDE` enum, modified `compute_pv_adjustment()` logic

**Tests**:
- `tests/test_pv_override.py` - 10 unit tests covering:
  - Original valid, AI different → PV_OVERRIDE
  - Original valid, AI same → no adjustment
  - Original invalid, AI valid → use AI
  - Whitespace sensitivity
  - Case sensitivity
  - Edge cases (empty original, empty PV set, empty suggestions)

**UI Fixes** (related):
- `src/stage_3_harmonize/static/stage_3_harmonize.css` - Card sizing fix for "0 changes" columns
- `src/stage_4_review_results/` - Compact card design with "was:/now:" labels

## Related ADRs

- ADR 003: PV Manifest Persistence - Ensures PV data survives server restarts
