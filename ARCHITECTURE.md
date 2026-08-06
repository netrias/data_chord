# Data Chord Architecture

Data Chord turns a tabular research dataset into a reviewed, standardized
dataset. The user chooses a Common Data Element (CDE) model, confirms how source
columns map to that model, runs Netrias harmonization, reviews value changes,
and downloads the result with its audit evidence.

This document describes the durable system boundaries. Product behavior is
summarized in [app.md](app.md), and hosted operations are in
[infra/README.md](infra/README.md).

## System shape

```text
Browser
  |
  v
FastAPI stage routers and schemas
  |
  v
Application use cases
  |-----------------------------|
  v                             v
Domain decisions           Boundary protocols
                                |-------------|
                                v             v
                         Persistence      Netrias adapters
                                |
                                v
                         WorkflowStorage
                         (local or S3)
```

The browser presents the five-stage workflow. Routers translate HTTP input and
errors. Use cases coordinate a user operation. Domain types own workflow state,
mapping identity, permissible-value rules, and manifest meaning. Persistence
translates those types to durable artifacts. Integrations translate Netrias
responses into domain types.

Dependencies point inward: stage modules may depend on domain, application,
persistence, integrations, and storage boundaries; the domain does not import a
stage or web framework. The backend app factory wires routers and concrete
implementations.

## Main ownership boundaries

| Area | Owns | Does not own |
| --- | --- | --- |
| `backend/app` | FastAPI construction, middleware, static mounts, router registration, boundary error responses | Workflow decisions |
| `src/stage_1_upload` | Upload and analysis HTTP flow | Durable storage layout |
| `src/stage_2_review_columns` | Mapping-review operations and HTTP flow | Harmonization execution |
| `src/stage_3_harmonize` | Durable job lifecycle and harmonization orchestration | Browser-only progress state |
| `src/stage_4_review_results` | Review queries and active override commands | Final ZIP construction |
| `src/stage_5_review_summary` | Final summary and download package | Mapping discovery |
| `src/domain` | Stable IDs, mapping choices, workflow state, manifests, PV rules, review meaning | SDK, S3, FastAPI |
| `src/app` | Service construction and short-lived reference-data cache | Durable workflow truth |
| `src/integrations` | Netrias request/response translation | User authorization or persistence |
| `src/persistence` | Domain-to-artifact translation | Backend selection |
| `src/storage` | Authorization, artifact naming, version checks, local/S3 parity | Stage behavior |
| `src/shared/static` | Small browser primitives used by several stages | Feature-specific UI behavior |

## Workflow

### 1. Upload and analyze

The upload endpoint creates the workflow owner record before storing workflow
data. It validates and stores the original CSV, TSV, or XLSX file and its
metadata. Analysis profiles columns, handles worksheet selection, asks mapping
discovery for CDE candidates, and stores a complete `WorkflowState` containing
the selected model version and discovered column mapping manifest.

The browser may retain display data for a fast handoff, but browser storage is
not an authority for ownership or confirmed workflow decisions.

### 2. Confirm column mappings

Stage 2 reads the authorized workflow and presents one stable column identity
per source position, so duplicate headers remain distinct. The user can accept a
recommendation, choose another CDE, choose no mapping, and rename an output
column.

The confirm command updates `WorkflowState` with an optimistic version check.
A stale browser receives a conflict instead of silently replacing a newer
decision. Starting harmonization persists those choices and creates or reuses a
durable job before the browser navigates to Stage 3.

### 3. Harmonize

Stage 3 loads the authorized durable state, derives the effective mapping plan,
and calls Netrias with a prepared mapping manifest. A `StageThreeJobState`
records the accepted plan, status, lease, and progress independently of process
memory. Polling therefore survives a worker or deployment restart.

A worker heartbeats its lease and may publish only while it still owns that
lease. The job is marked successful only after the harmonized output, base
Parquet manifest, permissible-value data, and CDE mapping audit are durable.
Provider failures become a durable failed job with a safe public message.
Retrying an interrupted or failed job does not require reconstructing its input
from browser state.

### 4. Review results

Stage 4 derives target mappings from `WorkflowState`, then combines the base
manifest, permissible values, source row context, and active review overrides.
The browser reviews by column or row, including keyboard and focus behavior for
large PV lists.

Two records remain deliberately distinct:

- `ReviewOverrides` is the active decision set applied to export. It uses an
  ETag/version token so a stale tab receives a conflict.
- Manual changes appended to the Parquet manifest are historical audit events.
  Deleting active overrides resets export behavior but does not erase history.

Saving the same active override again is idempotent: it does not create another
audit event.

### 5. Summarize and download

Stage 5 resolves the visible summary from the same current decisions used for
download: the harmonized result plus active `ReviewOverrides`. Character case
and whitespace are meaningful, so both Stage 3 and Stage 5 use the shared
`column_outcomes` domain rules for distinct values changed, rows affected, and
final-value review status. Columns retain stable source identity and source
order even when display labels repeat.

The Parquet manifest remains the historical audit source for the decision
history dialog; it does not control the current final value. Deleting active
overrides therefore resets both export values and current summary counts while
preserving the earlier reviewer events as history. The download streams a ZIP
containing the final tabular file, a JSON representation of the manifest, and
the CDE mapping audit document.

## Durable state and derived data

| Record | Purpose | Update rule |
| --- | --- | --- |
| Workflow metadata | Workflow ID, owner, creation time, storage schema | Create once |
| `WorkflowState` | Model version, discovered mapping manifest, confirmed choices, schema version | Optimistic compare-and-swap |
| `StageThreeJobState` | Plan identity, run status, worker lease, result or safe failure | Optimistic compare-and-swap |
| Original upload | Source evidence | Create once |
| Harmonized output | Netrias output used for review/export | Safe replacement by the active job |
| Base Parquet manifest | Transformation evidence and manual audit history | Durable append/update operations |
| PV manifest | Permissible values recovered after cache loss | Replaced for the selected model version |
| CDE mapping audit | Stable output document for the download | Rebuilt after complete mapping metadata exists |
| `ReviewOverrides` | Current export choices and UI progress | Versioned save/delete |

`SessionCache` accelerates CDE catalogs, permissible values, and column
profiles. Its identity includes the workflow owner and workflow ID, and cached
reference data is bound to one model version. Mapping truth is derived from
`WorkflowState`; it is not independently synchronized into the cache. A
fetched empty PV set is distinct from a PV set that has not been fetched.

## Storage and authorization

`WorkflowStorage` is the shared contract for local and S3 implementations. It
owns:

- workflow ownership checks before artifact access;
- semantic artifact names rather than caller-built paths or S3 keys;
- create-once versus replaceable artifact rules;
- version tokens for optimistic JSON writes;
- non-destructive artifact replacement.

Hosted deployments use S3. Local development uses the same contract on disk.
The only legacy ownerless fallback is limited to the local backend and the
`local-user` identity; it is never available in S3. Old split workflow-state
and mapping records remain readable during the compatibility expansion, while
new writes use `WorkflowState` and continue emitting the established mapping
artifact needed by older binaries.

Authorization is established from durable workflow metadata before a cache or
local scratch path is consulted. A missing workflow is different from a
workflow owned by another user.

## Failure contract

The HTTP boundary keeps failure meanings stable:

| Condition | Result |
| --- | --- |
| Invalid request | 422; no durable write |
| Missing workflow, upload, state, or run | 404 |
| Workflow owned by another user | 403 |
| Stale workflow or review version | 409 |
| Same active Stage 3 plan submitted twice | Existing job, 200 |
| Different plan submitted while one is active | 409 |
| Storage unavailable before job acceptance | 503 |
| Provider timeout, network failure, 5xx, or malformed response | Durable failed job; safe 502-class detail |
| Corrupt or unsupported durable schema | Conflict/error; record is not overwritten |

Public errors contain useful operation and workflow identifiers but do not
expose provider exception text, secret values, internal URLs, or storage keys.

## Frontend

The UI is server-rendered Jinja with small vanilla ES modules; there is no
bundler or client framework. Feature modules remain with their stage. Shared
modules contain only cross-stage primitives such as storage keys, HTML escaping,
step instructions, event/timing helpers, design tokens, and the semantic
column-outcome table shared by Stages 3 and 5. Stage-specific adapters translate
their API responses into that table's small display contract; the renderer does
not define change or conformance policy.

Browser state improves responsiveness and remembers the job being polled. It
does not authorize a workflow, replace durable mapping choices, or determine
whether a harmonization run succeeded.

## Hosted deployment

The hosted stack runs one FastAPI container image on ECS Fargate behind an
Application Load Balancer. Cognito protects normal HTTPS traffic; an explicitly
configured VPN CIDR listener rule remains available for controlled onboarding
or recovery. Bypassed requests have no ALB identity headers and therefore share
the fallback `local-user` principal; they do not provide per-person workflow
isolation. S3 stores durable workflows, ECR stores immutable images, CodeBuild
builds the selected Git commit, and CloudWatch/EventBridge/SNS provide logs and
alerts.

The root OpenTofu configuration is split by feature so an operator can follow
one responsibility without changing state addresses:

- `infra/app-runtime.tf`
- `infra/web-entrypoint.tf`
- `infra/workflow-storage.tf`
- `infra/monitoring.tf`
- `infra/image-build.tf`

See [infra/README.md](infra/README.md) for mutation boundaries and commands.

## Proof strategy

Tests protect public behavior rather than private helper layout:

- domain tests cover identity, version, mapping, PV, and review invariants;
- storage contract tests run the same observable cases against local and S3
  implementations;
- application and API tests cover authorization, conflicts, provider failures,
  job recovery, and exact download contents;
- JavaScript tests import production modules directly;
- Playwright journeys cover the five-stage browser path, stale state, rename
  propagation, keyboard/focus behavior, and ZIP output;
- OpenTofu formatting, validation, and reviewed plans protect deployment
  behavior and state-address safety.
