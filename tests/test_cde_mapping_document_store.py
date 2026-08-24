"""Schema-boundary proof for the durable CDE mapping document."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
from src.persistence.cde_mapping_document_store import (
    CdeMappingDocumentStore,
    CdeMappingUnreadableError,
    load_cde_mapping_entries_by_column,
    load_cde_mapping_json,
)
from src.storage import LocalWorkflowStorage, UserContext, WorkflowFile

_FILE_ID = dataset_workflow_id_from_string("a" * 32)


def _valid_mapping_document() -> dict[str, object]:
    return {
        "file_id": str(_FILE_ID),
        "generated_at": "2026-08-24T12:00:00+00:00",
        "data_model_key": "data-model",
        "external_version_number": "1.0",
        "mappings": [
            {
                "column_key": "col_0000",
                "source_column_name": "source",
                "output_column_name": "output",
                "cde_key": "cde-key",
                "cde_id": 1,
                "cde_description": "CDE",
                "cde_type": "pv",
                "mapping_source": "ai",
                "maps_values": True,
            },
        ],
    }


def _stored_mapping_document(
    tmp_path: Path,
    payload: dict[str, object],
) -> tuple[LocalWorkflowStorage, UserContext]:
    storage = LocalWorkflowStorage(tmp_path / "workflow-storage")
    user = UserContext(user_id="reviewer")
    workflow = storage.create_workflow(user, _FILE_ID)
    storage.write_json(user, workflow.dataset_workflow_id, WorkflowFile.CDE_MAPPING, payload)
    return storage, user


def test_mapping_document_accepts_a_valid_document() -> None:
    # Given: a complete durable mapping document.
    payload = _valid_mapping_document()

    # When: the document crosses the storage schema boundary.
    document = CdeMappingDocumentStore.from_store(payload)

    # Then: the complete mapping list is preserved.
    assert len(document.mappings) == 1
    assert document.mappings[0].column_key == "col_0000"


@pytest.mark.parametrize(
    "mappings",
    [
        pytest.param(None, id="null"),
        pytest.param({}, id="object"),
        pytest.param("not-a-list", id="string"),
        pytest.param(1, id="number"),
    ],
)
def test_mapping_document_rejects_a_malformed_mappings_container(mappings: object) -> None:
    # Given: a document whose mappings container is not a JSON array.
    payload = _valid_mapping_document()
    payload["mappings"] = mappings

    # When/Then: parsing fails as one unreadable document.
    with pytest.raises(CdeMappingUnreadableError, match="CDE mapping document"):
        CdeMappingDocumentStore.from_store(payload)


def test_mapping_document_rejects_a_missing_mappings_container() -> None:
    # Given: a document without its required mappings container.
    payload = _valid_mapping_document()
    del payload["mappings"]

    # When/Then: parsing fails instead of creating an empty document.
    with pytest.raises(CdeMappingUnreadableError, match="CDE mapping document"):
        CdeMappingDocumentStore.from_store(payload)


@pytest.mark.parametrize(
    "field",
    ["file_id", "generated_at", "data_model_key", "external_version_number"],
)
def test_mapping_document_rejects_missing_identity_fields(field: str) -> None:
    payload = _valid_mapping_document()
    del payload[field]

    with pytest.raises(CdeMappingUnreadableError, match="CDE mapping document"):
        CdeMappingDocumentStore.from_store(payload)


def test_mapping_document_rejects_an_invalid_generated_time() -> None:
    payload = _valid_mapping_document()
    payload["generated_at"] = "not-a-date"

    with pytest.raises(CdeMappingUnreadableError, match="CDE mapping document"):
        CdeMappingDocumentStore.from_store(payload)


@pytest.mark.parametrize("generated_at", ["2026-08-24", "2026-08-24T12:00:00"])
def test_mapping_document_rejects_a_time_without_a_time_zone(generated_at: str) -> None:
    payload = _valid_mapping_document()
    payload["generated_at"] = generated_at

    with pytest.raises(CdeMappingUnreadableError, match="CDE mapping document"):
        CdeMappingDocumentStore.from_store(payload)


def test_mapping_document_rejects_an_invalid_workflow_identity() -> None:
    payload = _valid_mapping_document()
    payload["file_id"] = "not-a-workflow-id"

    with pytest.raises(CdeMappingUnreadableError, match="CDE mapping document"):
        CdeMappingDocumentStore.from_store(payload)


@pytest.mark.parametrize("target", ["document", "entry"])
def test_mapping_document_rejects_unknown_fields(target: str) -> None:
    payload = _valid_mapping_document()
    if target == "document":
        payload["unknown"] = True
    else:
        mappings = payload["mappings"]
        assert isinstance(mappings, list)
        entry = mappings[0]
        assert isinstance(entry, dict)
        entry["unknown"] = True

    with pytest.raises(CdeMappingUnreadableError, match="CDE mapping document"):
        CdeMappingDocumentStore.from_store(payload)


@pytest.mark.parametrize(
    "invalid_entry",
    [
        pytest.param({"column_key": "col_0000"}, id="missing-required-fields"),
        pytest.param("not-an-object", id="non-object"),
        pytest.param(
            {
                "column_key": "col_0000",
                "source_column_name": "source",
                "output_column_name": "output",
                "mapping_source": "unknown",
                "maps_values": True,
            },
            id="invalid-enum",
        ),
    ],
)
def test_mapping_document_rejects_any_invalid_entry(invalid_entry: object) -> None:
    # Given: one valid entry followed by an invalid entry.
    payload = _valid_mapping_document()
    mappings = payload["mappings"]
    assert isinstance(mappings, list)
    mappings.append(invalid_entry)

    # When/Then: the valid entry is not returned as a partial document.
    with pytest.raises(CdeMappingUnreadableError, match="CDE mapping document"):
        CdeMappingDocumentStore.from_store(payload)


@pytest.mark.parametrize("loader_name", ["entries", "json"])
def test_durable_mapping_loaders_reject_duplicate_column_identities(
    tmp_path: Path,
    loader_name: str,
) -> None:
    payload = deepcopy(_valid_mapping_document())
    mappings = payload["mappings"]
    assert isinstance(mappings, list)
    mappings.append(deepcopy(mappings[0]))
    storage, user = _stored_mapping_document(tmp_path, payload)

    with pytest.raises(CdeMappingUnreadableError, match="duplicate column"):
        if loader_name == "entries":
            load_cde_mapping_entries_by_column(_FILE_ID, storage, user)
        else:
            load_cde_mapping_json(str(_FILE_ID), storage, user)


@pytest.mark.parametrize("loader_name", ["entries", "json"])
def test_durable_mapping_loaders_reject_malformed_documents(
    tmp_path: Path,
    loader_name: str,
) -> None:
    # Given: a durable mapping artifact with one malformed entry.
    payload = deepcopy(_valid_mapping_document())
    mappings = payload["mappings"]
    assert isinstance(mappings, list)
    mappings.append({"column_key": "col_0001"})
    storage, user = _stored_mapping_document(tmp_path, payload)

    # When/Then: every durable read path reports the unreadable document.
    with pytest.raises(CdeMappingUnreadableError, match="CDE mapping document"):
        if loader_name == "entries":
            load_cde_mapping_entries_by_column(_FILE_ID, storage, user)
        else:
            load_cde_mapping_json(str(_FILE_ID), storage, user)


@pytest.mark.parametrize("loader_name", ["entries", "json"])
def test_durable_mapping_loaders_reject_another_workflow_identity(
    tmp_path: Path,
    loader_name: str,
) -> None:
    payload = _valid_mapping_document()
    payload["file_id"] = "b" * 32
    storage, user = _stored_mapping_document(tmp_path, payload)

    with pytest.raises(CdeMappingUnreadableError, match="another workflow"):
        if loader_name == "entries":
            load_cde_mapping_entries_by_column(_FILE_ID, storage, user)
        else:
            load_cde_mapping_json(str(_FILE_ID), storage, user)
