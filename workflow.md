# Workflow: Data Harmonization Guided Experience (Draft)

## Overview
Four-stage guided experience that lets curators upload tabular data, review column-to-model mappings, run harmonization, and approve results before export. Each stage calls out user goals, available actions, and supporting affordances.

---

## 1. Upload Data
- **Primary goals**
  - Provide the source dataset (CSV or Excel).
  - Understand any format requirements before analysis begins.
- **Key actions**
  - Drag/drop or select a file; choose sheet if Excel; confirm delimiter/encoding when auto-detect fails.
  - Review file name/size and kick off analysis via “Analyze Columns”.
- **Affordances & feedback**
  - Large drop zone, clear instructions, accepted format hints.
  - Immediate validation messaging for unsupported types, encoding issues, or schema errors.
  - Inline progress/confirmation once file is received; “Replace file” option before moving on.
- **System work**
  - Upload to persistent storage, profile headers, detect column types, log metadata for plan JSON.

---

## 2. Review & Confirm Column → Model Mappings
- **Primary goals**
  - Verify AI-suggested mappings per column and make corrections before harmonization.
  - Inspect sample data to confirm column semantics.
- **Key actions**
  - Sort/filter columns (confidence buckets, overrides, unmapped) to focus attention.
  - Override model selection, mark column as “skip”, or accept suggestion.
  - Preview column data (with optional context columns) and review guidance text/tooltips.
- **Affordances & feedback**
  - Cards or grid view showing column name, suggested model, confidence badge, manual override indicator.
  - Hover/selection states that reinforce columns vs rows; help text prominently placed near controls.
  - Data preview pane with clear column labels, optional secondary columns, and pagination/virtualization.
  - Filtering controls (checkboxes/toggles) for low confidence, changed columns, “no map” cases.
- **System work**
  - Draft plan JSON capturing column assignments, suggested models, confidence scores, metadata.
  - Persist override choices and rationale in session state; prep pipeline inputs.

---

## 3. Harmonize (Execute Plan)
- **Primary goals**
  - Run selected models/strategies to produce harmonized values.
  - Monitor progress, pause/cancel if needed, ensure policy compliance.
- **Key actions**
  - Start harmonization once mappings valid; review per-column progress bars.
  - Cancel or return to mapping if configuration needs adjustment; resume later if interrupted.
- **Affordances & feedback**
  - Overall progress indicator plus per-column progress (counts/percentages, remaining ETA).
  - Status badges for local vs external inference based on policy; warnings when egress disabled.
  - “Cancel and Edit Mappings” button; descriptive empty states while processing.
- **System work**
  - Execute harmonization pipeline using local and optional external models per policy.
  - Write intermediate outputs to durable storage; maintain audit logs and traces.
  - Update resumable state so users can resume mid-flight jobs.

---

## 4. Review & Approve Results (Data Work)
- **Primary goals**
  - Inspect harmonized values, resolve low confidence items, apply overrides.
  - Ensure high-quality output before export; log curator decisions.
- **Key actions**
  - Navigate batches sorted by confidence (lowest first); filter by column, change type, “needs review”.
  - Open cell detail modal to view original value, harmonized value, alternatives, confidence graph, model details; enter manual overrides.
  - Apply batch actions (approve all high confidence, mark batch complete) and track progress to 100%.
  - Trigger a targeted harmonization rerun for a missed column/model without restarting entire workflow.
- **Affordances & feedback**
  - Virtualized table showing record IDs, columns, confidence color bands (low/medium/high), override badges.
  - Batch navigation with stats (counts per confidence bucket); clear explanation of “batch”.
  - Undo/reset options, checkpoints, and workflow reset/back to earlier stages.
  - Tooltips/annotations when overrides differ from AI recommendation.
- **System work**
  - Record curator decisions, overrides, timestamps; update plan JSON outcomes and audit trail.
  - Support incremental save/export of approved batches; maintain resumable checkpoints.

---

## 5. Export & Finish
- **Primary goals**
  - Produce conformant dataset and supporting audit artifacts.
  - Provide summary of harmonization (counts, confidence distribution, overrides).
- **Key actions**
  - Download harmonized dataset (CSV/Parquet/Excel) and audit bundle (plan JSON, diffs, confidences, models, hash).
  - Optionally export diagnostics/support bundle; restart workflow for new file.
- **Affordances & feedback**
  - Completion screen with success state, summary stats, and links to downloads.
  - Links or buttons for “Download final results”, “Download audit log”, “Start new harmonization”.
- **System work**
  - Package results in durable storage; sign bundle if required; clear session or archive per retention policy.
