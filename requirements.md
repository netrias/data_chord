# Requirements: Data Harmonization Guided Workflow (Initial Draft)

## Scope
- Provide a web-based, GUI-first application to harmonize tabular data (CSV/Excel) to target standards via automated suggestions and human review.
- Deliver as a single Docker image that serves the web UI and API on one port; later, an optional GUI wrapper may orchestrate startup.

## Workflow Stages
- Upload Data
  - Accept CSV and Excel files; surface file name, size, sheet selection (for Excel).
  - Provide clear guidance text and call-to-action (“Analyze Columns”).
  - Validate format and encoding; show friendly errors with remedies.
- Review & Confirm Column→Model Mappings
  - Display detected columns with AI-suggested target models and confidence (bucketed: low/medium/high).
  - Allow manual override of model per column; visually distinguish overrides and show the original suggestion.
  - Support sorting columns (e.g., by confidence, alphabetical, detected type) and filtering (e.g., low confidence, changed/overridden, unmapped).
  - Provide column-level data preview; clearly signpost which column is being previewed; allow adding context columns to the preview.
  - Provide hover/selection states for columns; ensure columns are visually represented as columns (not rows).
  - Offer help text and tips early in this step; consistent, accessible color palette.
- Harmonize (Execute Plan)
  - Show overall progress and per-column progress; allow cancel/return to mapping.
  - Respect egress policy toggles per column/task; proceed locally when disabled.
  - Persist intermediate state for resumability.
- Review & Approve Results (Data Work)
  - Present a tabular, batch-oriented review UI sorted by lowest confidence first; show low/medium/high color bands.
  - Enable cell-level inspection: original value, harmonized value, confidence bar, top model, alternatives, and manual override entry.
  - Provide batch actions (e.g., approve all high-confidence); mark batch complete; show overall review progress.
  - Allow alternative sorting/grouping modes (e.g., by column, by change type); clarify “batch” meaning in UI.
  - Allow running an additional harmonization on a missed column/model directly from this page; support checkpoints and workflow reset.
- Export
  - Export conformant dataset and an audit bundle (plan JSON, diffs/overrides, confidences, model/ontology versions, input hash).

## Data Handling
- Ingestion
  - CSV: delimiter/quote/encoding handling; robust error messages.
  - Excel: sheet selection; basic date/number parsing; preserve headers.
- Profiling
  - Detect columns needing harmonization; infer types/semantics where feasible; record confidence.
- Plan JSON
  - Emit a machine-readable plan describing column mappings, tactics (model/rules), and execution order; include versions and hashes for reproducibility.
- Persistence
  - Store session state, plan, decisions, and progress in durable storage to resume work.

## Models & Policies
- Model Selection
  - Support AI suggestions and manual selection per column from a known catalog of models.
  - Allow “skip column” choice.
- Confidence
  - Present confidence as buckets (low/medium/high); show numeric values where helpful but keep buckets primary.
- Egress Controls
  - Global and per-column/task toggles for external calls; default to local-only; enable PII redaction when egress is allowed.
- Model Delivery
  - Lazy download to a local cache on first use; support signed offline Model Packs for airgapped environments.

## UI/UX
- Table-first
  - High-performance, virtualized tables with inline editing, and bulk actions.
- Clarity
  - Clear stage indicators; consistent naming (“Analyze Columns” → mapping; “Harmonize” → execution; “Review Results” → data work).
- Feedback items (from mockups)
  - Add explanatory text on upload and mapping; consistent colors; more distinctive overrides.
  - Sorting/filtering controls on mapping screen; data preview optionally on the right; add hover/selection states.
  - Allow manually adding context columns to previews; clearly show which column is active.
  - Clarify row vs column representation in review; clarify “batch” terminology.
- Accessibility
  - Keyboard-first workflows; ARIA-compliant components; sufficient contrast.
- Branding
  - Support configurable product name and color theme.

## Performance & Scale
- Handle typical datasets for interactive use with low-latency operations (upload, mapping list, previews, batch navigation).
- Use server-side pagination/viewport queries for large tables; stream slices efficiently.
- Provide realistic ETA messaging for CPU-mode inference when no GPU is present.

## Privacy & Security
- Local-only by default; explicit opt-in for any external calls; capture policy decisions without logging raw cell contents.
- Redact or obfuscate PII before any egress when enabled.
- Signed images and Model Packs; publish SBOMs/provenance; allow internal mirroring.

## Observability & Support
- Health endpoints for readiness/liveness.
- Structured, redacted logs with correlation IDs.
- Generate a redacted support bundle containing config/versions/metrics without raw data.

## Integration
- DataHub
  - Support headless entry (API) and interactive handoff (preloaded session) using a single container image.
  - Return conformant dataset and audit bundle to the caller or provide download links.

## Deployment
- Single Docker image serving UI and API on one port; multi-arch (linux/amd64, linux/arm64).
- Use volumes `datasets/`, `models/`, `state/` for persistence.
- Optional GUI wrapper (technology TBD) may orchestrate startup, port selection, updates, and offline pack import.

## Out of Scope (for now)
- Multi-service orchestration (Compose/Kubernetes) as baseline.
- Dedicated model-serving cluster; cloud bursting.
- Offline browser-only processing (WASM) beyond early experiments.
