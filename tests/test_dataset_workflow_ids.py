"""Dataset workflow id domain model tests."""

from __future__ import annotations

import pytest

from src.domain.dataset_workflow_ids import (
    DATASET_WORKFLOW_ID_LENGTH,
    dataset_workflow_id_from_string,
    is_dataset_workflow_id,
    new_dataset_workflow_id,
)


def test_new_dataset_workflow_id_is_lowercase_uuid7_hex() -> None:
    # Given / When: the app creates a new dataset workflow id
    dataset_workflow_id = new_dataset_workflow_id()

    # Then: the id has the one canonical shape accepted by the domain
    assert len(dataset_workflow_id) == DATASET_WORKFLOW_ID_LENGTH
    assert is_dataset_workflow_id(dataset_workflow_id)
    assert str(dataset_workflow_id) == str(dataset_workflow_id).lower()
    assert str(dataset_workflow_id)[12] == "7"
    assert str(dataset_workflow_id)[16] in {"8", "9", "a", "b"}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "abc123",
        "A" * DATASET_WORKFLOW_ID_LENGTH,
        "g" * DATASET_WORKFLOW_ID_LENGTH,
        "../" + ("a" * (DATASET_WORKFLOW_ID_LENGTH - 3)),
        "a" * (DATASET_WORKFLOW_ID_LENGTH - 1),
        "a" * (DATASET_WORKFLOW_ID_LENGTH + 1),
    ],
)
def test_dataset_workflow_id_from_string_rejects_non_canonical_ids(raw: str) -> None:
    # Given: a boundary value that is not the app's dataset workflow id format
    assert not is_dataset_workflow_id(raw)

    # When / Then: conversion into the domain type fails
    with pytest.raises(ValueError):
        dataset_workflow_id_from_string(raw)


def test_dataset_workflow_id_from_string_accepts_canonical_id() -> None:
    # Given: a boundary string with the app's canonical id shape
    raw = "a" * DATASET_WORKFLOW_ID_LENGTH

    # When: it is converted into the domain type
    dataset_workflow_id = dataset_workflow_id_from_string(raw)

    # Then: the value remains serializable as the same string
    assert str(dataset_workflow_id) == raw
