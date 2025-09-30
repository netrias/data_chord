# Proposal: Single-Image Data Harmonization Appliance + GUI Wrapper

## Summary
Deliver a single, multi-arch Docker image that hosts the full application (web UI + API + background jobs) and serves the guided harmonization workflow directly. Pair it with a lightweight desktop wrapper (technology TBD) that provides one‑click start/stop/update, preflight checks, automatic port selection, and optional offline Model Pack import. This keeps non‑technical UX simple while meeting DataHub’s “run a container” requirement and supporting on‑prem privacy.

## What We Ship
- Container image (linux/amd64, linux/arm64)
  - Runs UI, API, and jobs in one process space
  - Uses SQLite for local state; persists to volumes
  - Exposes `/ui`, `/api`, `/health`, `/ready`
  - Default privacy: local‑only; policy‑gated egress for zero‑shot
- Volumes
  - `datasets/` – uploaded files and outputs
  - `models/` – cached models (lazy pulls or imported packs)
  - `state/` – SQLite DB, audit trail, config
- Security & provenance
  - Cosign‑signed images; published SBOM and provenance
  - Minimal telemetry; PII redaction; audit trail of decisions and versions
- Optional GUI wrapper (technology TBD)
  - One‑click start; preflight (Docker/ports/GPU); auto‑pick port; write `.env`; open browser
  - Update flow: check channels (`stable`/`rc`), pull, health‑check, rollback on failure
  - Import Offline Model Packs and show progress/space usage
 

## Acceptance Criteria
- DataHub can run a single container and route non‑conformant uploads into a usable UI
- Non‑technical on‑prem users can launch locally via a simple installer and see the UI without CLI steps
- Models download on demand (or via Offline Model Packs), and the audit bundle is exportable and reproducible
- Privacy defaults to local‑only with clear, enforceable egress policies

 
## Acceptance Criteria
- Runs as a single container image that serves the web UI and API on a single localhost port by default.
- Cross‑platform support with multi‑arch images: linux/amd64 and linux/arm64; verified to run on Windows (WSL2), macOS (Intel/Apple Silicon), and Linux.
- DataHub compatibility: container can be hosted by DataHub and accept routed uploads; exposes endpoints suitable for both interactive sessions and headless API use when needed.
- Non‑technical usability: a simple GUI wrapper (technology TBD) can start/stop/update the container without CLI; automatically selects an open port, writes minimal configuration, and opens the browser.
- Minimal configuration: sane defaults; optional `.env` for advanced settings; no mandatory CLI flags for end users.
- Privacy by default: local‑only processing unless explicitly enabled; per‑task/column policy toggles for any external calls; PII redaction before any egress when enabled.
- Model handling: supports lazy model downloads to a local `models/` cache; supports importing signed Offline Model Packs for airgapped environments; visible progress and clear error handling.
- Persistence and audit: uses durable volumes (`datasets/`, `models/`, `state/`); exports an audit bundle including plan JSON, ontology/model versions, input hash, model confidences, and curator decisions; runs are reproducible given fixed versions.
- GPU optionality: detects GPU availability where applicable; runs correctly without GPU on CPU with clear time expectations; uses GPU acceleration when present without additional user steps.
- Health and diagnostics: provides basic health endpoints; structured, redacted logs; ability to generate a redacted support bundle without exposing raw data.
- Updates and provenance: supports pulling a new signed image and restarting cleanly with rollback on failure; publishes SBOM and image signatures.
- Performance expectations: handles typical dataset sizes for interactive review without timeouts; supports bulk actions and keyboard shortcuts for curation at scale.
- Accessibility and UX: responsive web UI with clear workflow steps; keyboard‑friendly interactions; conveys model confidence and conflicts clearly for human review.
