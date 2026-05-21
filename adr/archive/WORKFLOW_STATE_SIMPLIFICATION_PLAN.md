# Workflow State Simplification Plan

## Problem Context

Data Chord already supports the intended user workflow:

1. Upload a table.
2. Select a data model version.
3. Fetch CDE recommendations and permissible values.
4. Let the user confirm column mappings.
5. Run harmonization.
6. Let the user review and override results.
7. Export final data.

The problem is not missing behavior. The problem is that the same workflow facts
are stored or rebuilt in several places. That makes working behavior harder to
protect because a change in one layer can silently disagree with another layer.

The direct path is to simplify in small vertical slices. Each slice should first
pin the current behavior with feature-level tests, then reduce one duplicate
source of truth or one public helper surface.

## Workflow State Inventory

| Fact | Current Storage Or Reconstruction | Current Owner | Notes |
|---|---|---|---|
| Uploaded file identity and file metadata | `uploads/meta/{file_id}.json`; `currentFileSession` in browser session storage; URL `file_id` params | `UploadStorage` is durable owner | Browser state is navigation convenience. Server should treat upload metadata as durable truth. |
| Selected worksheet | `UploadedFileMeta.selected_sheet`; `currentFileSession.selected_sheet`; Stage 1 analyze request `sheet_name` | `UploadStorage` is durable owner | This is already mostly clean. Browser only remembers the UI state. |
| Selected data model and version | `WorkflowState` JSON sidecar; Stage 1 analyze request; Stage 2 URL `schema` / `version_number`; `stage2Payload.target_version_number`; `stage3HarmonizePayload.request` and `.context`; `SessionCache.data_model_selection`; `PVManifest.data_model_key` / `version_label`; CDE mapping download JSON | `WorkflowState` is the durable owner after Stage 1 analysis | Stage 2 and Stage 3 now prefer stored selection when present and keep URL/request values as fallbacks for old sessions. |
| AI column-to-CDE recommendations | Stage 1 analyze response in `stage2Payload`; upload manifest JSON `{file_id}.json`; Stage 3 request `manifest`; `ColumnMappingManifest` domain model | Upload manifest JSON is durable owner for the SDK mapping plan | Browser currently carries the full manifest forward even though Stage 3 can reload it from storage. |
| User column-to-CDE choices | `WorkflowState.mapping_choices`; Stage 2 browser `manual_overrides`; `stage2Payload.manual_overrides`; Stage 3 request `manual_overrides`; `ColumnCdeMap` in `SessionCache`; PV manifest `column_to_cde_key`; CDE mapping audit JSON | `WorkflowState.mapping_choices` is the durable owner after Stage 2 saves choices | Stage 3 now prefers stored confirmed choices when present and keeps request values as fallback for older sessions. |
| Column renames | `WorkflowState.mapping_choices`; Stage 2 browser `column_renames`; Stage 3 request; CDE mapping audit JSON | `WorkflowState.mapping_choices` is the durable owner after Stage 2 saves choices | Similar to user mappings. Browser still carries a request copy as compatibility fallback. |
| CDE metadata and type | Data Model Store API; `SessionCache.cdes`; Stage 2 template `cde_catalog`; column-detail response `cde_types`; mapping audit JSON stores selected CDE info | Data Model Store is external owner; cache is optimization | Cache currently carries enough workflow state that restart paths need special handling. |
| Permissible values | Data Model Store API; `SessionCache.pvs`; PV manifest JSON; Stage 4 `columnPVs`; Stage 5 lazy `ensure_pvs_loaded` | PV manifest is durable recovery copy after Stage 3 | Current behavior correctly recovers after cache loss, but the cache is still part of many read paths. |
| Harmonized output file | `uploads/harmonized/{file_id}.harmonized.{ext}`; resolved from original upload path and file id | `UploadStorage` should be durable owner | The module-level `resolve_harmonized_path()` reconstructs layout outside the storage object. This leaks storage layout. |
| Harmonization manifest | SDK parquet copied to `uploads/manifests/{file_id}_harmonization.parquet`; Stage 4 and Stage 5 read it; Stage 4 appends manual overrides; Stage 3 applies PV adjustments | Parquet manifest is durable owner after Stage 3 | This is the strongest durable representation of harmonization results and manual override audit history. |
| Manual review overrides | ReviewOverrides JSON sidecar; manual override rows appended to parquet manifest; browser Stage 4 autosave state | Split owner | JSON drives final row export. Parquet drives summary/history. This dual-write path needs tests before simplification. |
| Final export state | Generated on demand from upload metadata, harmonized file, review overrides JSON, parquet manifest, and CDE mapping JSON | Stage 5 router currently orchestrates | Export has no durable state, which is fine. It should depend on durable artifacts through storage adapters, not file layout details. |

## Main Duplications To Untangle

1. Selected model/version is passed through browser state, URL state, cache, PV
   manifest, and mapping audit output. It needs one durable workflow owner.

2. Stage 2 sends the full mapping manifest to Stage 3 even though Stage 1 also
   stores it server-side. This makes the browser a carrier for backend truth.

3. `SessionCache` is both an optimization and a workflow handoff mechanism. It
   holds CDEs, PVs, column profiles, selected model, and column mappings.

4. Stage 4 manual overrides are stored twice: ReviewOverrides JSON for export,
   and parquet manual override rows for summary/history. That may be correct,
   but the contract should be explicit and feature-tested before any collapse.

5. Tests still create some storage artifacts directly. Those tests protect
   behavior, but they also preserve knowledge of private file layout.

## Current Progress

The first small simplification is already in the working tree: Stage 3 now
prefers the mapping manifest saved by Stage 1 analysis and treats the manifest
body in the harmonize request as a compatibility fallback. This removes the
browser-carried manifest as required backend truth while preserving the request
field for existing browser/session contracts.

The second simplification introduced `WorkflowState` as the durable owner for
selected data model/version. Stage 1 saves it after analysis. Stage 2 can reload
the mapping page with only `file_id` after CDE cache loss. Stage 3 prefers the
stored selection over stale request fields.

The third simplification extended `WorkflowState` with optional
`ConfirmedMappingChoices`. Stage 2 saves confirmed manual mappings, No Mapping
choices, and column renames before navigating to Stage 3. Stage 3 prefers those
confirmed choices over stale request fields when present, while preserving the
request fallback for older sessions.

The fourth simplification introduced a column-keyed PV lookup query in
`pv_persistence`. Stage 4 and Stage 5 now ask the domain layer for PV sets
instead of loading `SessionCache` and calling `get_pvs_for_column` directly.
The cache remains the internal acceleration/recovery path, but the review and
summary routers no longer treat it as workflow truth.

The fifth simplification moved harmonized output lookup behind `UploadStorage`.
Stage 5 now asks storage for the managed harmonized output path by `file_id`
instead of reconstructing the `harmonized/` layout from the original upload
path.

The sixth simplification characterized the Stage 4 manual override dual-write
contract and extracted the save path into a use case. ReviewOverrides JSON
still owns row-level export application. Parquet manifest manual override rows
still own summary/history audit behavior. Deleting review overrides clears the
export state but intentionally leaves the existing manifest audit rows in place
for now.

The seventh simplification removed one remaining test-support storage layout
leak. Shared test helpers now use `UploadStorage.harmonized_path_for` and
`save_harmonization_manifest` instead of reconstructing the `harmonized/`
directory. The unused public `UploadStorage.harmonized_dir` property was
removed.

The eighth simplification moved Stage 5 download package construction into a
use case. The route now maps use-case errors to HTTP responses and streams the
returned package. The use case owns the final export read path: upload
metadata, harmonized output, review override application, optional manifest
JSON, optional CDE mapping JSON, and cache cleanup.

The ninth simplification moved Stage 5 summary construction into the same use
case layer and split Stage 5 request/response models into a small schemas
module. The route now handles HTTP concerns, while the use case owns manifest
reading, PV lookup, change counting, transformation history, and
non-conformant summary calculation.

The tenth simplification removed the full mapping manifest from the normal
Stage 2 to Stage 3 browser handoff. Stage 3 still accepts a request manifest
as a compatibility fallback, but the browser no longer stores or forwards the
manifest as required backend truth.

The eleventh simplification moved Playwright harmonization seeding onto
`UploadStorage`. E2E support no longer reconstructs the managed harmonized
output or manifest paths; it uses storage metadata, harmonized path lookup, and
manifest save APIs.

The twelfth simplification removed Stage 3's module-level upload storage
object. The harmonization route now asks for the configured storage dependency
at request time and passes that storage into the manifest store/adjust helper.
The shared test fixture no longer patches a private Stage 3 storage escape
hatch.

The thirteenth simplification applied the same request-time storage dependency
pattern to Stage 4 and Stage 5. Those routes now ask the shared dependency
module for upload storage, and the shared app test fixture no longer patches
private route-level storage getters for the review and summary/download stages.

The fourteenth simplification applied the same configured-storage pattern to
Stage 1. Upload and analyze now ask the shared dependency module for upload
storage at request time, so the shared app fixture no longer patches private
storage objects in any stage router.

The fifteenth simplification moved Stage 4 review-row construction into a use
case. The `/stage-4/rows` route now handles HTTP concerns while the use case
owns manifest loading, PV lookup, column grouping, transformation response
construction, recommendation type, confidence fallback, row counts, and row
index truncation.

Focused proof currently covers both important cases:

1. Stage 3 can harmonize when the request omits the manifest body.
2. Stage 3 ignores a stale request manifest when a stored manifest exists.
3. Stage 1 persists the selected model/version.
4. Stage 2 recovers selected model/version after cache loss.
5. Stage 3 ignores stale model/version request fields when durable state exists.
6. Stage 2 persists confirmed mapping choices.
7. Stage 3 ignores stale mapping-choice request fields when confirmed choices
   exist in durable state.
8. Stage 4 recovers PV dropdown/conformance data after session cache loss.
9. Stage 5 recovers PV summary/history conformance after session cache loss.
10. UploadStorage owns harmonized output lookup by file id.
11. Stage 4 override saves update both export state and summary/history audit.
12. Stage 4 override deletes clear export state while preserving audit history.
13. Test harmonized artifacts are seeded through storage-owned helpers.
14. Stage 5 download includes manifest JSON when a manifest exists.
15. Stage 5 summary transformation history and PV conformance still work
    through the public summary endpoint.
16. Stage 2 can hand off to Stage 3 without carrying the mapping manifest.
17. E2E harmonized output and manifests are seeded through storage-owned
    helpers.
18. Stage 3 uses the configured upload storage dependency at request time while
    preserving stored manifest, stored selection, and stored mapping-choice
    behavior.
19. Stage 4 and Stage 5 use the configured upload storage dependency at request
    time while preserving review, override, summary, and download behavior.
20. Stage 1 uses the configured upload storage dependency at request time while
    preserving upload, analyze, selected-sheet, manifest-save, and workflow
    selection persistence behavior.
21. Stage 4 review rows are built by a use case while preserving response
    shape, PV conformance, row counts, row indices, recommendation type, and
    missing-artifact errors.

## Proposed Domain Model Direction

These are target concepts, not a request to build all of them at once.

| Concept | Canonical Owner Goal | Derived Views |
|---|---|---|
| Uploaded dataset | `UploadStorage` metadata | Browser `currentFileSession`, URL `file_id` |
| Workflow selection | A small durable workflow/session record keyed by `file_id` | URL params, Stage 2/3 browser payloads, cache model selection |
| Mapping plan | Existing `ColumnMappingManifest` stored server-side | Stage 2 payload, Stage 3 harmonize request |
| Confirmed mapping choices | Durable workflow/session record before Stage 3; parquet/audit docs after Stage 3 | `ColumnCdeMap`, Stage 3 request, PV manifest |
| PV data | Data Model Store externally; PV manifest as local recovery artifact | `SessionCache.pvs`, Stage 4 dropdown payload |
| Harmonization result | Harmonized file plus parquet manifest | Stage 3 metrics, Stage 4 rows, Stage 5 summary/export |
| Review overrides | Keep ReviewOverrides JSON for export unless tests prove parquet alone is enough | Parquet audit rows, Stage 5 history |

## Staged Simplification Plan

### Stage 0: Characterize The Current Workflow State Contract

Problem: the app has broad tests, but not every state handoff is named as a
contract.

File impact:

| File | Action | Scope |
|---|---|---|
| `tests/test_workflow_state_contracts.py` | Add | Integration tests around durable workflow facts |
| `tests/e2e/app.e2e.spec.mjs` | Strengthen if needed | Browser refresh/navigation contracts only |

Implementation steps:

1. Add tests proving Stage 3 can harmonize from server-stored mapping manifest
   when the request omits the manifest body.
2. Add tests proving selected model version survives a Stage 2/3 refresh path.
3. Add tests proving Stage 4 and Stage 5 still recover PV availability after
   clearing `SessionCache`.
4. Add tests proving review overrides affect both Stage 5 summary/history and
   downloaded rows.

Proving:

1. `uv run pytest tests/test_workflow_state_contracts.py`
2. Relevant existing tests around Stage 3, PV persistence, overrides, summary,
   and download.
3. `uv run ruff check` and `uv run basedpyright` for changed Python files.
4. Conform pass on changed tests.

### Stage 1: Stop Treating Browser Manifest Payload As Required Backend Truth

Problem: Stage 2 carries the full mapping manifest into Stage 3 even though the
server already stores the manifest after analysis.

File impact:

| File | Action | Scope |
|---|---|---|
| `src/stage_3_harmonize/router.py` | Change | Prefer stored manifest; accept request manifest as compatibility fallback |
| `src/stage_2_review_columns/static/stage_2_mappings.js` | Change | Keep request manifest only if needed for compatibility during transition |
| `tests/test_workflow_state_contracts.py` | Add/extend | Prove Stage 3 can use stored manifest |

Implementation steps:

1. Write the characterization test first.
2. Make Stage 3 explicitly load the stored manifest and fall back to the
   request manifest only when storage has none.
3. Keep the request field temporarily because it is a browser contract.
4. After tests prove no dependency on the browser-carried manifest, remove the
   browser write in a later PR.

Proving:

1. Focused workflow-state tests.
2. Existing Stage 2/3 e2e harmonization tests.
3. Lint, type check, conform.

### Stage 2: Introduce A Small Durable Workflow Selection Record

Status: initial selected model/version slice completed. Mapping choices are not
part of this record yet.

Problem: selected model/version is reconstructed from request bodies, URLs,
session storage, cache, PV manifest, and mapping audit output.

File impact:

| File | Action | Scope |
|---|---|---|
| `src/domain/workflow_state.py` | Add | Small model for selected data model/version and confirmed mapping choices |
| `src/domain/storage/file_types.py` | Change | Add semantic JSON artifact type if FileStore owns it |
| `src/domain/storage/file_store.py` | Change | Save/load workflow state helpers |
| `src/stage_1_upload/router.py` | Change | Save selection after analyze |
| `src/stage_2_review_columns/router.py` | Change | Read selection for CDE catalog where possible |
| `src/stage_3_harmonize/router.py` | Change | Read selection instead of relying on cache/request repetition |
| tests | Add/extend | Refresh/restart recovery behavior |

Implementation steps:

1. Add tests for selected version persistence through Stage 1 analyze, Stage 2
   page load, and Stage 3 harmonize.
2. Add `WorkflowState` with only fields needed for the current slice:
   `file_id`, `data_model_key`, `version_number`.
3. Persist it after Stage 1 analyze.
4. Read it in Stage 2 and Stage 3, keeping URL/request fallback for old
   sessions.
5. Do not move mapping choices yet.

Proving:

1. Focused integration tests for selected version.
2. Existing mapping discovery and harmonization tests.
3. E2E test that selects a version and proceeds through Stage 3.
4. Lint, type check, conform.

### Stage 3: Move Confirmed Mapping Choices Into Durable Workflow State

Status: initial manual override, No Mapping, and rename persistence slice
completed. Browser session payloads and Stage 3 request fields remain as
compatibility fallbacks.

Problem: confirmed user mapping choices and renames live in browser state until
Stage 3, then become cache state, PV manifest state, and audit JSON.

File impact:

| File | Action | Scope |
|---|---|---|
| `src/domain/workflow_state.py` | Change | Add confirmed mappings and renames |
| Stage 2 router/API | Add | Save mapping choices endpoint or reuse existing transition point |
| Stage 2 browser JS | Change | Save choices before Stage 3 navigation |
| Stage 3 router | Change | Build `ColumnCdeMap` from durable workflow state |
| tests | Add/extend | Mapping override, No Mapping, rename behavior |

Implementation steps:

1. Add tests for AI mapping, manual override mapping, No Mapping, and rename
   choices surviving browser refresh before Stage 3.
2. Persist confirmed choices server-side from Stage 2.
3. Make Stage 3 read confirmed choices from the durable record, with request
   fallback during transition.
4. Keep `SessionCache.column_to_cde_key` only as PV lookup acceleration.

Proving:

1. Focused tests for mapping choices.
2. Existing full-flow override and duplicate-header tests.
3. Full e2e if browser persistence changes.
4. Lint, type check, conform.

### Stage 4: Make Cache A Pure Optimization For CDEs/PVs/Profiles

Status: initial Stage 4/5 PV read-path slice completed. Stage 4 and Stage 5
use `column_pv_sets(file_id, column_keys)` instead of direct cache access. More
cache cleanup may still be possible in Stage 3 metrics and Stage 2 profile/CDE
flows.

Problem: cache currently contains enough workflow truth that other modules must
recover it from sidecar files.

File impact:

| File | Action | Scope |
|---|---|---|
| `src/domain/data_model_cache.py` | Change | Keep cache focused on fetched data and profiles |
| `src/domain/pv_persistence.py` | Change | Load PVs from durable workflow mapping state plus PV manifest |
| `src/stage_3_harmonize/router.py` | Change | Stop requiring cache to own selected model/mapping truth |
| `src/stage_4_review_results/router.py` | Change | Use a domain query for column PV lookup |
| `src/stage_5_review_summary/router.py` | Change | Same PV lookup path |

Implementation steps:

1. Add tests proving Stage 4/5 work after clearing all session caches.
2. Create one query function for `pvs_for_columns(file_id, columns)`.
3. Make that query load durable PV manifest and use cache internally.
4. Remove direct cache mapping reads from Stage 4/5 call sites where practical.

Proving:

1. PV recovery tests.
2. Stage 4 and Stage 5 summary tests.
3. Full e2e.
4. Lint, type check, conform.

### Stage 5: Hide File Layout Behind Storage Adapters

Status: initial harmonized output lookup slice completed. Stage 5 no longer
imports a module-level path reconstruction helper for harmonized output.

Problem: some code and tests still reconstruct paths or know which directory
holds which artifact.

File impact:

| File | Action | Scope |
|---|---|---|
| `src/domain/storage/upload_storage.py` | Change | Add read/write helpers for harmonized output and manifests |
| `src/stage_5_review_summary/router.py` | Change | Stop using module-level path reconstruction |
| tests/conftest.py and feature tests | Change | Use storage APIs instead of path layout |

Implementation steps:

1. Add tests for export behavior before changing storage helpers.
2. Add `UploadStorage.load_harmonized_path(file_id)` or equivalent.
3. Replace `resolve_harmonized_path()` call sites.
4. Update tests to avoid direct layout setup where a public helper can express
   the same behavior.

Proving:

1. Download/export feature tests.
2. Storage contract tests.
3. Lint, type check, conform.

### Stage 6: Review Manual Override Dual-Write

Status: characterization and initial route thinning completed. The dual-write
contract is still preserved.

Problem: ReviewOverrides JSON and parquet manifest manual override rows both
matter today. We should not collapse them until we prove which one owns which
behavior.

File impact:

| File | Action | Scope |
|---|---|---|
| `src/stage_4_review_results/router.py` | Change only after tests | Override persistence path |
| `src/stage_5_review_summary/router.py` | Change only after tests | Summary/export reads |
| `src/domain/review_overrides.py` | Change only after tests | Override model |

Implementation steps:

1. Add tests that clearly distinguish export row values from summary/history
   audit behavior.
2. Decide whether dual-write is intentional:
   - JSON owns row-level export application.
   - Parquet owns audit/history summary.
3. If dual-write is intentional, document it and extract one use-case function
   so the router is thin.
4. If one representation can go away, remove it in a separate PR after the
   tests prove there is no behavior loss.

Proving:

1. Override summary/history tests.
2. Download tests.
3. Full e2e.
4. Lint, type check, conform.

## Next Implementation Recommendation

Start the next code slice by looking for one remaining place where tests still
reach through storage layout, or one remaining Stage 4/5 router orchestration
block that can move behind an existing storage/query adapter without changing
behavior.

Why this next:

1. Selected model/version, confirmed mapping choices, and Stage 4/5 PV lookup
   now have clearer owners.
2. Harmonized output lookup now lives behind `UploadStorage`.
3. ReviewOverrides JSON and parquet manual override rows are now explicitly
   characterized and the save route is thinner.
4. The most obvious harmonized-output test layout leaks are gone, Stage 5
   summary/download orchestration is behind use cases, and the normal browser
   handoff no longer carries the mapping manifest. Remaining simplification
   should target Stage 3 orchestration, FileStore/UploadStorage overlap, or
   leftover browser fallback payload cleanup.

## Plan Review

| Category | Issue | Severity | Why It Matters | Fix |
|---|---|---:|---|---|
| Problem fit | Plan could grow into a broad rewrite if stages are combined | Medium | The app is working and behavior matters more than architecture | Keep each stage PR-sized and stop after one duplicate source of truth is removed |
| Architecture | `WorkflowState` could become a dumping ground | Medium | A giant session object would recreate the same problem under a new name | Start with selected model/version only; add mapping choices later only after tests justify it |
| Contracts | Browser request fields may still be public contracts | Medium | Removing fields too early could break existing sessions or tests | Treat request fields as compatibility fallbacks before removing writes |
| Proof | Some current tests seed storage internals directly | Medium | A refactor could pass tests that know too much about file layout | Add feature-level tests around public endpoints before changing storage internals |
| Simplicity | Manual override dual-write may be correct | Low | Collapsing it too early could break export or audit history | Defer until explicit tests prove ownership |

Final status: Gate Green for the stored-manifest, selected-model/version,
confirmed mapping-choice, Stage 4/5 PV read-path, harmonized-output storage
lookup, review-override save use-case, and test harmonized-output storage setup
slices, plus the Stage 5 summary/download use-case slices and browser manifest
handoff cleanup. E2E harmonization seeding now uses storage adapters. Do not
collapse manual override dual-write behavior without a separate
behavior-preserving slice.
