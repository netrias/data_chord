# Plan: S3 Pointer Launch for DataHub Integration

## Context

DataHub (a researcher portal) rejects uploads that fail term conformance. When rejected, DataHub:
1. Stores the rejected file in their S3 bucket
2. Launches a Data Chord container with a pointer to that file
3. Redirects the researcher to Data Chord
4. Researcher walks through harmonization workflow (starting at Stage 2)
5. Harmonized file is written back to S3

---

## Architecture

```
┌─────────────┐     1. Store rejected file     ┌─────────────────┐
│   DataHub   │ ─────────────────────────────► │   S3 (DataHub)  │
│   Portal    │                                │                 │
└─────────────┘                                │  /pending/      │
       │                                       │    {job_id}/    │
       │ 2. Launch container                   │      input.csv  │
       │    with S3 pointer                    │      output.csv │
       ▼                                       └─────────────────┘
┌─────────────┐                                        ▲
│ Data Chord  │  3. Read input, analyze on startup     │
│ Container   │ ◄──────────────────────────────────────┤
│             │                                        │
│  User lands │  6. Write harmonized output            │
│  at Stage 2 │ ───────────────────────────────────────┘
└─────────────┘
       │
       │ 4. User completes workflow (Stages 2-5)
       │
       ▼
   5. Container exits or signals completion
```

---

## Container Launch Contract

DataHub launches container with environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `DATA_CHORD_MODE` | Yes | `"ingest"` to enable S3 mode |
| `DATA_CHORD_INPUT_S3` | Yes | `s3://bucket/path/input.csv` |
| `DATA_CHORD_OUTPUT_S3` | Yes | `s3://bucket/path/output.csv` |

**AWS credentials**: Passed via standard AWS env vars or IAM role attached to container.

---

## User Flow

1. **DataHub** rejects upload, writes to S3, launches Data Chord container
2. **Container startup**:
   - Detects `DATA_CHORD_MODE=ingest`
   - Downloads file from `DATA_CHORD_INPUT_S3`
   - Runs analysis (column detection, CDE mapping)
   - Stores results in memory/local temp
3. **User redirected** to Data Chord URL (Stage 2)
4. **User proceeds** through Stages 2 → 3 → 4 → 5
5. **On Stage 5 completion**:
   - Harmonized CSV uploaded to `DATA_CHORD_OUTPUT_S3`
   - UI shows "File sent to DataHub" confirmation
6. **Container** can exit or remain for a grace period

---

## Changes to Data Chord

### New Module: `/src/ingest/`

```
/src/ingest/
  __init__.py
  config.py       # Read env vars, validate config
  s3_client.py    # Download/upload to S3
  startup.py      # Startup analysis orchestration
```

### Config Schema

```python
@dataclass(frozen=True)
class IngestConfig:
    """Configuration for S3 ingest mode."""
    mode: Literal["ingest", "standalone"]
    input_s3: str | None          # s3://bucket/path/input.csv
    output_s3: str | None         # s3://bucket/path/output.csv

def load_ingest_config() -> IngestConfig:
    """Load config from environment variables."""
    ...
```

### S3 Client

```python
class S3Client:
    """Read/write files to S3."""

    def download(self, s3_uri: str, local_path: Path) -> None:
        """Download file from S3 to local path."""
        ...

    def upload(self, local_path: Path, s3_uri: str) -> None:
        """Upload local file to S3."""
        ...
```

### Startup Behavior

In `backend/app/main.py` or a new startup hook:

```python
@app.on_event("startup")
async def handle_ingest_mode():
    config = load_ingest_config()
    if config.mode != "ingest":
        return  # Normal standalone mode

    # 1. Download input file from S3
    s3 = S3Client()
    local_input = Path("/tmp/ingest_input.csv")
    s3.download(config.input_s3, local_input)

    # 2. Store in UploadStorage (creates file_id)
    storage = get_upload_storage()
    meta = storage.store_bytes(
        local_input.read_bytes(),
        original_name=Path(config.input_s3).name,
    )

    # 3. Store ingest metadata for later S3 upload
    save_ingest_meta(IngestMeta(
        file_id=meta.file_id,
        output_s3=config.output_s3,
    ))

    # 4. Set global state so UI knows to redirect to Stage 2
    set_ingest_file_id(meta.file_id)
```

### Stage 1 Changes

**Router** (`src/stage_1_upload/router.py`):
- Check if ingest mode active
- If yes, redirect to Stage 2 with preloaded file_id

```python
@stage_one_router.get("", response_class=HTMLResponse)
async def render_stage_one(request: Request) -> HTMLResponse:
    ingest_file_id = get_ingest_file_id()
    if ingest_file_id:
        # Ingest mode: skip to Stage 2
        return RedirectResponse(
            url=f"/stage-2?file_id={ingest_file_id}&source=datahub",
            status_code=307,
        )
    # Normal mode: show upload UI
    ...
```

### Stage 2 Changes

**Router** (`src/stage_2_review_columns/router.py`):
- Accept `file_id` and `source` query params
- If present, load pre-analyzed data from storage
- Pass `source` to template for UI badge

**Template**:
- Show "File from DataHub" badge when `source` present
- Pre-populate analysis results

### Stage 5 Changes

**Router** (`src/stage_5_review_summary/router.py`):
- Add endpoint or modify existing to upload to S3 on completion

```python
@stage_five_router.post("/complete")
async def complete_harmonization(payload: CompleteRequest) -> CompleteResponse:
    ingest_meta = load_ingest_meta(payload.file_id)
    if not ingest_meta or not ingest_meta.output_s3:
        return CompleteResponse(uploaded=False, message="No S3 destination configured")

    # Find harmonized file
    harmonized_path = resolve_harmonized_path(payload.file_id)

    # Upload to S3
    s3 = S3Client()
    s3.upload(harmonized_path, ingest_meta.output_s3)

    return CompleteResponse(uploaded=True, destination=ingest_meta.output_s3)
```

**Template**:
- Replace/augment "Export" with "Send to DataHub" button
- Show upload confirmation

---

## Files to Create

| File | Purpose |
|------|---------|
| `src/ingest/__init__.py` | Package marker |
| `src/ingest/config.py` | Load and validate env vars |
| `src/ingest/s3_client.py` | S3 download/upload |
| `src/ingest/startup.py` | Startup analysis orchestration |
| `src/ingest/state.py` | Track ingest file_id for redirects |

## Files to Modify

| File | Change |
|------|--------|
| `backend/app/main.py` | Add startup hook for ingest mode |
| `src/stage_1_upload/router.py` | Redirect to Stage 2 in ingest mode |
| `src/stage_1_upload/services.py` | Add `store_bytes()` method |
| `src/stage_2_review_columns/router.py` | Accept `file_id`, `source` params |
| `src/stage_2_review_columns/templates/stage_2_mappings.html` | Source badge |
| `src/stage_5_review_summary/router.py` | Add S3 upload endpoint |
| `src/stage_5_review_summary/templates/stage_5_review.html` | "Send to DataHub" button |
| `pyproject.toml` | Add `boto3` dependency |

---

## Dependencies

Add to `pyproject.toml`:
```toml
dependencies = [
    ...
    "boto3>=1.34",
]
```

---

## Storage Layout (Ingest Mode)

```
/src/stage_1_upload/uploads/
  /files/{file_id}.csv           # Downloaded from S3
  /meta/{file_id}.json           # Standard upload metadata
  /manifests/{file_id}.json      # Analysis results
  /ingest/{file_id}.json         # Ingest metadata (output_s3)
```

---

## Open Questions

1. **Session/auth**: How does DataHub authenticate the user redirect? Options:
   - Signed URL with expiry
   - Session token in query param
   - Trust container network isolation (no auth)

2. **Container lifecycle**: Should container:
   - Exit after Stage 5 completion?
   - Stay alive for some grace period?
   - Signal completion via HTTP callback?

3. **Error handling**: What if S3 upload fails at Stage 5?
   - Retry logic?
   - User notification?
   - DataHub callback?

---

## Implementation Order

1. Add `boto3` to dependencies
2. Create `/src/ingest/` module with config and S3 client
3. Add `store_bytes()` to UploadStorage
4. Implement startup hook for ingest mode
5. Modify Stage 1 to redirect in ingest mode
6. Modify Stage 2 to accept preloaded file
7. Add S3 upload to Stage 5
8. Modify Stage 5 template for "Send to DataHub"
9. Write tests (mock S3)
10. Run ruff + basedpyright
