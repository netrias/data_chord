# Playwright Requirements Migration Plan

This plan categorizes every requirement by how well it can be exercised through a real browser workflow. The goal is to move as many requirements tests as is useful into Playwright while keeping Python tests for rules that are clearer, faster, or stronger outside the browser.

## Problem Context

The current requirements suite is formal, traceable, and mostly Python-based. That gives good coverage of API behavior, storage rules, domain invariants, and architecture boundaries, but it does not always prove that a user can complete the same behavior through the actual UI.

The direct path is to keep requirements tests under `tests/requirements`, add Playwright tests there for user-facing behavior, and teach the traceability checker to count those browser tests. Python should stay responsible for pure domain rules, static architecture checks, and exact low-level serialization behavior where a browser would only provide weak proof.

## File Impact

| File or path | Action | Scope |
| --- | --- | --- |
| `tests/requirements/e2e/` | Add | Requirement-tagged Playwright tests and browser fixtures. |
| `tests/requirements/e2e/support/` | Add | Shared browser helpers, seeded workflow setup, download parsing, and route mocks. |
| `tests/requirements/e2e/fixtures/` | Add | CSV fixtures used only by requirement browser tests. |
| `scripts/check_requirements_traceability.py` | Update | Count Playwright requirement tests as formal traceability coverage. |
| `playwright.config.mjs` or a new requirements config | Update or add | Let Playwright discover tests under `tests/requirements/e2e`. |
| `justfile` | Update | Add a command such as `just requirements-e2e`. |
| Existing Python requirement tests | Keep, trim, or replace case by case | Remove only tests whose proof is fully replaced by stronger browser coverage. |

## Domain Model

The important concepts for the migration are:

1. A **requirement** is still the canonical behavior statement in `requirements.md`.

2. A **formal requirement test** is any pytest or Playwright test that declares one or more requirement IDs and follows Given/When/Then structure.

3. A **browser proof** verifies behavior through pages, controls, navigation, requests, and downloaded artifacts that a user would actually touch.

4. A **Python proof** verifies behavior below the UI, such as pure domain rules, storage contracts, static architecture, and property-based invariants.

## Contracts

1. Playwright requirement tests should use titles beginning with bracketed IDs, such as `[R-008 R-009] csv upload returns a stable file id`.

2. Playwright requirement tests should include `// Given:`, `// When:`, and `// Then:` comments so the traceability report can summarize intent.

3. Pytest requirement tests continue using `@pytest.mark.requirements(...)` and docstring Given/When/Then lines.

4. A requirement can be covered by both Playwright and Python when the UI proof and the lower-level proof answer different questions.

## Categories

| Fit | Meaning |
| --- | --- |
| Full | The requirement can be directly verified through a browser workflow. |
| Partial | A browser workflow can verify the user-visible outcome, but Python should keep the lower-level invariant or contract proof. |
| No | A browser test would be indirect or weak. Keep this as Python, static analysis, or property-based testing. |

## Test Organization

1. Put requirement Playwright tests under `tests/requirements/e2e/`.

2. Keep shared Playwright fixtures under `tests/requirements/e2e/support/` and CSV fixtures under `tests/requirements/e2e/fixtures/`.

3. Use a test-title convention that the traceability checker can read, for example:

   ```js
   test('[R-008 R-009] csv upload returns a stable file id', async ({ page }) => {
     // Given: a user is on Stage 1 with no uploaded file selected.
     // When: the user uploads a CSV and starts analysis.
     // Then: Stage 2 opens for the uploaded file and carries the same file_id.
   });
   ```

4. Update `scripts/check_requirements_traceability.py` so it reads both pytest markers and Playwright titles under `tests/requirements`.

5. Add a dedicated command such as `just requirements-e2e`, then decide whether `just requirements-coverage` should run browser tests too or stay Python-only because coverage is a Python-code metric.

## Migration Order

1. Move the obvious browser workflows first: R-004 through R-023, R-027, R-043 through R-060, and R-077 through R-084.

2. Add browser coverage for recovery behavior next: R-006, R-029, R-067 through R-070.

3. Add browser-backed exactness cases for whitespace, case, PV display, duplicate headers, short rows, and export downloads.

4. Leave architecture, pure domain invariants, and traceability policy in Python unless the browser adds a real user-visible proof.

## Proving

1. Run `just requirements-trace` after the traceability checker learns Playwright tests.

2. Run `just requirements-e2e` for the browser requirements suite.

3. Run `just test` to keep the Python suite healthy while tests are moved.

4. Run `just js-check` when Playwright helpers or frontend-facing code changes.

5. Run a final `conform` pass on the modified files or current changeset before calling the migration complete.

## Requirement Matrix

| Requirement | Fit | Browser proof to add or keep | Python proof to keep |
| --- | --- | --- | --- |
| R-001 | No | None. | Requirement ID uniqueness is a document rule. |
| R-002 | No | None. | Flat numbering is a document rule. |
| R-003 | No | None. | Keep the current heuristic or manual review expectation. |
| R-004 | Full | Visit all five stages and assert the pages render with the workflow tracker. | Optional smoke test only. |
| R-005 | Full | Complete upload, mapping, harmonize, review, and export without re-entering earlier choices. | Keep only if a lower-level session contract needs proof. |
| R-006 | Full | Trigger recoverable errors from the UI and assert plain-language messages and next actions. | Keep API-level edge cases that are hard to trigger from UI. |
| R-007 | Full | Start one workflow, cache downstream state, start a new upload, and assert old state is gone. | Keep storage-specific clearing checks if needed. |
| R-008 | Full | Upload a CSV from Stage 1. | None needed beyond browser test. |
| R-009 | Full | Assert the `file_id` in the Stage 2 URL and later requests stays tied to the upload. | Keep only if storage ID format matters. |
| R-010 | Partial | Upload source data, proceed to export, and compare downloaded CSV values. | Keep exact storage roundtrip checks. |
| R-011 | Partial | Verify whitespace/case/empty source values survive through browser-driven export. | Keep exact reader/writer invariants. |
| R-012 | Full | Use duplicate CSV headers and assert review/export keep both positional columns. | Keep parser-level positional tests if useful. |
| R-013 | Full | Run duplicate headers through mapping, review, summary, and export. | Keep domain-level duplicate column identity tests. |
| R-014 | Full | Export a short-row CSV after a trailing-column override and assert padding. | Keep direct export edge case if browser setup is heavy. |
| R-015 | Full | Assert duplicate or reordered columns are addressed by position during mapping and review. | Keep canonical assignment unit/feature tests. |
| R-016 | Full | Show duplicate display names but apply decisions to the correct column. | Keep domain contract tests. |
| R-017 | Full | In Stage 2, accept a recommendation, override a CDE, and choose no mapping. | None needed beyond focused browser tests. |
| R-018 | Full | Override an AI mapping in Stage 2 and assert harmonization uses the override. | Keep request-payload inspection if easier in Python. |
| R-019 | Full | Set a column to no mapping and assert the UI and export treat it as explicitly unmapped. | Keep boundary schema tests. |
| R-020 | Full | Leave a column unmapped and assert downloaded export preserves it. | Keep direct export artifact checks if needed. |
| R-021 | Partial | Intercept the harmonize request and assert resolved assignment fields. | Keep canonical domain representation tests. |
| R-022 | Partial | Intercept a no-mapping harmonize request and assert no CDE or harmonization type is sent. | Keep assignment construction tests. |
| R-023 | Partial | Override to a CDE with a different harmonization type and inspect the request. | Keep domain resolution tests. |
| R-024 | No | None. | Domain ownership is an architecture rule. |
| R-025 | Partial | Intercept harmonize and assert only mapped harmonizable columns are submitted. | Keep service-level filtering tests. |
| R-026 | Partial | Use a pass-through CDE and assert it is not sent for value harmonization. | Keep domain/service rule tests. |
| R-027 | Full | Open Stage 4 and assert original value, AI value, confidence, row, alternatives, and column identity are visible or available. | Keep response-shape tests if the UI hides some metadata. |
| R-028 | Partial | Complete harmonization and refresh/reopen later stages using persisted state. | Keep manifest writer/reader tests. |
| R-029 | Full | Mock harmonization failure/unavailable client and assert controlled UI behavior. | Keep API-level no-traceback checks. |
| R-030 | Partial | Use whitespace-significant values in review and export workflows. | Keep pure exact-match tests. |
| R-031 | Partial | Show case mismatch as non-conformant in summary/review. | Keep pure exact-match tests. |
| R-032 | Partial | Show exact PV conformance in review/summary. | Keep property/domain PV membership tests. |
| R-033 | Partial | Browser summary can show original value preserved when already conformant. | Keep Stage 3 PV adjustment logic test. |
| R-034 | Partial | Summary/review can show stripped AI output. | Keep manifest reader boundary test. |
| R-035 | Partial | Summary/review can show original user whitespace preserved. | Keep manifest reader boundary test. |
| R-036 | Full | Verify whitespace markers render in Stage 4 for meaningful differences. | Keep small JS/unit test if marker rendering has pure logic. |
| R-037 | Partial | Refresh Stage 4 after seeded PV persistence and assert PV-dependent display works. | Keep PV lookup-by-column identity test. |
| R-038 | No | None. | Immutability is a domain data-structure rule. |
| R-039 | Partial | Restart-like browser test with cleared cache and persisted PV state. | Keep direct persistence recovery test. |
| R-040 | Full | Open review when PV manifest is missing and assert the page still works with limited PV display. | Keep API fallback test. |
| R-041 | Full | Stage 5 summary shows non-conformant when original, AI, and alternatives are outside PVs. | Keep summary classification logic test. |
| R-042 | Partial | Browser summary can show the conformant alternative outcome. | Keep Stage 3 adjustment source test. |
| R-043 | Full | Stage 4 shows harmonized values beside original values. | None needed beyond browser test. |
| R-044 | Full | Edit a reviewed value in Stage 4. | None needed beyond browser test. |
| R-045 | Full | Edit a reviewed value, reload, and assert it persists. | Keep storage contract only if needed. |
| R-046 | Full | Apply overrides to duplicate-named columns and assert each lands in the right column. | Keep positional storage test. |
| R-047 | Full | Open row context for duplicate headers and assert both positional cells are shown. | Keep API response test if context popup is hard to drive. |
| R-048 | Full | Change mode/filter/sort/progress, reload or navigate away/back, and assert state is restored. | Keep state schema tests if any. |
| R-049 | Full | Stage 5 summary distinguishes unchanged, AI, and manual override rows. | Keep classification unit tests if they are small. |
| R-050 | Full | Stage 5 summary shows duplicate-named columns as distinct entries. | Keep direct summary response test. |
| R-051 | Full | Open transformation history and assert original, AI, and manual steps are shown. | Keep manifest boundary tests. |
| R-052 | Full | Make repeated and distinct overrides, then assert displayed history collapses only consecutive duplicates. | Keep classification/history logic test if easier. |
| R-053 | Full | Download Stage 5 bundle and assert it contains a harmonized CSV. | None needed beyond browser download test. |
| R-054 | Full | Download Stage 5 bundle and assert it contains the mapping/audit artifact. | None needed beyond browser download test. |
| R-055 | Full | Save manual overrides and assert downloaded CSV uses them. | Keep direct export test for edge cases. |
| R-056 | Full | Download duplicate-header CSV and assert headers are preserved exactly. | Keep direct CSV writer test if needed. |
| R-057 | Full | Download export and assert untouched columns/cells are unchanged. | Keep property tests for broader input coverage. |
| R-058 | Full | Leave one column unmapped, harmonize another, and assert unmapped export values are unchanged. | Keep direct export rule test. |
| R-059 | Full | Browser-driven export of short rows with trailing overrides. | Keep direct export edge case if browser fixture is brittle. |
| R-060 | Partial | Browser download can inspect line endings in the CSV entry. | Keep direct writer test because line endings are serialization-level. |
| R-061 | No | None. | Cross-stage imports are static architecture. |
| R-062 | No | None. | Domain ownership is static architecture. |
| R-063 | No | None. | Stage-specific asset location is static architecture. |
| R-064 | Partial | Browser can prove refresh/reopen works from persisted manifest. | Keep manifest source-of-truth tests. |
| R-065 | No | None. | Boundary conversion ownership is architecture. |
| R-066 | No | None. | Storage abstraction usage is static architecture. |
| R-067 | Full | Navigate to missing-file URLs or actions and assert clear not-found UI. | Keep API status-code test. |
| R-068 | Full | Open missing-manifest/PV/review states and assert recoverable UI. | Keep API fallback tests. |
| R-069 | Full | Assert user-facing recovery messages are plain and actionable. | Keep API message tests for non-UI endpoints. |
| R-070 | Partial | UI can assert no traceback leaks into visible messages. | Keep log/API-level traceback tests. |
| R-071 | Partial | Traceability checker can count Playwright requirement titles. | Keep checker tests in Python. |
| R-072 | Partial | Playwright tests should use Given/When/Then comments. | Keep checker tests in Python. |
| R-073 | Partial | Playwright tests should include negative Given assertions where helpful. | Keep convention checker in Python. |
| R-074 | Partial | Moving workflow tests to Playwright supports the policy. | Keep policy checker in Python. |
| R-075 | No | None. | Pure domain rules belong in Python/property tests. |
| R-076 | No | None. | Property-based testing is a Python test strategy requirement. |
| R-077 | Full | Click backward stage navigation in the browser. | None needed beyond browser test. |
| R-078 | Full | Navigate back to Stage 2 and assert saved mapping state appears. | Keep expected-failing Python test until implementation exists. |
| R-079 | Full | Assert controls distinguish navigation from actions in the UI. | Keep only if API state model is added. |
| R-080 | Full | Change upstream mapping after harmonization and assert downstream stale state is blocked. | Keep domain stale-state model tests once added. |
| R-081 | Full | After changed mappings, assert review/export require re-harmonization. | Keep API guard tests once added. |
| R-082 | Full | From later stages, return to Stage 1. | None needed beyond browser test. |
| R-083 | Full | Start a new workflow and assert file, mappings, review, and export state are cleared. | Keep storage cleanup checks if needed. |
| R-084 | Full | Stage 5 exposes and completes a start-new-workflow path. | None needed beyond browser test. |

## Expected Result

This moves most user-facing requirements into real browser workflows without weakening the project rules that are better proven below the UI. The likely final shape is:

1. Playwright becomes the primary proof for workflow requirements.

2. Python feature tests remain for API-level setup, storage edge cases, and fast regression checks.

3. Python unit, property, and static tests remain the source of truth for architecture and pure domain rules.
