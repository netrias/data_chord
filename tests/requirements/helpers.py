"""Shared requirement-test examples.

These constants are intentionally conceptual: when the domain example changes,
requirement tests should change in one obvious place instead of drifting through
many one-off strings.
"""

from __future__ import annotations

CSV_MIME_TYPE = "text/csv"

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
