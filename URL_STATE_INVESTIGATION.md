# URL and Workflow State Investigation

## Purpose

This note captures a routing and state-management issue in the current Data Chord workflow so we do not lose the thread. The app is moving toward a hosted Docker deployment, so URLs should be clean, stable, and safe to share or reload. Today, several URLs expose internal workflow details and duplicate state that already exists elsewhere.

## Current Behavior

The workflow currently passes state between stages through a mix of:

1. Query parameters in the URL.

2. Browser `sessionStorage`.

3. Backend upload metadata and manifest files.

4. In-memory backend session cache.

5. API request and response bodies.

The important current URL examples are:

```text
/stage-2?file_id=<file_id>&schema=<standard_key>&version_number=<internal_version>
```

```text
/stage-3?file_id=<file_id>&target_schema=<standard_key>&version_number=<internal_version>
```

```text
/stage-4?file_id=<file_id>&job_id=<job_id>&status=<status>&detail=<message>
```

This works, but it makes the URL carry more than it should.

## What Is Wrong

### 1. Internal versioning leaks into user-facing URLs

The standards selector previously displayed both the internal model version and the source standard version, for example:

```text
v2 / 11.0.2
```

The popup now displays only the source standard version:

```text
11.0.2
```

However, the internal version still appears in URLs as:

```text
version_number=2
```

That is a user-facing leak because the address bar is part of the product surface. Even if most users ignore URLs, hosted tools should avoid exposing internal identifiers unless the identifier is intentionally part of the public contract.

### 2. URLs are acting like state storage

The URL currently carries values such as:

```text
schema
target_schema
version_number
job_id
status
detail
```

Some of these values describe the current resource, but many describe transient workflow state. That makes URLs noisy and fragile.

A good URL should mostly answer:

```text
What thing am I looking at?
```

It should not need to answer:

```text
What did every previous stage compute?
```

### 3. State is duplicated across multiple places

For example, the selected standard and version can exist in:

1. The Stage 2 query string.

2. The Stage 2 analyze payload in `sessionStorage`.

3. The Stage 3 harmonize payload in `sessionStorage`.

4. The backend session cache.

5. The API request body.

When the same fact has several homes, it can drift. A reload, back-button navigation, stale tab, or copied URL can create conflicts such as:

```text
URL says version 2, but session storage says version 1.
```

or:

```text
URL says standard gc, but the cached CDE/PV data belongs to ccdi.
```

The app can defend against those cases, but it is simpler and safer to avoid creating them.

### 4. URLs use implementation names instead of product language

The app currently exposes names like:

```text
schema
target_schema
version_number
```

Those names make sense to developers, but the user-facing concept is closer to:

```text
standard
standard version
workflow session
```

If a field must appear in the URL, it should use product language rather than backend table/API language.

### 5. Stage URLs are not clean enough for a hosted app

For local development, long query strings are tolerable. For a hosted Docker deployment, they become part of the app’s public feel. Clean URLs help with:

1. Refreshing a page.

2. Sharing a link with a teammate.

3. Returning to a recent workflow session.

4. Browser history readability.

5. Debugging without exposing internals.

The URL should be stable and minimal, especially if users may bookmark it or return to it within a reasonable retention window.

## What Should Stay In The URL

It probably still makes sense to include a workflow/session identifier.

Today that is effectively:

```text
file_id
```

Keeping `file_id` in the URL is reasonable because it lets the app recover the active workflow after refresh or return-to-session navigation, assuming the backend keeps the related upload and manifests for some limited time.

The cleaner short-term shape would be:

```text
/stage-2?file_id=<file_id>
```

```text
/stage-3?file_id=<file_id>
```

```text
/stage-4?file_id=<file_id>
```

That is not perfect, but it is much better than carrying standard, version, job status, and error detail through the URL.

## What Should Not Be In The URL

These should move out of the address bar:

1. `version_number`

   This is internal Data Model Store addressing. It should remain in backend/session/API state, not the visible URL.

2. `schema` / `target_schema`

   This is duplicated workflow state. The selected standard should be restored from the workflow session identified by `file_id`.

3. `job_id`

   If needed for diagnostics, it can be shown in the UI or stored in workflow state. The Stage 4 URL should not need it to render the result.

4. `status`

   This is derived state. Stage 4 can read the stored harmonization result or manifest status.

5. `detail`

   This is especially poor URL state because it can be long, user-visible, encoded awkwardly, and stale.

## Recommended Direction

### Short-term cleanup

Keep the existing stage pages, but reduce query params to `file_id`.

Recommended routes:

```text
/stage-1
/stage-2?file_id=<file_id>
/stage-3?file_id=<file_id>
/stage-4?file_id=<file_id>
/stage-5?file_id=<file_id>
```

Stage 2, Stage 3, and later stages should load workflow context by `file_id`.

That workflow context should include:

1. Selected standard key.

2. Selected internal `version_number`.

3. User-facing source standard version, when available.

4. Upload metadata.

5. Mapping manifest.

6. Harmonization job/result metadata.

7. Cached or persisted CDE/PV manifest metadata.

### Medium-term cleanup

Introduce an explicit workflow/session resource instead of making `file_id` carry that meaning.

Possible routes:

```text
/workflows/<workflow_id>/upload
/workflows/<workflow_id>/mapping
/workflows/<workflow_id>/harmonize
/workflows/<workflow_id>/review
/workflows/<workflow_id>/summary
```

or, if we want to preserve current stage wording:

```text
/sessions/<session_id>/stage-2
/sessions/<session_id>/stage-3
/sessions/<session_id>/stage-4
```

This would make the URL identify one workflow, while the backend owns the workflow state.

### Longer-term cleanup

Move from stage-specific query-state reconstruction to backend-owned workflow state.

The browser can still use `sessionStorage` as a convenience cache, but it should not be the canonical source of truth. On page load, the app should be able to ask the backend:

```text
GET /api/workflows/<workflow_id>
```

and receive the current workflow state needed to render that stage.

## Suggested Canonical State Model

The workflow state should have one canonical backend representation, keyed by `file_id` in the short term or by a future `workflow_id`.

It should include:

```text
workflow_id or file_id
uploaded file metadata
selected worksheet
selected standard key
selected internal standard version number
selected source standard version label
analysis summary
mapping manifest path/state
harmonization result path/state
PV manifest path/state
```

The frontend may cache parts of this, but cached values should be treated as hints. If a cached value conflicts with the backend workflow state, the backend should win.

## Practical Migration Plan

### Step 1: Stop adding `version_number` to URLs

Keep sending `target_version_number` in request bodies and session payloads, but remove it from:

```text
/stage-2?...&version_number=...
/stage-3?...&version_number=...
```

Stage 2 and Stage 3 already receive enough state through the analyze/harmonize payload handoff for normal navigation.

### Step 2: Stop adding standard/schema to Stage 3 URLs

Stage 3 should use the stored Stage 3 payload for:

```text
target_schema
target_version_number
manifest
manual_overrides
```

The Stage 3 URL only needs:

```text
file_id
```

### Step 3: Remove status/detail/job fields from Stage 4 URLs

Stage 4 should load the harmonization result and stored manifest by `file_id`.

If a job id matters to users, show it on the page. Do not require it in the URL.

### Step 4: Add a backend workflow context endpoint

Add a route like:

```text
GET /api/workflow-context/{file_id}
```

It can return the state each stage needs to recover after refresh. This can start small and grow as the workflow becomes less dependent on browser storage.

### Step 5: Consider a real workflow id

Once hosting and retention rules are clearer, consider making `workflow_id` the public URL identifier and treating `file_id` as an internal artifact within that workflow.

## Open Questions

1. How long should users be able to return to a workflow session in the hosted Docker deployment?

2. Will uploaded files and manifests be stored only on local container disk, or in durable shared storage?

3. Should `file_id` be considered safe as a public-ish URL token, or should we mint a separate opaque `workflow_id`?

4. Should URLs preserve the stage names (`/stage-2`) or switch to product words (`/mapping`)?

5. What should happen when a user opens an expired workflow URL?

## Recommendation

Use URLs to identify the workflow and stage, not to carry workflow state.

For the next cleanup pass, keep `file_id` in the URL and remove everything else that can be recovered from backend/session state. That gives us cleaner hosted URLs quickly without requiring a full workflow-state redesign.

The likely end state is:

```text
/workflows/<workflow_id>/mapping
```

with backend-owned workflow context behind it.
