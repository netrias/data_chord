# Considered Options (And Decisions)

## Context & Constraints
- Core requirement: a GUI for a guided, human‑in‑the‑loop workflow.
- Typical load is light; heavy inference is occasional.
- DataHub explicitly wants a Docker container; non‑technical users struggle with CLI.
- Cross‑platform support (Windows/Mac/Linux) with minimal friction.
- Privacy by default; optional, policy‑gated egress for zero‑shot.

## Kept (Current Direction)
- Single Docker image hosting UI + API + jobs, serving the web UI directly
  - Why kept: Matches workload; simplest to distribute and run; works for DataHub and on‑prem.
  - Details: SQLite for state; volumes `datasets/`, `models/`, `state/`; endpoints `/api`, `/ui`, `/health`.
- GUI Desktop Wrapper (future) to start/stop/update the image
  - Why kept: Eliminates CLI friction; handles port selection, `.env`, and updates; imports offline Model Packs.
  - Likely tech: Electron (mature, broad ecosystem). Tauri is attractive but deferred (see below).
- Model distribution: Lazy model pull (connected) + Offline Model Packs (airgapped)
  - Why kept: Small initial footprint; deterministic offline option with signatures.
- GPU handling: Optional preflight with CPU fallback
  - Why kept: Inference is rare; avoid complexity unless GPU is present.

## Eliminated (For Baseline)
- Multi‑container Compose stack
  - Why eliminated: Overhead without clear benefit at current scale; baseline is mostly UI + light jobs.
- Kubernetes + Helm
  - Why eliminated: Operationally heavy for laptops/servers; not needed for baseline.
- Docker Desktop Extension
  - Why eliminated: Ties users to Docker Desktop; excludes many Linux installs; adds packaging channel complexity.
- CLI‑only wrappers (Rust/Go)
  - Why eliminated: A GUI is a core requirement; CLI can exist for IT later but not as primary.
- Tauri (Rust) GUI wrapper
  - Why eliminated now: Attractive footprint, but maturity/long‑term risk; deferring until the ecosystem is more proven for our needs.
- .NET MAUI GUI
  - Why eliminated: Heavier runtime; cross‑platform friction; not aligned with lightweight goals.
- Java/JavaFX GUI
  - Why eliminated: JRE bundling bloat; “heavier” user experience relative to needs.
- Python + PyInstaller GUI (PySide/PyQt)
  - Why eliminated: Large bundles, dependency issues, frequent AV false positives.
- Bundling a container runtime (containerd/nerdctl/Lima inside the app)
  - Why eliminated: Heavy, complex, platform quirks; prefer leveraging existing Docker/Podman.
- Watchtower auto‑update
  - Why eliminated: Less control over rollouts; wrapper‑managed updates are clearer and safer for users.
- Dedicated model servers (vLLM/TGI) as a separate service
  - Why eliminated: Inference is rare; unnecessary complexity for baseline. Revisit only if usage changes.

## Retained as Fallbacks
- Podman/nerdctl support
  - Rationale: Detect alternatives when Docker Desktop/Engine isn’t present and adapt run commands.
- Remote inference connectors (OpenAI/Anthropic or managed endpoints)
  - Rationale: Optional offload for zero‑shot; strictly policy‑gated and redacted when enabled.

## Decision Summary
- Keep it simple: single multi‑arch image serving the web UI, plus an eventual Electron wrapper for one‑click UX.
- Prefer lazy model pull with signed Offline Model Packs for airgapped users.
- GPU is optional; rely on CPU with clear ETAs if no GPU is available.
- Everything else (Compose/K8s/Extensions) is deprioritized unless scale or specific platform mandates it later.
