# ADR 002: Row Context Popup for Verification

## Status
Accepted

## Context

During Stage 4 (Review Results), users review harmonized values in "Column Mode" where entries are grouped by original value. Each entry shows how many spreadsheet rows contain that value (e.g., "6 rows"). However, users had no way to see the surrounding spreadsheet context - which other column values appeared in those rows.

This context is important for verification because:
- Users want to sanity-check that the harmonization makes sense given neighboring cell values
- Some transformations may appear incorrect in isolation but are valid when context is visible
- Original spreadsheet structure helps users understand data provenance

## Decision

### 1. On-Demand Row Context Fetching

We added a new endpoint that fetches original CSV rows by index:

```
POST /stage-4/row-context
{
  "file_id": "abc123...",
  "row_indices": [0, 5, 12]  // 0-based indices
}

Response:
{
  "headers": ["col1", "col2", ...],
  "rows": [["val1", "val2", ...], ...]
}
```

**Why on-demand**: Loading all original rows into the review state would significantly increase payload size and memory usage. Most users only inspect a few entries, so lazy loading is more efficient.

**Why POST**: The row indices array can be large (hundreds of indices for high-frequency values), which exceeds practical URL length limits for GET requests.

### 2. Progressive Loading

For entries appearing in many rows (>20), we:
1. Initially load only the first 20 rows
2. Show a "Load all N rows" button to fetch remaining rows
3. This prevents large payloads for common values like "Not Reported"

### 3. Column Highlighting with BOM Handling

The popup highlights the source column being reviewed. However, CSV files often have a UTF-8 BOM (Byte Order Mark, U+FEFF) prepended to the first header, which breaks exact string matching.

**Solution**: Normalize strings by stripping BOM before comparison:
```javascript
function _normalizeForComparison(str) {
  return str.replace(/^\uFEFF/, '').trim();
}
```

This normalization is only used for UI display matching, not for the actual data values (which preserve semantic differences per domain rules).

### 4. Horizontal Scroll UX

Spreadsheets often have many columns. To improve navigation:
- Clicking the column name in the title scrolls the table to center on that column
- Manual scroll calculation (not `scrollIntoView`) for reliable behavior in dialog containers
- Vertical mouse wheel converts to horizontal scroll when no vertical overflow exists

## Consequences

### Positive
- Users can verify transformations against original spreadsheet context
- Efficient loading pattern prevents memory bloat for high-frequency values
- Column highlighting helps users quickly locate the relevant data
- Works correctly with BOM-prefixed CSV files

### Negative
- Additional API call required to view context (not embedded in review data)
- Slight latency when opening popup for the first time

### Risks Mitigated
- **XSS**: All values escaped via `escapeHtml()` before HTML insertion
- **DoS**: Row indices validated (non-negative integers only); out-of-bounds indices filtered
- **Memory**: Progressive loading limits initial payload size

## Files Changed

**Backend**:
- `src/stage_4_review_results/router.py` - New `/row-context` endpoint
- `src/stage_4_review_results/schemas.py` - `RowContextRequest`, `RowContextResponse`

**Frontend**:
- `src/stage_4_review_results/static/row_context_popup.js` - New module for popup dialog
- `src/stage_4_review_results/static/review_mode_column.js` - Click handler on row labels
- `src/stage_4_review_results/static/stage_4_review.css` - Dialog and table styles
