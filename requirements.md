# Data Chord Requirements

Status: Initial draft.

Purpose: This document captures the durable behavior Data Chord shall preserve as
the design and implementation evolve. These requirements should be stable enough
to support requirements-based tests, especially around data integrity, column
identity, whitespace, user edits, and export correctness.

## Numbering

R-001. Requirement IDs shall be stable once referenced by tests, ADRs, or issue
tracking. If a requirement is removed, keep its ID retired instead of reusing it.

R-002. Requirements should stay flat. Use simple IDs such as `R-017`; do not
introduce nested IDs such as `R-017.3`.

R-003. A requirement should describe required behavior, not one temporary UI
design. UI details may change as long as the required behavior remains true.

## Product Workflow

R-004. The application shall guide a user through five major workflow stages:
upload, review column mappings, harmonize, review results, and export.

R-005. The workflow shall preserve enough session state for a user to move from
one stage to the next without re-entering prior decisions.

R-006. Each stage shall expose failures in plain language and give the user a
reasonable next action when recovery is possible.

R-007. Starting a new upload shall clear stale per-file harmonization state so
data from a previous file cannot affect a later file.

## Upload And Source Data

R-008. The application shall accept CSV uploads as the baseline supported input
format.

R-009. The application shall assign each uploaded file a stable `file_id` used to
connect all later workflow stages to that exact file.

R-010. The application shall preserve uploaded source data exactly unless a later
requirement explicitly allows a change.

R-011. Source cell values shall not be trimmed, normalized, case-folded, or
otherwise rewritten merely because they contain leading whitespace, trailing
whitespace, internal whitespace, unusual casing, or an empty string.

R-012. Source CSV headers shall be treated as positional columns, not as unique
dictionary keys.

R-013. Duplicate header names shall remain distinct columns throughout upload,
mapping, harmonization, review, row context lookup, summary, and export.

R-014. Rows shorter than the header row shall be handled positionally. Missing
trailing cells should be treated as empty cells for downstream review and export
instead of causing later columns to be skipped.

## Column Mapping

R-015. Each source column shall be addressable by stable `column_id` based on its
position in the uploaded CSV.

R-016. Display names such as header text may be shown to the user, but they shall
not be the only identity used for mapping, review, persistence, or export.

R-017. The user shall be able to accept an AI-recommended CDE mapping, override it
with another CDE, or leave the column unmapped.

R-018. A manual column mapping decision shall take precedence over the AI
recommendation for that column.

R-019. Leaving a column unmapped shall be represented explicitly and shall not be
confused with a missing value, failed lookup, or empty string CDE key.

R-020. Unmapped columns shall pass through to export without harmonization changes
unless the user later maps or edits them.

R-021. The resolved column assignment shall include the column id, original column
name, resolved CDE key if any, and resolved harmonization type if any.

R-022. A resolved column assignment with no CDE key shall also have no
harmonization type.

R-023. A resolved column assignment with a CDE key shall use the harmonization type
that belongs to that resolved CDE, not the harmonization type from a previous or
overridden recommendation.

R-024. Column assignment data used by later stages shall come from one canonical
domain representation, with stage-specific payload shapes treated as boundary
formats.

## Harmonization

R-025. Harmonization shall operate only on columns that are mapped to a
harmonizable CDE.

R-026. Columns classified as pass-through, including numeric columns or CDEs
without permissible values, shall not be sent through value harmonization that can
alter their cell values.

R-027. The harmonization result shall include enough information to review each
changed or proposed value, including original value, AI value, confidence, row
indices, alternatives when available, and target column identity.

R-028. Harmonization shall produce a manifest that records transformation
decisions and can be read by later stages without relying only on in-memory
state.

R-029. If the external harmonization client is unavailable, the application shall
fail or degrade in a controlled way rather than crashing the workflow.

## Whitespace, Case, And Exact Matching

R-030. Whitespace in user-provided data is semantically significant.

R-031. Case differences in user-provided data are semantically significant.

R-032. Permissible value conformance shall use exact matching unless a later
accepted requirement explicitly introduces a different matching rule.

R-033. If a user's original value is already a valid permissible value, the
application shall preserve that original value even when the AI suggests a
different valid permissible value.

R-034. AI output may have leading or trailing artifact whitespace stripped at a
shared reader boundary before conformance checks and review display.

R-035. Stripping AI output whitespace shall not strip or otherwise alter the
user's original `to_harmonize` value.

R-036. The review UI shall make meaningful whitespace differences visible enough
for a user to understand why two values differ.

## Permissible Values

R-037. Permissible values shall be cached and looked up by resolved CDE and stable
column identity, not by header name alone.

R-038. Permissible value sets shall be treated as immutable lookup sets once
loaded for a session.

R-039. Permissible value data needed for review and summary shall be recoverable
from persisted state after an application restart when that persisted state
exists.

R-040. Missing persisted permissible value state shall not crash review or export.
The application should continue with the PV-dependent features unavailable or
limited.

R-041. If original value, AI value, and alternatives are all outside the
permissible value set, the value shall be marked non-conformant instead of being
silently accepted.

R-042. If the AI value is not conformant but an alternative suggestion is
conformant, the application may use the first conformant alternative and shall
record that adjustment source.

## Review

R-043. The review stage shall let users inspect harmonized values together with
their original values.

R-044. The review stage shall support manual overrides at the reviewed value or
cell level.

R-045. Manual review overrides shall be persisted so a refresh, later stage, or
export uses the user's decision.

R-046. Manual review overrides shall be addressed by row identity and stable
column identity, not by duplicate-prone header names alone.

R-047. The review stage shall provide original row context without collapsing
duplicate headers or losing positional cell values.

R-048. Review state such as mode, filters, sorting, progress, and batch position
should be preserved when it affects the user's ability to continue the same
review session.

## Summary And Export

R-049. The summary shall classify each reviewed transformation in a way that
distinguishes unchanged values, AI harmonization, and manual overrides.

R-050. The summary shall preserve duplicate-named columns as distinct mappings
when their `column_id` values differ.

R-051. The summary shall include transformation history that shows the original
value, the AI value when present, and user override steps when present.

R-052. Consecutive duplicate manual override values may be collapsed in displayed
history, but distinct override values shall remain visible in order.

R-053. Export shall include a harmonized CSV.

R-054. Export shall include a human-readable mapping or audit artifact that
records column mapping decisions.

R-055. Export shall apply saved manual review overrides to the correct positional
cells in the harmonized CSV.

R-056. Export shall preserve duplicate header names exactly in the downloaded CSV.

R-057. Export shall preserve untouched columns and untouched cells exactly, aside
from CSV serialization details explicitly accepted by the project.

R-058. Export shall not rewrite an unmapped column's values merely because other
columns were harmonized.

R-059. Export shall pad short rows as needed so overrides to later columns are not
lost.

R-060. Exported CSV line endings shall remain consistent with the project's chosen
writer behavior.

## Architecture

R-061. Stage modules shall not import from other stage modules. Shared contracts,
models, storage, and services belong in `src/domain/`.

R-062. The domain layer shall remain the owner of durable workflow concepts such
as column assignments, change classification, PV validation, storage formats, and
manifest reading/writing.

R-063. Stage-specific API schemas, templates, CSS, and JavaScript should remain
inside the stage module that owns them unless another stage truly needs the same
concept.

R-064. The harmonization manifest shall be treated as the durable source of truth
for transformation decisions after harmonization.

R-065. Boundary payloads may differ from the canonical domain model, but
conversions shall be explicit at module boundaries.

R-066. Storage code shall use the project's storage abstraction instead of
open-coded paths when reading or writing workflow artifacts.

## Error Handling And Recovery

R-067. Missing uploaded files shall return a clear not-found response instead of
an unhandled exception.

R-068. Missing manifests, missing PV state, and missing review state shall be
handled as recoverable workflow states when possible.

R-069. User-facing error messages shall describe what went wrong in terms the user
can act on.

R-070. Internal logs may include technical details needed for debugging, but they
shall not replace clear user-facing failure responses.

## Requirements-Based Testing

R-071. Tests that verify a requirement should reference the requirement ID in the
test name, docstring, or nearby comment when doing so would improve traceability.

R-072. Requirements-based tests should prefer Given/When/Then structure.

R-073. The Given block should include at least one negative assertion when it
helps prove the test is not passing by accident.

R-074. Feature-level tests are preferred for workflow behavior that crosses
upload, review, harmonization, summary, or export boundaries.

R-075. Unit tests are appropriate for pure domain rules such as exact PV
validation, column assignment resolution, and transformation classification.

R-076. Property-based tests should be considered for pure invariants such as
round-tripping positional CSV rows, preserving untouched values, and exact-match
membership behavior.

## Workflow Navigation

R-077. The user shall be able to navigate backward to previous stages in the
current workflow.

R-078. When the user navigates backward, the previous stage shall show the saved
state for the current workflow rather than a blank or newly initialized state.

R-079. The application shall make clear when moving forward will only navigate to
already-computed downstream state and when moving forward will run a workflow
action such as analysis, harmonization, or export generation.

R-080. If the user changes an upstream decision that affects downstream results,
the application shall prevent stale downstream results from being treated as
current.

R-081. If the user changes column mappings after harmonization has already run,
the application shall require harmonization to run again before the user can
review or export results based on the changed mappings.

R-082. The user shall be able to return to Stage 1 and start a new workflow.

R-083. Starting a new workflow shall clearly reset the current file, mappings,
harmonization results, review state, and export state.

R-084. Stage 5 shall provide a clear way to start a new workflow with a new file.

## Verification Examples

V-001. For `R-011`, `R-030`, and `R-035`: Given an uploaded value with leading or
trailing whitespace, when the value is written to and read from the manifest,
then the user-provided value remains byte-for-byte equivalent as text.

V-002. For `R-033`: Given an original value that is already in the PV set and an
AI suggestion that is different but also in the PV set, when PV adjustment runs,
then the chosen value is the original value and the adjustment source records
that the AI suggestion was overruled.

V-003. For `R-034` and `R-035`: Given AI output with artifact whitespace and
original user data with meaningful whitespace, when the manifest is read, then
the AI output is stripped and the original user value is not stripped.

V-004. For `R-012`, `R-013`, and `R-056`: Given a CSV with duplicate headers,
when row context and export are requested, then both columns remain present,
ordered, and distinct.

V-005. For `R-014` and `R-059`: Given a row that is shorter than the header row
and a saved override for a trailing column, when export runs, then the row is
padded and the override appears in the intended trailing column.

V-006. For `R-018`, `R-021`, and `R-023`: Given an AI mapping and a manual
override to a different CDE with a different harmonization type, when resolved
assignments are built, then the assignment uses the override CDE and that CDE's
harmonization type.

V-007. For `R-019`, `R-020`, and `R-058`: Given a column marked unmapped, when
harmonization and export run for other columns, then the unmapped column's values
are not changed.

V-008. For `R-041`: Given original, AI, and alternative values that are all
outside the PV set, when PV validation runs, then the result is marked
non-conformant.

V-009. For `R-045`, `R-046`, and `R-055`: Given saved manual overrides for two
duplicate-named columns, when export runs, then each override is applied to the
cell identified by its row and column position.

V-010. For `R-049`, `R-051`, and `R-052`: Given original, AI, and manual
override steps, when the Stage 5 summary is requested, then the summary includes
ordered transformation history and preserves distinct user override values.

V-011. For `R-039` and `R-040`: Given the in-memory PV cache is empty, when PV
state exists on disk then review can reload it, and when it does not exist then
review continues without crashing.

V-012. For `R-061`: Given a stage module imports from another stage module, when
the cross-stage import check runs, then the check fails.

V-013. For `R-077` and `R-078`: Given a user has reached Stage 4 with saved
mapping and review state, when the user navigates back to Stage 2, then Stage 2
shows the saved mapping state for the current workflow.

V-014. For `R-080` and `R-081`: Given a user has completed harmonization and then
changes a Stage 2 column mapping, when the user tries to continue to review or
export, then the application requires a new harmonization run before those
downstream stages are treated as current.

V-015. For `R-082`, `R-083`, and `R-084`: Given a user is on Stage 5, when the
user starts a new workflow, then the application returns to Stage 1 and clears
the prior workflow state before accepting the next file.
