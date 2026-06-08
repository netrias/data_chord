"""Tests for selected data model version references."""

from __future__ import annotations

import pytest

from src.domain.data_model_version_reference import DataModelVersionReference


def test_data_model_version_reference_strips_external_version() -> None:
    # Given: the selected external version arrives with surrounding whitespace
    reference = DataModelVersionReference(data_model_key="gc", external_version_number="  11.0.4  ")

    # Then: the canonical domain value is the stripped external version
    assert reference.external_version_number == "11.0.4"


def test_data_model_version_reference_keeps_latest_as_external_version_text() -> None:
    # Given: a caller supplied "latest" as the external version text
    reference = DataModelVersionReference(data_model_key="gc", external_version_number="latest")

    # Then: DataChord does not special-case it before lookup
    assert reference.external_version_number == "latest"


def test_data_model_version_reference_rejects_blank_external_version() -> None:
    # Given/When/Then: empty version text still has no lookup value
    with pytest.raises(ValueError, match="external_version_number is required"):
        DataModelVersionReference(data_model_key="gc", external_version_number="  ")
