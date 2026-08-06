"""Own the persisted PyArrow shape of a harmonization manifest."""

from __future__ import annotations

import pyarrow as pa

MANUAL_OVERRIDES_FIELD = "manual_overrides"


def get_manifest_schema() -> pa.Schema:
    override_struct = pa.struct([
        ("user_id", pa.string()),
        ("timestamp", pa.string()),
        ("value", pa.string()),
    ])

    return pa.schema([
        ("job_id", pa.string()),
        ("column_id", pa.int64()),
        ("column_name", pa.string()),
        ("to_harmonize", pa.string()),
        ("top_harmonization", pa.string()),
        ("ontology_id", pa.string()),
        ("top_harmonizations", pa.list_(pa.string())),
        ("confidence_score", pa.float64()),
        ("error", pa.string()),
        ("row_indices", pa.list_(pa.int64())),
        (MANUAL_OVERRIDES_FIELD, pa.list_(override_struct)),
    ])


__all__ = ["MANUAL_OVERRIDES_FIELD", "get_manifest_schema"]
