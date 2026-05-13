# ADR 009: Tabular Uploads and Stable Column Identity

## Status

Accepted

## Context

Data Chord started with CSV-only assumptions. Column mappings, review state,
PV lookup, and override persistence were often keyed by the visible header
text. That worked for simple CSVs, but it does not hold for broader tabular
support:

- TSV and XLSX files need the same workflow without reimplementing parsing in
  every stage.
- XLSX workbooks require a selected worksheet to travel through analysis,
  mapping, harmonization, review, and export.
- Duplicate headers are valid tabular input, so header text cannot be the
  stable column identifier.
- Stage 2 rename controls can change output column names, but those display
  names must not break PV lookup or review overrides.

We considered keeping header-keyed dictionaries and adding special handling for
duplicates, but that would spread duplicate-header logic across stages. We also
considered converting everything to CSV at upload time, but that would lose XLSX
worksheet structure and make format-preserving export harder.

## Decision

Use the `netrias_client` tabular helpers as the file boundary, and use stable
column keys as the internal identity for tabular columns.

1. Upload storage accepts CSV, TSV, and XLSX files and records the tabular
   format plus selected worksheet metadata.

2. Stage 1 analysis reads files with SDK tabular helpers and emits both:
   - `column_key`, the stable internal identity such as `col_0000`
   - `column_name` / `header`, the visible source header

3. Mapping manifests are keyed by `column_key`. The manifest record may also
   carry `column_name`, but that name is display/output metadata, not identity.

4. Stage 2 rename choices are sent as `column_renames`, keyed by `column_key`.
   Stage 3 applies those names to the manifest output metadata while preserving
   the stable key for all internal lookups.

5. Column-to-CDE and column-rename maps are immutable domain snapshots once
   parsed from browser payloads. Callers create replacement maps rather than
   mutating shared dictionaries.

6. Stage 4 review overrides and PV lookup use `column_key`. The UI can show the
   visible header separately.

7. Stage 5 applies manual review overrides by original column key, then writes
   the harmonized dataset using the harmonized file's columns and format so SDK
   output headers and Stage 2 rename choices are preserved.

8. The downloadable CDE mapping artifact is keyed by `column_key`. Source and
   output headers are recorded as metadata so duplicate headers and user
   renames remain auditable without becoming identity.

## Consequences

### Positive

- Duplicate headers are safe because internal state is not keyed by header text.
- CSV, TSV, and XLSX share one tabular path instead of separate per-format logic.
- XLSX downloads can preserve the input workbook shape and selected worksheet.
- Column rename controls affect output names without changing domain identity.

### Negative

- The domain model has two column concepts: stable key and visible name. Callers
  must choose the right one.
- Older code and docs that say "column name" can be ambiguous and need cleanup.
- Tests need to cover both duplicate-header identity and output-header
  preservation.

### Follow-up Rules

- New cross-stage contracts should use `ColumnKey` or serialized `column_key`
  for identity.
- Use visible headers only for UI labels and exported output names.
- Treat column-keyed mapping objects as immutable snapshots; use replacement
  methods when a workflow needs to change a mapping.
- When writing downloads, preserve the harmonized dataset's headers and format;
  use the original dataset only to locate source columns for review overrides.
- When adding audit artifacts, use `column_key` as the join key and treat
  source/output header names as derived metadata.
