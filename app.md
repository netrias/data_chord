# Data Chord

Data Chord helps a curator turn a CSV, TSV, or Excel workbook into a reviewed,
standardized dataset. It combines Netrias recommendations with explicit human
decisions, then packages both the result and the evidence needed to understand
how it was produced.

## What the user does

1. **Upload and choose a model.** The user uploads a tabular dataset, chooses a
   worksheet when needed, and selects a Common Data Element (CDE) model and
   version.

2. **Review column mappings.** Data Chord recommends a target CDE for each
   source column. The user can accept it, choose another CDE, choose no mapping,
   or rename the output column. Duplicate source headers remain separate.

3. **Run harmonization.** Netrias standardizes values using the confirmed
   column plan. The progress page can recover after a browser refresh, worker
   restart, or application deployment because the accepted job is durable.

4. **Review values.** The user reviews changes by column or row, sees confidence
   and permissible-value context, opens source-row context, and applies manual
   overrides. A stale browser tab cannot silently replace newer review work.

5. **Summarize and download.** Data Chord shows what stayed the same, what
   Netrias changed, and what the curator changed. The download contains the
   final tabular data and its mapping and transformation audit artifacts.

## Behavior that matters

- Supported inputs are CSV, TSV, and XLSX, including worksheet selection.
- Column identity is positional, so duplicate or blank headers do not merge.
- Character differences such as case, whitespace, and punctuation remain
  meaningful.
- An original value that already conforms to the target permissible values is
  not replaced by an AI suggestion.
- Confirmed model, mapping, and job state are durable; browser storage and
  in-memory caches are accelerators only.
- Every hosted workflow belongs to an authenticated Cognito principal, and
  authorization is checked before cached or local data is used.
- Repeating the same accepted harmonization request reuses its active job.
  Competing Stage 3 plans and stale Stage 4 reviews return a visible conflict.
- Active review overrides control export. The Parquet audit history remains
  historical evidence even if the active overrides are deleted.
- External failures are visible and retryable without exposing provider
  exception details.

## Output

The ZIP download includes three files:

- the final CSV, TSV, or XLSX dataset;
- a JSON representation of the harmonization manifest;
- the CDE column-mapping audit document.

## Implementation in one paragraph

Data Chord is a FastAPI application with server-rendered Jinja pages and small
vanilla JavaScript modules. Domain types own workflow and transformation
meaning. Routers and use cases coordinate the five stages. Netrias adapters and
local/S3 storage implementations sit behind explicit boundaries. Hosted
deployments run on ECS Fargate behind an authenticated Application Load
Balancer, with durable workflow artifacts in S3.

See [ARCHITECTURE.md](ARCHITECTURE.md) for boundaries and state ownership,
[README.md](README.md) for local setup, and [DEPLOYMENT.md](DEPLOYMENT.md) for
hosted deployment.
