# ADR 003: PV Manifest Persistence for Session Recovery

## Status
Accepted

## Context

The Data Chord application uses an in-memory `SessionCache` to store:
- Column-to-CDE mappings (which column maps to which Common Data Element)
- Permissible Value (PV) sets for each CDE

This data is fetched from the Data Model Store API during Stage 3 (Harmonization) and used in Stage 4 (Review) and Stage 5 (Summary) for:
- Validating that harmonized values are within the permissible value set
- Populating PV dropdown menus for manual overrides
- Counting non-conformant values in the gating dialog

**Problem**: When the server restarts (e.g., during development hot-reload, deployment, or crash recovery), the in-memory cache is cleared. Users navigating back to Stage 4 or Stage 5 would find:
- PV combobox dropdowns not populating
- Non-conformant value warnings not appearing
- The need to re-run Stage 3 to restore PV data

## Decision

### 1. Persist PV Manifest to Disk

After fetching PVs in Stage 3, we persist them to disk alongside the harmonization manifest:

**File Type**: `PV_MANIFEST` (JSON format)

**Contents**:
```json
{
  "data_model_key": "cptac",
  "version_label": "v2.1",
  "column_to_cde_key": {
    "primary_diagnosis": "primary_diagnosis_cde",
    "tissue_type": "tissue_or_organ_of_origin"
  },
  "pvs": {
    "primary_diagnosis_cde": ["Adenocarcinoma", "Squamous Cell Carcinoma", ...],
    "tissue_or_organ_of_origin": ["Lung", "Liver", "Kidney", ...]
  }
}
```

### 2. Lazy Loading on Cache Miss

When Stage 4 or Stage 5 needs PV data and the cache is empty:

```python
cache = get_session_cache(file_id)
if not cache.has_any_pvs():
    load_pv_manifest_into_cache(file_id, cache)
```

This lazy loading pattern:
- Only loads from disk when needed
- Avoids loading PVs if the user never revisits review stages
- Is transparent to the rest of the application

### 3. Shared Loading Function in Domain Layer

The `load_pv_manifest_into_cache` function lives in `src/domain/data_model_cache.py` (not duplicated in each stage) to:
- Ensure consistent loading logic across stages
- Maintain stage independence (stages depend on domain, not each other)
- Keep the domain layer as the single source of truth for cache operations

### 4. Cache Clearing on New Upload

Session caches are cleared when a new file is uploaded (Stage 1), not when downloading results (Stage 5). This ensures:
- Fresh PV data for each new harmonization workflow
- Users can revisit Stage 4/5 multiple times after download without losing PV data

## Consequences

### Positive
- **Resilience**: Server restarts no longer break PV validation
- **User Experience**: No need to re-run harmonization after server restart
- **Auditability**: PV manifest serves as a record of which PVs were used
- **Consistency**: Same loading logic across all stages that need PV data

### Negative
- **Storage overhead**: Additional JSON file per harmonization session
- **Staleness risk**: Persisted PVs could become stale if the Data Model Store updates; mitigated by clearing cache on new upload

### Risks Mitigated
- **Thread safety**: All cache operations use locks; `get_column_mappings()` returns a copy
- **Missing data**: Graceful degradation if PV manifest doesn't exist (debug log, no error)

## Files Changed

**Domain Layer**:
- `src/domain/data_model_cache.py` - Added `load_pv_manifest_into_cache()`, `get_column_mappings()`, `clear_all_session_caches()`
- `src/domain/storage/file_types.py` - Added `PV_MANIFEST` file type
- `src/domain/storage/serializers.py` - Added JSON serializer for PV_MANIFEST

**Stage 1 (Upload)**:
- `src/stage_1_upload/router.py` - Clear all caches on new upload

**Stage 3 (Harmonize)**:
- `src/stage_3_harmonize/router.py` - Added `_save_pv_manifest()` after PV fetch

**Stage 4 (Review)**:
- `src/stage_4_review_results/router.py` - Lazy load PVs in `_build_column_pvs()` and `get_non_conformant_values()`

**Stage 5 (Summary)**:
- `src/stage_5_review_summary/router.py` - Lazy load PVs in `_build_summary_from_manifest()`
