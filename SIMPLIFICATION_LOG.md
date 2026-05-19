# Simplification Log

This log tracks small, proven simplifications made while moving Data Chord
toward a clearer workflow structure. Each entry should say what changed, what
behavior was protected, and what proof ran.

## 2026-05-19

1. Stage navigation after harmonization

   Improved the completed Stage 3 flow so users can go directly to Stage 5
   after harmonization finishes, while Stage 2 stays read-only for the mappings
   that produced the current output. Stage 5 also now loads from the URL
   `file_id` when session storage is missing.

   Behavior protected: completed harmonization unlocks Stage 4 and Stage 5;
   locked Stage 2 cannot rerun harmonization or mutate mappings; Stage 5 can
   reload from URL context.

   Proof: `uv run pytest`, `uv run ruff check src tests`,
   `uv run basedpyright`, `npm run typecheck`, `just js-check`,
   `npm run test:e2e`, plus green CI on PR #86.

2. Workflow state audit started

   Audited where workflow facts are stored or reconstructed across backend
   storage, FileStore JSON sidecars, in-memory cache, URL parameters, and
   browser session storage. No production behavior changed in this step.

   Behavior protected: no runtime code changed.

   Proof: docs-only diff plus `git diff --check`.

3. Stored manifest handoff verified

   Confirmed the current Stage 3 handoff now treats the Stage 1 stored mapping
   manifest as backend truth and keeps the request manifest only as a
   compatibility fallback. The workflow plan now marks this as completed and
   points the next simplification at selected data model/version persistence.

   Behavior protected: Stage 3 can harmonize without a browser-carried manifest
   body, and a stale request manifest cannot override the stored analysis result.

   Proof: `uv run pytest tests/test_harmonize_flow.py tests/test_stage_level_features.py -q`,
   `uv run ruff check src/stage_3_harmonize/router.py tests/test_harmonize_flow.py tests/test_stage_level_features.py`,
   `uv run basedpyright src/stage_3_harmonize/router.py tests/test_harmonize_flow.py tests/test_stage_level_features.py`,
   plus `git diff --check`.

4. Selected model/version durable owner

   Added `WorkflowState` as a small FileStore sidecar for the selected data
   model and version. Stage 1 saves it after analysis. Stage 2 can reload CDE
   options from it when only `file_id` is present and the CDE cache is empty.
   Stage 3 prefers it over stale harmonize request fields. Stage 4 and Stage 5
   now also call the configured FileStore through the dependency module, so
   review override persistence and workflow state use the same store path.

   Behavior protected: selected model/version survives browser URL loss and
   cache loss; old request/URL fields remain as fallbacks; re-running
   harmonization still clears stale review overrides.

   Proof: `uv run pytest -q`, `uv run ruff check src tests`, and
   `uv run basedpyright`.

5. Confirmed mapping choices durable owner

   Extended `WorkflowState` with optional `ConfirmedMappingChoices` for Stage 2
   manual mappings, explicit No Mapping choices, and column renames. Stage 2 now
   saves those choices before navigating to harmonization. Stage 3 prefers the
   durable confirmed choices when present, while keeping the request body as the
   compatibility fallback.

   Behavior protected: Stage 3 still works from the existing request payload,
   but stale request mapping choices cannot override choices already confirmed
   and saved by Stage 2.

   Proof: `uv run pytest -q`, `uv run ruff check src tests`,
   `uv run basedpyright`, `npm run typecheck`, `just js-check`, and
   `npm run test:e2e`.

6. Stage 4/5 PV lookup query

   Added a column-keyed PV lookup query in `pv_persistence` and moved Stage 4
   review rows, Stage 4 non-conformant values, and Stage 5 summary/history
   conformance onto that query. The query still uses `SessionCache` internally
   for acceleration and recovery, but the routers no longer depend on cache as
   their workflow-state interface.

   Behavior protected: PV dropdowns, non-conformant review warnings, Stage 5
   non-conformant counts, and transformation history conformance all recover
   from the durable PV manifest after session cache loss.

   Proof: `uv run pytest -q`, `uv run ruff check src tests`,
   `uv run basedpyright`, `npm run typecheck`, `just js-check`, and
   `npm run test:e2e`.

7. Harmonized output storage lookup

   Added `UploadStorage.load_harmonized_path(file_id)` and moved Stage 5
   downloads to use it instead of reconstructing the `harmonized/` path from
   the original upload path. The old module-level `resolve_harmonized_path`
   helper is no longer exported.

   Behavior protected: downloads still use the harmonized intermediate for CSV,
   TSV, and XLSX flows, preserve harmonized headers, and return the existing
   missing-harmonized-file error when output is absent.

   Proof: `uv run pytest -q`, `uv run ruff check src tests`,
   `uv run basedpyright`, `npm run typecheck`, `just js-check`, and
   `npm run test:e2e`.

8. Review override save use case

   Characterized the Stage 4 manual override dual-write contract before
   changing production code. ReviewOverrides JSON remains the source used by
   Stage 5 export to alter row values. Parquet manifest manual override rows
   remain the source used by Stage 5 summary and transformation history.
   Deleting review overrides currently clears export overrides but leaves the
   manifest audit history intact. After pinning that behavior, the save route
   was thinned to call a `save_review_overrides` use case that owns both the
   JSON save and manifest audit sync.

   Behavior protected: saving overrides still affects downloads and
   summary/history; deleting overrides still removes export changes while
   preserving existing manifest audit history.

   Proof: `uv run pytest tests/test_overrides_behavior.py
   tests/test_full_flow_features.py::test_full_flow_overrides_propagate_within_column
   tests/test_full_flow_features.py::test_full_flow_reharmonize_clears_overrides
   tests/test_stage_level_features.py::test_stage5_download_matches_harmonized_when_no_overrides -q`,
   `uv run ruff check src/stage_4_review_results/router.py
   src/stage_4_review_results/use_cases.py tests/test_overrides_behavior.py`,
   and `uv run basedpyright src/stage_4_review_results/router.py
   src/stage_4_review_results/use_cases.py tests/test_overrides_behavior.py`.

9. Test harmonized-output storage setup

   Moved the shared `create_harmonized_csv` test helper onto
   `UploadStorage.harmonized_path_for(file_id, original_path)` instead of
   reconstructing the `uploads/harmonized` directory from
   `original_path.parent.parent`. The manifest seeding helper now writes its
   temporary parquet file in a temporary directory and then calls the storage
   adapter, so it no longer needs `UploadStorage.harmonized_dir`. That public
   property was removed.

   Behavior protected: Stage 4 review, Stage 5 summary, override, download,
   journey, and contract tests still create the same managed harmonized
   artifacts through storage-owned paths.

   Proof: `uv run pytest tests/test_overrides_behavior.py
   tests/test_full_flow_features.py tests/test_stage_level_features.py
   tests/test_contracts.py tests/test_journeys.py tests/test_transformation_history.py -q`,
   `uv run ruff check src/domain/storage/upload_storage.py tests/conftest.py
   tests/test_overrides_behavior.py tests/test_full_flow_features.py
   tests/test_stage_level_features.py tests/test_contracts.py
   tests/test_journeys.py tests/test_transformation_history.py`, and
   `uv run basedpyright src/domain/storage/upload_storage.py tests/conftest.py
   tests/test_overrides_behavior.py tests/test_full_flow_features.py
   tests/test_stage_level_features.py tests/test_contracts.py
   tests/test_journeys.py tests/test_transformation_history.py`.

10. Stage 5 download use case

   Added a characterization test proving Stage 5 downloads include the
   harmonization manifest as inspectable JSON when a manifest exists. Then
   moved download package construction out of the Stage 5 route and into
   `build_download_package`. The route now maps use-case errors to HTTP
   responses and streams the returned package. The use case owns loading the
   upload metadata, harmonized dataset, review overrides, manifest JSON, CDE
   mapping artifact, and cache cleanup.

   Behavior protected: downloads still include final tabular data, optional
   manifest JSON, optional CDE mapping JSON, row-level review overrides, and
   selected-sheet XLSX output.

   Proof: `uv run pytest tests/test_stage_level_features.py::test_stage5_download_matches_harmonized_when_no_overrides
   tests/test_stage_level_features.py::test_stage5_download_includes_manifest_json_when_available
   tests/test_stage_level_features.py::test_stage5_download_includes_cde_mapping_artifact
   tests/test_stage_level_features.py::test_stage5_download_tsv_input_exports_tsv
   tests/test_stage_level_features.py::test_stage5_download_xlsx_input_exports_xlsx_selected_sheet
   tests/test_overrides_behavior.py::test_stage4_save_writes_export_overrides_and_summary_audit
   tests/test_overrides_behavior.py::test_stage4_delete_clears_export_overrides_but_preserves_summary_audit
   tests/test_full_flow_features.py::test_full_flow_duplicate_headers_keep_columns_separate -q`,
   `uv run ruff check src/domain/cde_mapping_persistence.py
   src/stage_5_review_summary/router.py src/stage_5_review_summary/use_cases.py
   tests/test_stage_level_features.py`, and `uv run basedpyright
   src/domain/cde_mapping_persistence.py src/stage_5_review_summary/router.py
   src/stage_5_review_summary/use_cases.py tests/test_stage_level_features.py`.

11. Stage 5 summary use case and schemas

   Moved Stage 5 summary response models into `stage_5_review_summary.schemas`
   and moved summary construction into `build_summary`. The route now loads the
   configured upload storage, maps summary use-case errors to the existing HTTP
   responses, and returns the use-case response. The use case owns the manifest
   read, PV lookup, change counting, transformation history, and
   non-conformant summary calculation.

   Behavior protected: Stage 5 summary still reports AI/manual/unchanged
   counts, term mappings, transformation history, PV conformance, durable PV
   recovery after cache loss, and the existing missing/unreadable manifest
   error boundaries.

   Proof: `uv run pytest tests/test_transformation_history.py
   tests/test_full_flow_features.py::test_stage5_summary_recovers_pvs_after_session_cache_loss
   tests/test_overrides_behavior.py::test_stage4_save_writes_export_overrides_and_summary_audit
   tests/test_overrides_behavior.py::test_stage4_delete_clears_export_overrides_but_preserves_summary_audit
   tests/test_stage_level_features.py::test_stage5_summary_zero_changes_when_terms_equal
   tests/test_contracts.py::TestSummaryContract -q`,
   `uv run ruff check src/stage_5_review_summary/router.py
   src/stage_5_review_summary/use_cases.py src/stage_5_review_summary/schemas.py
   tests/test_transformation_history.py`, and `uv run basedpyright
   src/stage_5_review_summary/router.py src/stage_5_review_summary/use_cases.py
   src/stage_5_review_summary/schemas.py tests/test_transformation_history.py`.

12. Stage 2 to Stage 3 manifest handoff cleanup

   Removed the full mapping manifest from the browser-carried Stage 3 handoff.
   Stage 2 still keeps its analysis payload for rendering, and Stage 3 still
   accepts a request manifest for compatibility, but the normal browser path no
   longer stores or forwards the manifest as backend truth. Stage 3 now relies
   on the manifest saved by Stage 1 analysis.

   Behavior protected: Stage 3 still harmonizes when the request omits the
   manifest body, stored manifests still win over stale request manifests, and
   Stage 2 still hands off manual CDE overrides plus column renames.

   Proof: `uv run pytest tests/test_harmonize_flow.py::test_harmonize_uses_stored_mapping_manifest_when_request_omits_manifest
   tests/test_harmonize_flow.py::test_harmonize_prefers_stored_mapping_manifest_over_stale_request_manifest
   tests/test_stage_level_features.py::test_stage3_harmonize_uses_stored_manifest_when_payload_missing
   tests/test_stage_level_features.py::test_stage3_harmonize_prefers_stored_manifest_over_payload_manifest -q`,
   `npm run test:e2e -- --grep "Stage 2 submits selected column renames"`,
   and `just js-check`.

13. E2E harmonization seeding storage adapter

   Moved the Playwright `seed_harmonization.py` helper onto `UploadStorage`.
   The helper now loads upload metadata through storage, writes harmonized
   output through `harmonized_path_for`, and saves parquet manifests through
   `save_harmonization_manifest`. Its command-line interface did not change.

   Behavior protected: e2e flows can still seed CSV, TSV, and XLSX harmonized
   output plus optional manifests for review, summary, and download tests.

   Proof: `npm run test:e2e -- --grep "happy path flow|TSV flow|XLSX flow"`,
   `uv run pytest tests/test_stage_level_features.py::test_upload_storage_loads_managed_harmonized_output_path
   tests/test_stage_level_features.py::test_stage5_download_includes_manifest_json_when_available -q`,
   `uv run ruff check tests/e2e/support/seed_harmonization.py`, and
   `uv run basedpyright tests/e2e/support/seed_harmonization.py`.

14. Stage 3 request-time upload storage

   Removed the Stage 3 module-level upload storage object. The harmonization
   route now loads the configured storage dependency at request time and passes
   that storage object into the manifest store/adjust helper. The shared test
   fixture no longer patches a private `stage_3_harmonize.router._storage`
   escape hatch.

   Behavior protected: Stage 3 still reads upload metadata, stored mapping
   manifests, selected workflow state, confirmed mapping choices, harmonized
   output paths, and stored harmonization manifests through the same public
   route behavior.

   Proof: `uv run pytest tests/test_harmonize_flow.py
   tests/test_stage_level_features.py::test_stage3_harmonize_uses_stored_manifest_when_payload_missing
   tests/test_stage_level_features.py::test_stage3_harmonize_prefers_stored_manifest_over_payload_manifest
   tests/test_stage_level_features.py::test_stage3_harmonize_prefers_stored_selection_over_stale_request
   tests/test_stage_level_features.py::test_stage3_harmonize_prefers_stored_mapping_choices_over_stale_request -q`,
   `uv run ruff check src/stage_3_harmonize/router.py tests/conftest.py
   tests/test_harmonize_flow.py tests/test_stage_level_features.py`, and
   `uv run basedpyright src/stage_3_harmonize/router.py tests/conftest.py
   tests/test_harmonize_flow.py tests/test_stage_level_features.py`.
   Full proof also passed with `uv run pytest -q`, `uv run ruff check src
   tests`, `uv run basedpyright`, `npm run typecheck`, `just js-check`, and
   `npm run test:e2e`.

15. Stage 4/5 configured storage dependency

   Aligned Stage 4 and Stage 5 with the Stage 3 storage pattern. These routes
   now ask `src.domain.dependencies` for upload storage at request time instead
   of importing the storage getter directly. The shared app test fixture now
   patches only the dependency module and no longer reaches into Stage 4 or
   Stage 5 route modules for storage replacement.

   Behavior protected: Stage 4 rows, override saves, non-conformant lookups,
   row context, term row indices, Stage 5 summary, and Stage 5 downloads still
   use the configured upload storage and existing durable artifacts.

   Proof: `uv run pytest tests/test_overrides_behavior.py
   tests/test_transformation_history.py
   tests/test_stage_level_features.py::test_stage5_download_matches_harmonized_when_no_overrides
   tests/test_stage_level_features.py::test_stage5_summary_zero_changes_when_terms_equal
   tests/test_full_flow_features.py::test_stage4_recovers_pvs_after_session_cache_loss
   tests/test_full_flow_features.py::test_stage5_summary_recovers_pvs_after_session_cache_loss -q`,
   `uv run ruff check src/stage_4_review_results/router.py
   src/stage_5_review_summary/router.py tests/conftest.py
   tests/test_overrides_behavior.py tests/test_transformation_history.py
   tests/test_stage_level_features.py tests/test_full_flow_features.py`, and
   `uv run basedpyright src/stage_4_review_results/router.py
   src/stage_5_review_summary/router.py tests/conftest.py
   tests/test_overrides_behavior.py tests/test_transformation_history.py
   tests/test_stage_level_features.py tests/test_full_flow_features.py`.

16. Stage 1 configured storage dependency

   Removed the Stage 1 module-level upload storage object. Upload and analyze
   now ask `src.domain.dependencies` for upload storage at request time, and
   the selected-sheet helper receives that storage explicitly. The shared app
   test fixture no longer patches private storage objects in any stage router.

   Behavior protected: upload storage, content-type rejection, analyze error
   handling, CSV/TSV/XLSX analysis, selected worksheet handling, manifest
   saving, selected data model/version persistence, and reference cache priming
   still work through the public Stage 1 endpoints.

   Proof: `uv run pytest tests/test_stage_level_features.py::test_stage1_upload_persists_exact_bytes
   tests/test_stage_level_features.py::test_stage1_upload_rejects_mismatched_content_type
   tests/test_stage_level_features.py::test_stage1_analyze_rejects_invalid_utf8
   tests/test_stage_level_features.py::test_stage1_analyze_handles_quoted_commas
   tests/test_stage_level_features.py::test_stage1_analyze_handles_ragged_rows
   tests/test_stage_level_features.py::test_stage1_analyze_accepts_duplicate_headers_with_distinct_column_keys
   tests/test_stage_level_features.py::test_stage1_analyze_accepts_tsv
   tests/test_stage_level_features.py::test_stage1_analyze_xlsx_defaults_to_first_sheet
   tests/test_stage_level_features.py::test_stage1_analyze_xlsx_uses_selected_sheet
   tests/test_stage_level_features.py::test_stage1_analyze_is_idempotent
   tests/test_stage_level_features.py::test_stage1_analyze_uses_selected_version_and_primes_reference_cache
   tests/test_stage_level_features.py::test_stage1_analyze_persists_selected_data_model_version
   tests/test_contracts.py::TestUploadContract tests/test_contracts.py::TestAnalyzeContract -q`,
   `uv run ruff check src/stage_1_upload/router.py tests/conftest.py
   tests/test_stage_level_features.py tests/test_contracts.py`, and
   `uv run basedpyright src/stage_1_upload/router.py tests/conftest.py
   tests/test_stage_level_features.py tests/test_contracts.py`.
   Full proof also passed with `uv run pytest -q`, `uv run ruff check src
   tests`, `uv run basedpyright`, `npm run typecheck`, `just js-check`, and
   `npm run test:e2e`.

17. Stage 4 review rows use case

   Moved the `/stage-4/rows` response construction out of the route and into
   `build_stage_four_rows`. The route now loads configured storage and maps
   use-case errors to the same 404 responses as before. The use case owns the
   review-row workflow: upload metadata lookup, original row count, manifest
   loading, PV lookup, column grouping, transformation response construction,
   recommendation type, confidence fallback, PV conformance, and row index
   truncation.

   Behavior protected: Stage 4 rows still return the same response shape,
   grouped row indices, PV dropdown data after cache loss, duplicate-header
   separation, whitespace-sensitive overrides, BOM handling, and existing
   missing-upload/missing-manifest errors. The recommendation-type tests now
   verify `ai_changed`, `ai_unchanged`, and `no_recommendation` through the
   public `/stage-4/rows` response instead of importing a private router helper.

   Proof: `uv run pytest tests/test_contracts.py::TestRowsContract
   tests/test_full_flow_features.py::test_full_flow_overrides_propagate_within_column
   tests/test_full_flow_features.py::test_stage4_recovers_pvs_after_session_cache_loss
   tests/test_full_flow_features.py::test_full_flow_duplicate_headers_keep_columns_separate
   tests/test_overrides_behavior.py::test_stage4_rows_include_grouped_indices
   tests/test_overrides_behavior.py::test_stage4_preserves_whitespace_values_in_overrides
   tests/test_overrides_behavior.py::test_stage4_handles_bom_headers
   tests/test_error_boundaries.py::TestMissingFileErrors::test_rows_missing_file
   tests/test_error_boundaries.py::TestMissingHarmonizedFileErrors::test_rows_missing_harmonized
   tests/test_journeys.py::test_harmonize_to_review_journey -q`,
   `uv run ruff check src/stage_4_review_results/router.py
   src/stage_4_review_results/use_cases.py tests/test_contracts.py
   tests/test_full_flow_features.py tests/test_overrides_behavior.py
   tests/test_error_boundaries.py tests/test_journeys.py`, and
   `uv run basedpyright src/stage_4_review_results/router.py
   src/stage_4_review_results/use_cases.py tests/test_contracts.py
   tests/test_full_flow_features.py tests/test_overrides_behavior.py
   tests/test_error_boundaries.py tests/test_journeys.py`, plus
   `uv run pytest tests/test_recommendation_type.py -q`,
   `uv run ruff check tests/test_recommendation_type.py
   src/stage_4_review_results/router.py src/stage_4_review_results/use_cases.py`,
   and `uv run basedpyright tests/test_recommendation_type.py
   src/stage_4_review_results/router.py src/stage_4_review_results/use_cases.py`.
   Full proof also passed with `uv run pytest -q`, `uv run ruff check src
   tests`, `uv run basedpyright`, `npm run typecheck`, `just js-check`, and
   `npm run test:e2e`.
