# Data Harmonization: Use Cases and Challenges

## Purpose
Summarize the target use cases, end-to-end flow, and core challenges for a data/metadata harmonization application that ingests tabular data (CSV, Excel, other tables) and standardizes it against a chosen ontology/standard via automated and human-in-the-loop steps.

## Use Cases
- DataHub Remediation Container
  - Provide a Docker container DataHub can route non-conforming uploads to.
  - Users remediate data in an interactive, guided workflow and export conformant outputs.
  - Non-interactive API likely also needed for batch/automated cases.
- On-Prem (Local) Vendor Deployments
  - Ship a self-hosted package so data never leaves the premises.
  - Leverage local GPUs if present for performance; degrade gracefully to CPU.
  - Balance privacy with optional zero-shot calls to external model providers.
- Hosted Web Portal (SaaS)
  - Public UI where users upload data, choose a target ontology, and complete a guided correction workflow.
  - Supports both fully automated and review/curation steps; captures feedback for model improvement.

## End-to-End Flow (Functional)
- Ingest
  - Accept tabular datasets (CSV, Excel, parquet, others) and associated metadata.
- Profile & Select Columns
  - Determine which columns require harmonization; which are already compliant.
- Plan (Processing Strategy)
  - Select models/strategies per column (zero-shot, fine-tuned, rules, lookups).
  - Emit a machine-readable plan (JSON) describing the mapping, tactics, and sequence.
- Execute
  - Apply models/transformers; produce proposed conformant values and confidence signals.
- Human-in-the-Loop Review
  - Provide a guided UI to accept/correct outputs; surface conflicts & low-confidence items.
  - Capture curator feedback as training/evaluation data (close the loop).
- Export & Report
  - Generate conformant outputs and an audit trail (mappings, overrides, confidences, provenance).
- Learn
  - Feed corrections into fine-tuned models; update rules/ontologies/versioning.

## Core Challenges
- Distribution & Updates
  - Non-technical users struggle with CLI, port mapping, and container args.
  - No automatic update mechanism for on-prem images; manual pulls are brittle.
- GPU Acceleration
  - Enabling NVIDIA GPU passthrough reliably on diverse hosts is non-trivial.
  - CPU-only inference on large datasets is slow; need graceful fallback and clear UX.
- Model Packaging & Size
  - Many column-specific models; bundling all into a single image is huge.
  - Shipping a few models per image limits usefulness; many standards require dozens.
- External Zero-Shot Calls
  - Some strategies call external LLMs (OpenAI/Anthropic), requiring egress and careful privacy controls.
  - Airgapped environments need alternatives or policy toggles to disable external calls.
- Privacy & Compliance
  - Sensitive data must not be transmitted, logged, or persisted beyond policy.
  - Need PII redaction, configurable retention, and transparent auditing.
- Performance & Scale
  - Large files, many columns, and high row counts stress memory/latency.
  - Batch vs interactive workloads demand different resource strategies.
- Observability & Supportability
  - Diagnostics in customer environments without exposing data (structured logs, telemetry controls).
  - Clear error surfacing and guided remediation when models fail or misbehave.
- Standards & Ontologies
  - Multiple target schemas, versions, and evolving definitions; need robust versioning and migration.
  - Column semantics can vary by source domain; require detection, heuristics, or learned classifiers.
- Data Formats & I/O
  - Need consistent handling of CSV/Excel/Parquet; large file streams; encoding and locale issues.
- Reproducibility & Audit
  - Deterministic runs tied to ontology/model versions; store plan JSON and hashes for traceability.
  - Export an audit trail of suggestions, human edits, and final decisions.
- UX & Workflow
  - Guided steps must be frictionless for non-technical users.
  - Support keyboard-first curation at scale (thousands of terms) with bulk actions.
- Integration with DataHub
  - Define handoff interface (inputs, plan, outputs) and interactive vs headless entry points.
  - Authentication/authorization model when launched from DataHub context.

## Non-Functional Requirements (NFRs)
- Security by default: least privilege, secrets management, minimal telemetry, signed images.
- Portability: Linux/Mac/Windows; x86_64 and arm64; works with/without GPUs.
- Maintainability: modular components; model registry; clear versioning; CI for images.
- Reliability: resumable jobs, idempotent operations, persistent volumes for state.
- Usability: one-click or minimal-step startup for local deployments.

## Success Criteria
- DataHub can run the container and route non-conforming uploads into a usable, guided workflow.
- On-prem users can start locally without CLI expertise; models load on demand; updates are simple.
- Hosted portal scales multi-tenant; provides strong privacy controls and auditability.
- Measurable uplift in conformance quality; feedback loops demonstrably improve models over time.

## Open Questions
- Which ontologies/standards are priority and what are their version cadences?
- Minimum viable offline experience: which zero-shot features must work without egress?
- Preferred persistence model for on-prem (SQLite vs Postgres) and backup expectations?
- SLA/SLO expectations for DataHub and for commercial on-prem support?
