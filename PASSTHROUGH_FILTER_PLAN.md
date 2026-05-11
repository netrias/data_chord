# Add Pass-through filter chip to Stage 2

## Problem Context

Stage 2's column-mapping list has a row of source-filter chips (All / AI Recommendation / Override / No Mapping) that lets a reviewer narrow the list by *where the current mapping came from*. The four chips today form a partition over the column set: every column has exactly one effective status, so the chip counts sum to the total. Pass-through CDEs — targets that have no permissible-value list and let values flow through unchanged — are visually distinct on each row (a slate `↪` glyph in the fit cell) but cannot be isolated or skipped from the list. A reviewer who wants to triage a batch of pass-throughs in isolation, or who wants to confirm at a glance "how many of my columns map to pass-through targets," has no way to do that today.

The fix is a fifth chip — "Pass-through" — added to the existing source-filter row. We chose this control after comparing five alternatives in `prototypes/passthrough-filter-controls.html`. The decision accepts that the new chip filters on a *different axis* (target CDE *type*) than the existing four (mapping *source*). That mixing is real but, on consideration, low-cost: each chip's count still correctly equals the number of rows shown when it is selected (with the search box empty), the "chips form a partition" property is invisible to users in practice, and per-row status icons continue to convey source even when the pass-through chip is active. The composability we lose ("AI Rec ∩ Pass-through") is not a real review workflow. The alternatives that preserved axis purity (second chip row, type segmented control, filters dropdown) all added more chrome than the use case warranted.

The layer involved is the Stage 2 frontend: a vanilla-JS module (`stage_2_mappings.js`) that owns filter state and renders the chip bar and the row list, and the CSS file that styles each chip's active treatment. The change is narrow because the existing filter pipeline already has the right shape — `_passesSourceFilter` is a single predicate that the rest of the rendering pipeline composes through. Adding a new filter value extends the predicate; nothing further upstream or downstream changes. No backend, schema, or domain-service change is required, because pass-through-ness is a property of the target CDE in the catalog (`cde_type`) which the frontend already loads at startup.

The `SOURCE_FILTER` constant name becomes slightly misleading once one of its values is a type filter rather than a source filter. We deliberately do **not** rename it — the constant is module-private and renaming touches seven call sites for zero behavioral benefit. Instead we carry the dual-axis intent in a why-comment on the constant declaration. (Earlier draft of this plan proposed `LIST_FILTER`; review flagged the churn risk as unjustified and the comment-only approach as preferable.)

**Stale-takeover-on-filter** is a pre-existing edge case worth naming: if a user opens a takeover on column X then activates a filter that excludes X, `navigate()` falls back to `findIndex(... → -1)` and prev/next behave oddly. This change does not introduce that bug — it exists today for the four current chips — but Pass-through is a small bucket in typical inputs and will hit the case more often. We accept it as out of scope here; a separate fix can address takeover lifecycle when the active row falls out of the filtered list.

## File Impact

| File | Action | Scope |
|------|--------|-------|
| `src/stage_2_review_columns/static/stage_2_mappings.js` | modify | add `SOURCE_FILTER.PASSTHROUGH`; add `_isPassthroughColumn` helper; add a branch to `_passesSourceFilter`; add chip entry + count to `renderSourceFilter` (single-pass count loop); why-comment on `SOURCE_FILTER` documenting the dual-axis nature |
| `src/stage_2_review_columns/static/stage_2_mappings.css` | modify | add `.mapping-ico--passthrough` color rule + `.mapping-filter-btn--passthrough.active` rule mirroring the slate (`--gray-200` / `--gray-700`) tint already used by `.type-badge--passthrough` |
| `tests/e2e/app.e2e.spec.mjs` | modify | add one Playwright test: seeds Stage 2 with four columns covering all chip combinations, asserts the chip is visible with the right count, asserts clicking it narrows the list to pass-through-mapped columns only (in CSV order), asserts override-to-passthrough flips the count |

No file additions or deletions.

## Domain Model

**Source-filter chips** are a single-select narrowing control over the Stage 2 column-mapping list. They previously held three categorical values plus an "all" reset, drawn from a single axis — *effective mapping source* (`REC` / `OVR` / `NONE`). The chips are also the legend: each chip carries the same status icon used on the row, so the bar doubles as a key for the iconography.

This change introduces a **second axis** to the chip bar — *target CDE type* — represented by a single value, `PASSTHROUGH`. A column is "pass-through" iff its **effective target CDE** (per `_effectiveCde`, which honors the user's override and resolves No-Mapping overrides to `null`) exists in the catalog and the catalog reports `cde_type === 'passthrough'`.

**Override semantics fall out of using `_effectiveCde`**:
- Column originally mapped to a PV CDE, user overrides to a pass-through CDE → **counts as pass-through**.
- Column originally mapped to a pass-through CDE, user overrides to a PV/numeric CDE → **does not count**.
- Column originally mapped to a pass-through CDE, user overrides to No Mapping → **does not count** (`_effectiveCde` returns `null`).
- Unmapped columns (no AI rec, no override) → **do not count**.

The chips no longer form a partition. The `ALL` count remains the total column count; `REC + OVR + NONE` still sum to that total (they partition by source); `PASSTHROUGH` cuts across all three and overlaps freely. **Each chip count equals the number of rows shown when that chip is selected and the search box is empty** — consistent with the existing chip semantics, which already do not reflect the search filter.

**Single source of truth for CDE type**: the catalog (`cdeCatalog` / `cdeByKey`), populated once from `config.cdeCatalog` at module load. The takeover-level code reads CDE type from per-column detail responses with catalog as a fallback, but that is for the takeover (which loads detail lazily). The list view does not load detail for every column and must read from the catalog. This is consistent with how `_overlapCellHtml` already decides whether a row gets the pass-through glyph: catalog lookup, no detail.

The ubiquitous term **"pass-through"** is already established (badge label, fit-cell glyph, takeover type card). This change just exposes it as a filter dimension; no new vocabulary is introduced.

## Contracts

```js
// In src/stage_2_review_columns/static/stage_2_mappings.js:

// SOURCE_FILTER values now span two axes — mapping source (REC/OVR/NONE) and
// target CDE type (PASSTHROUGH). Single-select narrowing is the shared
// semantics; the constant name is kept for module-internal stability.
const SOURCE_FILTER = {
  ALL: 'all',
  REC: TAG.REC,
  OVR: TAG.OVR,
  NONE: TAG.NONE,
  PASSTHROUGH: 'passthrough',
};

// Catalog-backed predicate. Delegates to _effectiveCde so user overrides
// (including override-to-NoMapping) are honored — null effective CDE returns
// false, otherwise looks up cdeByKey.get(cde)?.type === 'passthrough'.
// List view does not load per-column detail, so the catalog is authoritative,
// matching how _overlapCellHtml renders the pass-through fit glyph today.
const _isPassthroughColumn = (column) => boolean

// Extended _passesSourceFilter. Dispatch:
//   ALL                  → true
//   REC | OVR | NONE     → _effectiveStatus(column) === filter   (source axis)
//   PASSTHROUGH          → _isPassthroughColumn(column)          (type axis)
const _passesSourceFilter = (column) => boolean

// renderSourceFilter gains a fifth chip entry. Counts are computed in a single
// fused pass over cols (extend the existing for-loop with one extra increment
// for _isPassthroughColumn(c)), so the pass-through count is independent of
// the REC/OVR/NONE source counts (it overlaps with all three).
const renderSourceFilter = () => void
```

No state-shape changes; `state.sourceFilter` continues to hold one of the `SOURCE_FILTER` values.

## Implementation Steps

Linear execution. Single small surface, no parallelizable work.

1. **Write worklog entry** — `~/.worklog/2026/04/30.md`. Use the Problem Context above for the Why and What fields.

2. **Test (red)** — add `tests/e2e/app.e2e.spec.mjs::Stage 2 pass-through filter chip`. Seed a payload with **four columns** covering each chip cell: one PV-mapped (`dx` → `dx_cde:pv`), one numeric-mapped (`age` → `age_cde:numeric`), one pass-through-mapped (`notes` → `notes_cde:passthrough`), one with no AI target (`junk`, no entry in `cde_targets`). Expected chip counts: All=4, REC=3, OVR=0, NONE=1, Pass-through=1. Assertions:
   - All five chips are visible with the counts above.
   - Clicking "Pass-through" narrows `#mappingRows` to exactly the `notes` row.
   - Clicking "All" restores all four rows in CSV order: `dx`, `age`, `notes`, `junk`.
   - **Override case**: programmatically apply an override that maps `dx` → `notes_cde` (the pass-through CDE), re-render; assert the Pass-through chip count is now 2 and that clicking it shows both `dx` and `notes`. (Use the same override mechanism the existing override tests use — `state.overrides` populated via the picker, or directly if the test infrastructure supports it.)

   Run the test, confirm it fails on the first chip-visibility assertion (chip not yet present).

3. **Implementation: extend filter** — in `stage_2_mappings.js`:
   - Add why-comment above `SOURCE_FILTER` declaration: dual-axis nature; values span source and type.
   - Add `PASSTHROUGH: 'passthrough'` to `SOURCE_FILTER`.
   - Add `_isPassthroughColumn(column)` helper near other domain helpers (after `_effectiveStatus`); body: `const cde = _effectiveCde(column); return !!cde && cdeByKey.get(cde)?.type === 'passthrough';`. Why-comment: catalog (not detail) is the source of truth for the list view, matching `_overlapCellHtml`.
   - Extend `_passesSourceFilter`: branch on `state.sourceFilter`. For `PASSTHROUGH`, return `_isPassthroughColumn(column)`. For everything else, keep current behavior.
   - In `renderSourceFilter`, fuse the pass-through count into the existing `for (const c of cols)` loop (add `if (_isPassthroughColumn(c)) counts.passthrough += 1;`). Initialize `counts.passthrough = 0`. Add a fifth `items` entry: `{ key: SOURCE_FILTER.PASSTHROUGH, label: 'Pass-through', icon: '↪', iconCls: 'mapping-ico--passthrough', count: counts.passthrough }`.

4. **Implementation: chip styling** — in `stage_2_mappings.css`:
   - Add `.mapping-ico--passthrough { color: var(--gray-700); }` to mirror the existing slate convention.
   - Add `.mapping-filter-btn--passthrough.active { background: var(--gray-200); border-color: var(--gray-700); color: var(--gray-900); }` — mirrors `.type-badge--passthrough`.
   - Add `.mapping-filter-btn--passthrough.active .mapping-filter-count { color: var(--gray-700); }`.

5. **Test (green)** — re-run the new e2e test; should pass. Run the full Stage 2 e2e suite to confirm no regressions in the existing filter behavior, takeover, override, or harmonize flows.

6. **Visual verification** — use the `dev-browser` skill: load Stage 2 with a real CSV that contains pass-through-mapped columns, click each of the five chips in turn, confirm the list narrows correctly each time and that the chip's active treatment reads correctly (slate, parallel to the other chips, distinguishable). Confirm CSV input order is preserved across all filter values.

## Proving

- **Real-world (browser, dev server)**: `just app-reload`, navigate to Stage 2 with a real CSV that has pass-through targets. Click "Pass-through" chip — confirm only pass-through-mapped rows appear, in CSV order. Click each other chip — confirm normal filter behavior is unchanged. Override a non-pass-through column to a pass-through CDE — confirm Pass-through count increments live.
- **E2E**: `just test-e2e tests/e2e/app.e2e.spec.mjs` — new test must pass; existing Stage 2 tests must still pass.
- **JS syntax check**: `just js-check` on modified files. (Lint/typecheck are Python tools and don't apply to this change.)
- **Side-effect check**: confirm the takeover navigates only within the filtered set under an active "Pass-through" filter — open a pass-through row, walk prev/next, confirm only pass-through rows are visited. (The pre-existing stale-takeover-when-active-row-filtered-out behavior is acknowledged as out of scope; not regression-tested here.)

---

Plan reviewed via `/review plan` — eight issues raised, all addressed: tightened `_isPassthroughColumn` contract to delegate to `_effectiveCde`; documented override semantics + added override test case; explicitly accepted stale-takeover edge case as out of scope; dropped the `SOURCE_FILTER` rename in favor of a why-comment; clarified the count loop is fused; corrected test seed to four columns with consistent counts; qualified chip-count semantics regarding search text; trimmed Proving to commands relevant for JS/CSS changes.
