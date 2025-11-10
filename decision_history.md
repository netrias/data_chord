<!-- # Decision History (Condensed)

## Baseline Deployment Strategy
- **Decision**: Ship a single multi-arch Docker image (linux/amd64, linux/arm64) that serves both the web UI and API on one port, with an optional desktop wrapper (technology TBD) for one-click launch.
- **Rationale**: Aligns with DataHub’s container requirement, keeps distribution simple for non-technical users, and matches the workload (light UI with occasional inference).
- **Implications**:
  - Local volumes `datasets/`, `models/`, `state/` for persistence, auditability, and offline use.
  - Lazy model downloads with signed Offline Model Packs for airgapped deployments.
  - Optional GPU acceleration; CPU fallback with realistic ETAs when GPUs are absent.

## Wrapper and Distribution Options
- **Kept**: Desktop wrapper (future) to start/stop/update the container, surface preflight checks, and manage model packs. Consider Electron vs alternatives later.
- **Eliminated for baseline**:
  - Multi-service docker-compose or Kubernetes/Helm stacks (overhead without current benefit).
  - Docker Desktop Extension (platform-limited, adds packaging complexity).
  - CLI-only wrappers (a GUI is a core requirement).
  - Python-first GUI frameworks (Panel/Streamlit/etc.) due to limited table customization/performance.
  - Bundled container runtimes (heavy, more support issues).

## Frontend Technology Choices
- **Selected**: React + TypeScript + Vite (later) with a high-performance data grid.
  - Enables deep customization, strong ecosystem, abundant talent, mature tooling.
  - Supports virtualization, keyboard-first workflows, and component composition.
- **Data grid**: AG Grid Community as the default, with MUI Data Grid or Glide Data Grid as fallbacks based on design needs.
- **Deferred/Rejected**:
  - Vue/Svelte: capable but smaller grid ecosystem for enterprise-grade editing.
  - Handsontable (licensed) unless Excel-like experience becomes critical.
  - TanStack Table alone (too much DIY for current timeline).

## Backend Technology Choices
- **Selected**: FastAPI (Python) with Pandas, Polars, DuckDB, and Arrow.
  - FastAPI supplies async APIs, OpenAPI docs, and straightforward integration with the UI.
  - Pandas handles Excel I/O; Polars accelerates columnar transforms; DuckDB supports SQL-style joins/aggregations; Arrow provides efficient interchange to the UI.
- **Deferred/Rejected**:
  - Node/NestJS backend: weaker DataFrame tooling and less alignment with existing Python expertise.
  - Heavy microservice splits: unnecessary at current scale; revisit when SaaS multi-tenancy demands it.

## Data Transport
- **Decision**: Use JSON for small control payloads and Arrow/Parquet (binary) for bulk table slices and exports.
- **Reasoning**: Balances ease of integration with high-performance transfers for large, virtualized tables.

## Privacy & Policy Handling
- Local-only processing by default with explicit, per-column/task toggles for any external calls.
- PII redaction prior to egress; policy decisions logged without raw data exposure.

## Observability & Support
- Structured, redacted logs with correlation IDs, health endpoints, and redacted support bundles.
- Signed container images and model packs with SBOM/provenance for enterprise trust.

## Outstanding Questions
- Priority ontologies/standards and their version cadence.
- Minimum viable offline capabilities (which zero-shot features must work without egress).
- Preferred persistence strategy for on-prem installs (SQLite vs Postgres) and backup expectations.
- SLA/SLO targets for DataHub vs commercial on-prem environments. -->
