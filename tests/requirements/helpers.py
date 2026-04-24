"""Shared requirement-test examples.

These constants are intentionally conceptual: when the domain example changes,
requirement tests should change in one obvious place instead of drifting through
many one-off strings.
"""

from __future__ import annotations

import json
from pathlib import Path

CSV_MIME_TYPE = "text/csv"
_FIXTURE_DIR = Path(__file__).parent / "fixtures"

DIAGNOSIS_COLUMN = "diagnosis"
AGE_COLUMN = "age"
NOTES_COLUMN = "notes"
SAMPLE_ID_COLUMN = "sample_id"
STATUS_COLUMN = "status"

ORIGINAL_DIAGNOSIS = "melanoma"
CANONICAL_DIAGNOSIS = "Melanoma"
LOWERCASE_LUNG_CANCER = "lung cancer"
CANONICAL_LUNG_CANCER = "Lung Cancer"
WHITESPACE_LUNG_CANCER = " Lung Cancer "
WHITESPACE_UNTOUCHED_NOTE = " untouched value "
UNTOUCHED_EXPORT_VALUE = "Do Not Touch"

PRIMARY_DIAGNOSIS_CDE = "primary_diagnosis"
THERAPEUTIC_AGENTS_CDE = "therapeutic_agents"
AGE_AT_DIAGNOSIS_CDE = "age_at_diagnosis"

LEFT_DUPLICATE_VALUE = "LEFT_A"
RIGHT_DUPLICATE_VALUE = "RIGHT_B"
LEFT_DUPLICATE_VALUE_2 = "LEFT_C"
RIGHT_DUPLICATE_VALUE_2 = "RIGHT_D"
LEFT_OVERRIDE_VALUE = "OVERRIDE_LEFT"
RIGHT_OVERRIDE_VALUE = "OVERRIDE_RIGHT"


def single_column_csv(column_name: str, value: str) -> bytes:
    return f"{column_name}\n{value}\n".encode()


def load_reference_json(name: str) -> dict[str, object]:
    path = _FIXTURE_DIR / name
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Reference fixture must be a JSON object: {path}")

    reference: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"Reference fixture keys must be strings: {path}")
        reference[key] = value
    return reference


def harmonizer_manifest_row(
    *,
    original: str,
    ai_value: str,
    alternatives: list[str] | None = None,
    job_id: str = "requirements-manifest",
    column_id: int = 0,
    column_name: str = DIAGNOSIS_COLUMN,
    row_index: int = 0,
    manual_overrides: list[dict[str, str | None]] | None = None,
) -> dict[str, object]:
    row = load_reference_json("harmonizer_manifest_row.json")
    row["job_id"] = job_id
    row["column_id"] = column_id
    row["column_name"] = column_name
    row["to_harmonize"] = original
    row["top_harmonization"] = ai_value
    row["top_harmonizations"] = alternatives if alternatives is not None else ([ai_value] if ai_value else [])
    row["row_indices"] = [row_index]
    row["manual_overrides"] = manual_overrides or []
    return row
