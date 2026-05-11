# Plan: XLSX Sheet Preview Popover (Stage 1)

## Problem Context

On Stage 1, the workbook tabs picker disambiguates XLSX worksheets by **name**, but in real datasets sheet names alone don't tell users what they're choosing — `Patients`, `Patients_v2`, and `Patients (final)` all look the same. The current custom tooltip (`#workbookTabTooltip` + `.workbook-tab-tooltip`, driven by `_showTabTooltip`/`_hideTabTooltip` in `stage_1_upload.js`) only shows the full name, and only when truncated. The data — the actual signal users need — is invisible.

**Architecture (Option B): lazy fetch + warm prefetch.**

- A new backend endpoint returns the first N rows × M columns of a single sheet for a given `file_id`.
- After upload, the frontend kicks off **low-priority background prefetches** (concurrency = 3, `requestIdleCallback`) for every sheet so hover almost always hits a warm cache.
- Hovering or focusing any tab opens a popover anchored above the tab. It shows the full sheet name in a header row, then a small data table. Cold cache → render skeleton with the name, then swap in data when the fetch resolves.
- The popover **replaces** the existing tooltip and appears for every tab (not only truncated names) because the preview is the value, not the name.
- The hidden `<select id="sheetSelect">` accessibility shim and Playwright contract are preserved — the popover never modifies selection state.

**Domain rule reminder:** every character is semantically significant. Cell values pass through verbatim — no trim, no normalize, no length cap. The `_normalize_sample` 80-char truncation used in column analysis is **not** reused on this path.

## File Impact

| File | Action | Scope |
|---|---|---|
| `src/domain/sheet_preview.py` | add | `SheetPreview` dataclass + `read_sheet_preview()` (~80 lines) |
| `src/stage_1_upload/schemas.py` | modify | add `SheetPreviewResponse` |
| `src/stage_1_upload/router.py` | modify | new `GET /sheet-preview` route + `_load_sheet_preview_safe` helper |
| `src/stage_1_upload/static/sheet_preview_popover.js` | add | popover + cache + prefetch (~150 lines) |
| `src/stage_1_upload/static/stage_1_upload.js` | modify | wire popover; remove tooltip code |
| `src/stage_1_upload/static/stage_1_upload.css` | modify | remove `.workbook-tab-tooltip*`; add `.sheet-preview-popover*` |
| `src/stage_1_upload/templates/stage_1_upload.html` | modify | swap tooltip for popover; add `sheetPreviewEndpoint` config |
| `tests/test_sheet_preview.py` | add | 20 cases (domain + HTTP) |
| `tests/e2e/app.e2e.spec.mjs` | modify | 3 new test cases (hover-renders, dedupe-and-reset, error-state) |

**Out of scope:** no changes to `upload_storage`, `analyze_columns`, the analyze pipeline, harmonization, manifests, or session storage.

## Domain Model

`@dataclass(frozen=True) class SheetPreview` in `src/domain/sheet_preview.py` is the canonical representation:

- `sheet_name: str` — echoed from request, verified against workbook
- `headers: list[str]` — exact strings from row 0; length == column count after truncation
- `rows: list[list[str]]` — 0..max_rows; **every row padded or truncated to `len(headers)`**
- `total_rows: int` — data row count BEFORE truncation, ≥ 0 (excludes header row)
- `total_columns: int` — column count BEFORE truncation, ≥ 0
- `truncated_rows: bool` — `total_rows > len(rows)`
- `truncated_columns: bool` — `total_columns > len(headers)`

**Caps:** backend default `max_rows=10`, `max_cols=12`. Route validates `1 ≤ max_rows ≤ 25`, `1 ≤ max_cols ≤ 30`. UI shows ~5 rows × ~6 columns; the extra headroom lets the popover grow later without a backend change.

**Cell coercion** (used inside `read_sheet_preview`): `None → ""`, all other types → `str(value)`. **No trim, no normalize, no length cap.** This is the only logic applied to cell content.

## Contracts

### Backend — `src/domain/sheet_preview.py`

```python
def read_sheet_preview(
    path: Path,
    *,
    sheet_name: str,
    max_rows: int = 10,
    max_cols: int = 12,
) -> SheetPreview
```

- `FileNotFoundError` if `path` does not exist.
- `ValueError` for non-XLSX paths or unknown `sheet_name` (message includes available worksheets, mirroring `_select_sheet`).
- Empty sheet → `headers=[], rows=[], total_rows=0, total_columns=0, truncated_*=False`.
- Single-cell sheet → `headers=[<value>], rows=[], total_columns=1, total_rows=0`.
- Reads only `max_rows + 1` rows × `max_cols` cols via `openpyxl.load_workbook(read_only=True, data_only=True)` + `iter_rows(values_only=True)`. Does **not** call `read_tabular`.
- Workbook always closed in a `try/finally`.

### HTTP — `GET /stage-1/sheet-preview`

Query params (FastAPI `Query`):
- `file_id`: `min_length=FILE_ID_MIN_LENGTH`, `pattern=FILE_ID_PATTERN` (re-use from `src/domain/schemas.py`)
- `sheet_name`: `min_length=1, max_length=255`
- `max_rows: int = 10`, `1 ≤ ≤ 25`
- `max_cols: int = 12`, `1 ≤ ≤ 30`

Response — `SheetPreviewResponse(BaseModel)`: `file_id, sheet_name, headers, rows, total_rows ≥ 0, total_columns ≥ 0, truncated_rows, truncated_columns`.

**Error mapping** (mirrors `analyze_dataset`):
- `404` — unknown `file_id`. Detail: `"Upload not found. Please upload again."`
- `410` — `file_id` known, file missing on disk (`FileNotFoundError`). Detail: `"Upload missing. Please upload again."`
- `400` — unknown sheet OR non-XLSX (`ValueError`). Detail mirrors the error message; CSV/TSV uploads return 400 with detail `"Sheet preview is only available for XLSX uploads."`.
- `422` — query param validation (FastAPI default).

### Frontend — `src/stage_1_upload/static/sheet_preview_popover.js`

Module-level cache:
```js
const _cache = new Map();
// key: `${file_id}|${sheetName}`
// value: { status: 'pending'|'ready'|'error', promise, data?, error? }
```

Exports:
- `initSheetPreviewPopover({ endpoint, popoverEl })` — call once on init
- `attachSheetPreviewHover(tabEl)` — wire `mouseenter`/`leave`/`focus`/`blur` to the tab
- `kickOffPreviewPrefetch(fileId, sheetNames)` — concurrency=3 via `requestIdleCallback` (fallback `setTimeout(0)`); no cancellation
- `hideSheetPreviewPopover()` — used by the strip's scroll listener
- `resetSheetPreviewCache()` — called from `_resetUploadState` and on every successful new upload

Behavior:
- `_loadSheetPreview(fileId, sheetName)` returns the in-flight `promise` if `pending`, the cached `data` if `ready`, or kicks off a fetch if `error`/missing. **Two near-simultaneous hovers on the same cold tab issue exactly one network request.**
- Popover renders `pending` → `ready` (or `pending` → `error`) without flicker. Header (sheet name) stays mounted across state changes; only body swaps.
- `error` copy: `410` → `"This upload expired. Re-upload to preview."`; `400`/`404`/network → `"Preview unavailable."` Next hover triggers one retry.
- All cell text inserted via `textContent` (XSS guarantee).
- Anchor: above the tab, centered, downward arrow. Position math copied from existing `_showTabTooltip` (clamped to `[8, innerWidth - width - 8]`).
- `Escape` while popover visible + tab focused → hide. `#sheetTabsStrip` scroll → hide.

### Window config

Template adds `sheetPreviewEndpoint: "{{ request.url_for('stage_one_sheet_preview') }}"` to `window.stageOneUploadConfig`.

## Implementation Steps

> **Test convention:** every test uses Given / When / Then comments and a negative assertion in the Given block (per project convention). Each test has a precise assertion message naming the contract clause it pins.

**Execution mode:** **async**. Subagent A (backend impl) + Subagent B (backend tests) + Subagent C (frontend impl) + Subagent D (e2e tests) run concurrently against the contracts above. A↔B converge on `SheetPreview` shape + error mapping; C↔D converge on the popover DOM contract (`#sheetPreviewPopover`, `data-sheet-name`, `.sheet-preview-table`, `.is-visible` class).

### Step 1 — Worklog entry (first, before any code)

Append today's entry to `~/.worklog/YYYY/MM/DD.md` per the global convention. Type `feature`, title from this plan's H1, Why/What sourced from the Problem Context.

### Step 2 — Backend tests (red)

Author all 20 cases in `tests/test_sheet_preview.py`. Use existing `tests/conftest.py` fixtures (`create_xlsx_content`, `temp_storage`, `app_client`, `upload_content`).

**Domain (no HTTP):**
1. Returns headers + first N rows for a normal 2-sheet workbook.
2. Preserves exact characters: leading/trailing spaces, doubled spaces, mixed case, accented chars survive byte-for-byte.
3. **Coerces cell types: a row of `[None, 42, 3.14, True, datetime(2024,1,1)]` becomes `["", "42", "3.14", "True", str(datetime(2024,1,1))]`** — no truncation applied to the long stringified datetime.
4. **Ragged rows are padded/truncated to `len(headers)`**: 3 headers, data rows of length 1 and 5, all emitted rows have `len == 3` (short padded with `""`, long truncated). Given asserts the raw rows have differing lengths.
5. Empty sheet → `headers=[]`, `rows=[]`, `total_rows=0`, `total_columns=0`, both `truncated_*` False.
6. Single-cell sheet → `headers=[value]`, `rows=[]`, `total_columns=1`, `total_rows=0`.
7. **Boundary: `total_rows == max_rows` → `truncated_rows=False`, `len(rows)==max_rows`** (off-by-one guard).
8. **Boundary: `total_rows == max_rows + 1` → `truncated_rows=True`, `len(rows)==max_rows`**.
9. Same boundary pair for columns (cols == max_cols and cols == max_cols + 1).
10. Wide sheet (50 cols, max_cols=12) → `headers` length 12, `truncated_columns=True`, `total_columns=50`.
11. Tall sheet (100 rows, max_rows=10) → `len(rows)==10`, `truncated_rows=True`, `total_rows=100`.
12. Unknown sheet → `ValueError` with available list.
13. Missing file → `FileNotFoundError`.
14. Non-XLSX path (`.csv`) → `ValueError`.

**HTTP (`@pytest.mark.asyncio`):**
15. 200 with valid file/sheet — full payload shape asserted.
16. 404 unknown `file_id`.
17. 400 unknown sheet (uploaded XLSX, queried sheet absent).
18. 422 `file_id` pattern violation; 422 `max_rows=999`.
19. 410 file deleted from disk after upload.
20. **CSV upload → 400 with detail containing `"XLSX"`** (pinned, not "likely 400").

### Step 3 — Backend implementation (green)

| Artifact | Tests it satisfies |
|---|---|
| `SheetPreview` dataclass | 1, 5, 6 |
| `read_sheet_preview` core path | 1, 2, 3, 4, 7–11 |
| Error raises in `read_sheet_preview` | 12, 13, 14 |
| `SheetPreviewResponse` + `Query` validation | 15, 18 |
| `_load_sheet_preview_safe` (error mapping) | 16, 17, 19, 20 |

3.1 `src/domain/sheet_preview.py` — dataclass + reader, using `openpyxl(read_only=True, data_only=True)` and `iter_rows(max_row=max_rows + 1, max_col=max_cols, values_only=True)`. Local `_cell_to_string` (no reuse of `_normalize_sample`).

3.2 `src/stage_1_upload/schemas.py` — `SheetPreviewResponse` with `total_rows: int = Field(ge=0)`, `total_columns: int = Field(ge=0)`.

3.3 `src/stage_1_upload/router.py` — route + `_load_sheet_preview_safe` mirroring `_analyze_columns_safe`'s error-mapping pattern.

3.4 Run `tests/test_sheet_preview.py` — green.

### Step 4 — Frontend tests (red)

Add three Playwright tests to `tests/e2e/app.e2e.spec.mjs`:

**4.1 `XLSX sheet preview popover renders headers and first rows on hover`**
- Given a multi-sheet XLSX uploaded; the popover is hidden (precondition assertion).
- When hovering the `Patients` tab.
- Then `#sheetPreviewPopover` has `is-visible`; `#sheetPreviewPopoverTitle` text equals the sheet name; first `<thead th>` text matches the workbook's first header.
- Then `mouseleave` removes `is-visible`.
- Negative invariant: `#sheetSelect.value` unchanged through the whole flow.

**4.2 `Hover on cached preview after prefetch fires no extra network request` (cache dedupe + warm prefetch)**
- Use `page.route('**/stage-1/sheet-preview*', ...)` to count calls.
- Given the upload completed and ~500 ms passed (prefetch settles).
- When hovering `Patients` then `Diagnoses` and back to `Patients`.
- Then total intercepted calls equals `state.sheetNames.length` (one per sheet from prefetch); zero new calls from the hovers.
- Then re-uploading a fresh file resets the cache: hovering a tab in the new workbook produces a new request even if the sheet name matches a previous workbook's sheet.

**4.3 `Popover error state renders specific copy on 410` (error-state)**
- Given an upload completed; route `**/stage-1/sheet-preview*` is intercepted to return 410.
- When hovering a tab.
- Then popover body text contains `"This upload expired"`; `#sheetSelect.value` unchanged.

### Step 5 — Frontend implementation (green)

5.1 Template: replace `#workbookTabTooltip` with the `#sheetPreviewPopover` structure (header + body slot); add `sheetPreviewEndpoint` to config.

5.2 CSS: delete `.workbook-tab-tooltip*` rules; add `.sheet-preview-popover` (fixed positioning, z-index 9999, max-width `min(440px, 92vw)`, opacity transition), `.is-visible`, `::after` arrow, `.sheet-preview-popover-header`, `.sheet-preview-popover-body`, `.sheet-preview-table`, `.sheet-preview-skeleton` shimmer, `.sheet-preview-error`, `.sheet-preview-footer` truncation hint. Mirror existing `prefers-reduced-motion` overrides.

5.3 New module `src/stage_1_upload/static/sheet_preview_popover.js` — exports listed in Contracts. All cell text via `textContent`. Anchoring math reuses the proven `_showTabTooltip` algorithm.

5.4 Edit `stage_1_upload.js`: import + initialize the module in `_init`, replace tooltip listeners with `attachSheetPreviewHover(tab)` in `_buildSheetTab`, call `kickOffPreviewPrefetch` at the end of `_renderSheetSelector` when `sheetNames.length > 1 && state.uploaded?.file_id`, call `resetSheetPreviewCache()` in `_resetUploadState` and on successful new upload, swap the `#sheetTabsStrip` scroll listener to `hideSheetPreviewPopover`.

5.5 Run `just test`, `just test-e2e`, `just lint`, `just typecheck`, `just js-check` — all green.

### Step 6 — Cleanup

- `grep -rn "workbookTabTooltip\|workbook-tab-tooltip\|_showTabTooltip\|_hideTabTooltip" src/ tests/` returns zero hits.
- The existing `'XLSX flow selects a worksheet…'` Playwright test still passes.
- CSV upload still works end-to-end (no XLSX-only regression).
- `grep -nR "from src.stage_1_upload" src/domain/ src/stage_*/` (other stages) returns zero hits.

## Proving

1. `just test` green — including all 20 new cases.
2. `just test-e2e` green — including the 3 new Playwright cases and the existing XLSX flow test.
3. `just lint`, `just typecheck`, `just js-check` clean.
4. **Manual smoke:** upload an XLSX with `Patients` / `Patients_v2` / `Patients (final)`. Hover each — popover renders within a frame after prefetch settles (≤ 1s on local). Click selection still works. Hidden `<select>` value matches the visual selection.
5. **Cross-stage rule:** pre-commit `no-cross-stage-imports` hook passes; `grep -nR "from src.stage_1_upload" src/domain/` returns zero.
6. **Domain-rule guardrail:** test 2 + test 3 specifically pin verbatim character preservation and type-coercion rules.
7. **Network behavior:** with DevTools throttled to "Slow 3G", after upload one sees ≤ 3 in-flight `/stage-1/sheet-preview` requests at any moment. Hovering a prefetched tab fires no new request (asserted by Playwright in test 4.2).

---

Plan reviewed via `plan-tests`; recommendations folded in (Given/When/Then convention, type-coercion test, ragged-row test, boundary tests for `truncated_*`, dedupe + reset e2e, error-state e2e, pinned CSV→400 outcome).
