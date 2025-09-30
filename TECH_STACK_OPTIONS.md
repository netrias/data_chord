# Tech Stack Options: Guided, Table-Heavy Workflow

## Purpose
Explore mature, customizable, and performant stacks for a web-based, guided harmonization workflow where users review and edit tabular data. Favor proven ecosystems that handle large tables smoothly, with a Python-friendly backend for data manipulation.

## Design Priorities
- GUI-first: rich web UI, not CLI
- Table-first UX: fast scrolling, inline editing, bulk actions, keyboard shortcuts
- Customizable: deep control over cells, validators, renderers, selection, shortcuts
- Mature/stable: long-lived communities, good docs, clear licensing
- Performance: virtualization, server-side pagination, efficient transport
- Python-friendly backend: leverage Pandas/Polars/DuckDB/Arrow; easy API surface
- Privacy by default, optional egress for zero-shot tasks

---

## Frontend Frameworks (UI Shell)
- React + TypeScript
  - Pros: most mature ecosystem; strongest grid/table options; abundant talent; great tooling (Vite, eslint, TS)
  - Cons: more boilerplate than meta frameworks; need to choose grid and state libs
  - Fit: Best all-around choice for complex editable tables and custom workflows
- Vue 3 + TypeScript
  - Pros: approachable; solid ecosystem; good table libs (AG Grid, Handsontable integrations)
  - Cons: fewer enterprise grid examples vs React; team familiarity varies
  - Fit: Viable alternate if team prefers Vue semantics
- Svelte/SvelteKit
  - Pros: lean; great DX; emerging data grid support
  - Cons: fewer mature, enterprise-grade editable grids; smaller community
  - Fit: Consider only if grid choice is confirmed to meet requirements
- Newer Python-first UI (e.g., Panel/fastHTML/NiceGUI/Streamlit/Dash)
  - Pros: easy Python integration; fast to start
  - Cons: limited deep customization for large editable tables; performance/latency concerns at scale
  - Fit: Not recommended as primary UI shell for heavy table interaction

Recommendation: React + TypeScript as the default UI shell.

---

## Data Grid Libraries (React-centric)
- AG Grid (Community/Enterprise)
  - Pros: most feature-rich; excellent virtualization; editing, grouping, filtering, pivoting; strong docs; large user base
  - Cons: Enterprise features require a license (e.g., pivoting, advanced filters); bundle size larger
  - Fit: Top pick when you need robust, Excel-like behavior and deep customization
- MUI Data Grid (MUI X)
  - Pros: strong integration with MUI design system; virtualization; editing; good docs
  - Cons: Some advanced features are Pro/Premium; less flexible than AG Grid for bespoke editors
  - Fit: Great if you want MUI everywhere and solid grid capability
- Handsontable
  - Pros: Excel-like editing feel; robust editing/validation; good performance
  - Cons: commercial license for many use cases; React wrapper is good but adds lock-in
  - Fit: Strong option when “Excel in browser” UX is paramount and licensing is acceptable
- Glide Data Grid
  - Pros: high-performance canvas-based grid; large datasets; MIT licensed
  - Cons: fewer built-in enterprise features; more DIY for editors/menus
  - Fit: Favor when raw performance matters and you can implement custom behaviors
- TanStack Table (+ virtualization)
  - Pros: headless flexibility; pair with react-virtual/virtuoso; composable
  - Cons: you must build editors, selection, and many behaviors; greater implementation effort
  - Fit: When you want absolute control and can invest engineering time
- Tabulator
  - Pros: mature, featureful, framework-agnostic; good performance
  - Cons: React integration is wrapper-based; theming/editing not as seamless as AG Grid
  - Fit: Viable alternative, especially outside React

Recommendation: Start with AG Grid Community; step up to Enterprise only if/when features (grouping/pivot) are required. Alternatives: MUI Data Grid or Glide Data Grid based on design direction.

---

## Backend (APIs + Data Ops)
- FastAPI (+ Uvicorn/Starlette)
  - Pros: Pythonic, type-hinted; OpenAPI/Swagger; async; great perf for I/O-bound APIs; easy to serve static assets
  - Cons: none material for our needs
  - Fit: Default backend choice
- Data processing: Pandas + Polars + DuckDB + Arrow (complementary, not exclusive)
  - Pandas
    - Pros: ubiquitous; Excel I/O via openpyxl/xlrd; rich ecosystem
    - Cons: slower on very large data; memory-bound
    - Use: reading/writing Excel; light transforms
  - Polars
    - Pros: very fast; parallel; Arrow-native; great for columnar transforms
    - Cons: Excel support indirect (convert via Pandas/pyarrow)
    - Use: heavy transforms; profiling; inference prep
  - DuckDB
    - Pros: in-process OLAP; SQL on CSV/Parquet/Arrow; great for joins/aggregations and larger-than-memory via spilling
    - Cons: different mental model (SQL) vs DataFrame ops
    - Use: complex joins/aggregations; server-side pagination and viewport queries
  - Apache Arrow
    - Pros: zero-copy columnar interchange; efficient transport; Parquet/Feather
    - Cons: adds type discipline to enforce
    - Use: backend <-> frontend data exchange; fast serialization

Recommendation: FastAPI + (Pandas for Excel I/O) + Polars for transforms + DuckDB for heavy joins/aggregation. Use Arrow/Parquet for efficient slices and exports.

---

## Frontend ↔ Backend Data Transport
- JSON (baseline)
  - Pros: simple; ubiquitous
  - Cons: inefficient for large tables; loses types; slower
  - Fit: small payloads, metadata, commands
- Arrow IPC/Feather/Parquet (binary)
  - Pros: columnar, typed, compact; fast (de)serialization; supports chunking
  - Cons: requires client libs/parsers; more plumbing in FE
  - Fit: high-performance viewport data; bulk downloads/exports
- Pagination models
  - Server-side pagination for editing sessions (cursor or offset)
  - Viewport streaming (range-based) + virtualization in FE

Recommendation: JSON for small control payloads; Arrow/Parquet for large slices/exports.

---

## State Management and UX Patterns (React)
- State: React Query (TanStack Query) for API/cache; lightweight local state (Zustand) for UI controls
- Virtualization: react-window/react-virtuoso for smooth scrolling
- Forms/validation: React Hook Form + Zod for schemas; column-level validators
- Accessibility: use ARIA patterns; keyboard-first editing; focus management
- Internationalization: i18n-ready labels and date/number formats

---

## Authentication and Packaging
- Auth (baseline): cookie-based sessions or JWT (if embedding in DataHub, support header-based auth or signed tokens)
- Packaging: build FE with Vite; emit static assets bundled into the Python image (served by FastAPI)
- Single-image delivery: multi-stage Docker build with `uv` for Python deps; static FE served at `/ui`

---

## Alternative Architectures (Considered, with caveats)
- Fully frontend data processing (WASM: DuckDB-WASM, Pyodide, Arquero)
  - Pros: privacy (local in browser); snappy for mid-size data; no server round-trips
  - Cons: memory limits; large WASM downloads; complex persistence; browser variability
  - Verdict: Interesting for future offline mode prototypes; not baseline
- Python UI frameworks (Panel, Dash, Streamlit, NiceGUI, fastHTML)
  - Pros: fast to build
  - Cons: limited deep table customization and performance under heavy editing; callback complexity
  - Verdict: Not recommended as the primary UI for a table-heavy editor
- Node/NestJS backend
  - Pros: homogeneous JS stack
  - Cons: data ops best-in-class are in Python/Arrow/Polars; team familiarity
  - Verdict: Not a fit given Python strengths in data manipulation

---

## Recommended Stack (Today)
- Frontend
  - React + TypeScript + Vite
  - Data grid: AG Grid Community (first), or MUI Data Grid/Glide Data Grid based on design constraints
  - Virtualization: built-in (AG Grid/MUI) or react-virtuoso where needed
- Backend
  - FastAPI (+ Uvicorn)
  - Pandas for Excel I/O; Polars for transforms; DuckDB for complex queries; Arrow for interchange
- Packaging
  - Single Docker image (multi-arch) serving static FE and FastAPI at one port
  - Volumes: `datasets/`, `models/`, `state/`
- Future wrapper
  - GUI wrapper (technology TBD) to start/stop/update the container and import Offline Model Packs

---

## Open Questions
- Which grid features are must-have at launch (grouping, pivoting, frozen columns, copy/paste, undo/redo, custom editors)?
- Typical dataset sizes (rows/columns) and acceptable latency budgets per operation?
- Do we need offline-first (browser-only) sessions for any users?
- Licensing constraints for AG Grid/MUI X/Handsontable if advanced features are required?
- Preferred transport for large slices (Arrow IPC vs Parquet downloads) in the initial milestone?
