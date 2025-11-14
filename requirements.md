# Requirements: Data Harmonization Guided Workflow (Initial Draft)
Context:
We are creating a data harmonization application with a guided workflow. (there may be more screens than steps, steps mostly constitute discrete
work the user must do)
Step 1:
Users will upload a single (for now csv) file.
There will be an "Analyze button" that will make an API call to determine the best model to harmonize each column
progressing the system to step 2.
Step 2: 
Users will review the column to model mapping. They can leave suggestions as is or make manual adjustments.
On completion of the work, they can say "Harmonize" which will progress to step 3
Step 3:
Harmonization loading/status
On completion, we will proceed to a review screen.
This will show some overview stats--total number of harmonized elements, perhaps confidence metrics etc.
We will then proceed to the manual review step.
Step 4.
Users will be presented with a tabular, batch-oriented review ui, they can make manual corrections as needed until they are finished harmonizing
Finally, they can download the data--one button will download
the original sheet but with harmonization
an expanded sheet that includes columns that include metadata, like the other top suggestions for the row, confidence score, etc. 
@mockups contains our starting prototype *example* we will not modify that code, but we can reference it as a starting point for real work.


## Scope
- Provide a web-based, GUI-first application to harmonize tabular data (CSV/Excel) to target standards via automated suggestions and human review.
- Deliver as a single Docker image that serves the web UI and API on one port; later, an optional GUI wrapper may orchestrate startup.

## Workflow Stages
- Upload Data
  - Accept CSV; surface file name, size, sheet selection (for Excel).
  - Provide clear guidance text and call-to-action (“Analyze Columns”).
  - See @cde_recommendation_endpoint.md
  - Validate format and encoding; show friendly errors with remedies.
- Review & Confirm Column→Model Mappings
  - Display detected columns with AI-suggested target models and confidence (bucketed: low/medium/high).
  - Allow manual override of model per column; visually distinguish overrides and show the original suggestion.
  - Support sorting columns (e.g., by confidence, alphabetical, detected type) and filtering (e.g., low confidence, changed/overridden, unmapped).
  - Provide column-level data preview; clearly signpost which column is being previewed; allow adding context columns to the preview.
  - Provide hover/selection states for columns; ensure columns are visually represented as columns (not rows).
  - Offer help text and tips early in this step; consistent, accessible color palette.
- Harmonize (Execute Plan)
  - Show loading indicator while harmonization is occurring; allow cancel/return to mapping.
  - We will install this library and use it to make the harmonization call: https://pypi.org/project/netrias-client/
- Review & Approve Results (Data Work)
  - Present a tabular, batch-oriented review UI sorted by lowest confidence first; show low/medium/high color bands.
  - Enable cell-level inspection: original value, harmonized value, confidence bar, top model, alternatives, and manual override entry.
  - Provide batch actions (e.g., approve all high-confidence); mark batch complete; show overall review progress.
  - Allow alternative sorting/grouping modes (e.g., by column, by change type); clarify “batch” meaning in UI.
  - Allow running an additional harmonization on a missed column/model directly from this page; support checkpoints and workflow reset.
- Export
  - Export conformant dataset and an audit bundle (plan JSON, diffs/overrides, confidences, model/ontology versions, input hash).


## Models & Policies
- Model Selection
  - Support AI suggestions and manual selection per column from a known catalog of models.
  - Allow “skip column” choice.
- Confidence
  - Present confidence as buckets (low/medium/high) (note actual numbers will be 0.0-1.0 Low will be 0-0.4, medium = 0.5-0.7, high 0.8-1);

## UI/UX
- Clarity
  - Clear stage indicators; consistent naming (“Analyze Columns” → mapping; “Harmonize” → execution; “Review Results” → data work).
- Feedback items (from mockups)
  - Add explanatory text on upload and mapping; consistent colors; more distinctive overrides.
  - Sorting/filtering controls on mapping screen; data preview optionally on the right; add hover/selection states.
  - Allow manually adding context columns to previews; clearly show which column is active.
  - Clarify row vs column representation in review; clarify “batch” terminology.
- Branding
  - Use Netrias colors

## Deployment
- Single Docker image serving UI on some port
- Pyinstaller creates beautiful launch application that basically just launches the docker image and opens a tab in browser to view it



## Out of Scope (for now)
- Multi-service orchestration (Compose/Kubernetes) as baseline.
- Dedicated model-serving cluster; cloud bursting.
- Offline browser-only processing (WASM) beyond early experiments.
- Excel support
- DataHub compatibility: container can be hosted by DataHub and accept routed uploads; exposes endpoints suitable for both interactive sessions and headless API use when needed.
