"""Read independently specified flat schema-2 rows through Data Chord's adapter."""

from __future__ import annotations

import gzip
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from boto3.dynamodb.types import Binary, TypeDeserializer, TypeSerializer

from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import (
    ReferenceDataCorruptError,
    ReferenceDataUnavailableError,
    ReferenceModelNotFoundError,
)
from src.integrations.dynamodb_reference_data import DynamoDbReferenceDataRepository

MODEL_PK = "MODEL#bW9kZWw#VERSION#MQ"
VERSION = DataModelVersionReference("model", "1")
MODEL_DIGEST = "e63938d8f4226ff222859f43121b06dec9eb8b6f7d9d3b03319c5798db932a00"
CATALOG_DIGEST = "b24c91e696b581ca42db67c46a3b0830d794c5925bc9662b73554c2a434c2656"


@dataclass
class Table:
    """Minimal paginated DynamoDB table for published row fixtures."""

    items: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)
    query_calls: int = 0

    def query(self, **kwargs: object) -> dict[str, object]:
        self.query_calls += 1
        values = kwargs["ExpressionAttributeValues"]
        assert isinstance(values, Mapping)
        pk = values[":pk"]
        rows = [item for (row_pk, _), item in sorted(self.items.items()) if row_pk == pk]
        start_key = kwargs.get("ExclusiveStartKey")
        start = 0
        if isinstance(start_key, Mapping):
            start = next(i + 1 for i, row in enumerate(rows) if row["sk"] == start_key["sk"])
        page = rows[start : start + 2]
        result: dict[str, object] = {"Items": page}
        if start + 2 < len(rows):
            result["LastEvaluatedKey"] = {"pk": pk, "sk": page[-1]["sk"]}
        return result


def _published_table() -> Table:
    """Return literal publisher-format rows, independent of reader encoding helpers."""
    relations: list[dict[str, object]] = [
        {
            "node_name": "diagnosis",
            "node_cde_id": 4268857500206703555,
            "source_node_cde_id": "YuwtJC",
            "description": "Stage at diagnosis",
            "data_type": "string",
            "is_key": True,
            "is_required": True,
        },
        {
            "node_name": "sample",
            "node_cde_id": 7337509294113026323,
            "source_node_cde_id": "Abc123",
            "description": "Stage on sample",
            "data_type": "enum",
            "is_key": False,
            "is_required": False,
        },
    ]
    rows: list[dict[str, object]] = [
        {
            "pk": MODEL_PK,
            "sk": "IMPORT",
            "schema_version": 2,
            "data_model_key": "model",
            "external_version_number": "1",
            "digest": MODEL_DIGEST,
        },
        {
            "pk": MODEL_PK,
            "sk": "CDE#YWdl#META",
            "schema_version": 2,
            "cde_key": "age",
            "cde_id": 4690995584867433052,
            "node_relations": [
                {
                    "node_name": "sample",
                    "node_cde_id": 5946852043267552078,
                    "source_node_cde_id": "Age123",
                    "description": None,
                    "data_type": "integer",
                    "is_key": False,
                    "is_required": False,
                }
            ],
            "value_count": 0,
            "chunk_count": 0,
            "digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        },
        {
            "pk": MODEL_PK,
            "sk": "CDE#c3RhZ2U#META",
            "schema_version": 2,
            "cde_key": "stage",
            "cde_id": 2457258298240256618,
            "node_relations": relations,
            "value_count": 2,
            "chunk_count": 1,
            "digest": "ad38db5821a8ce1688543c4c290da23e68116a15c3d715a3f5002125f1dc0ff0",
        },
        {
            "pk": MODEL_PK,
            "sk": "CDE#c3RhZ2U#VALUES#000000",
            "schema_version": 2,
            "cde_key": "stage",
            "chunk_index": 0,
            "uncompressed_size": len(b'["I","II"]'),
            "payload": gzip.compress(b'["I","II"]', mtime=0),
        },
        {
            "pk": MODEL_PK,
            "sk": "META",
            "schema_version": 2,
            "data_model_key": "model",
            "external_version_number": "1",
            "label": "Model",
            "nodes": ["diagnosis", "sample", "unused"],
            "node_count": 3,
            "node_cde_count": 3,
            "cde_key_count": 2,
            "value_count": 2,
            "digest": MODEL_DIGEST,
        },
        {
            "pk": "CATALOG",
            "sk": "IMPORT",
            "schema_version": 2,
            "model_count": 1,
            "digest": CATALOG_DIGEST,
            "source_digest": "a" * 64,
        },
        {
            "pk": "CATALOG",
            "sk": "MODEL#bW9kZWw#VERSION#MQ",
            "schema_version": 2,
            "data_model_key": "model",
            "external_version_number": "1",
            "label": "Model",
        },
        {
            "pk": "CATALOG",
            "sk": "META",
            "schema_version": 2,
            "model_count": 1,
            "digest": CATALOG_DIGEST,
            "source_digest": "a" * 64,
        },
    ]
    return Table({(str(row["pk"]), str(row["sk"])): row for row in rows})


def test_flat_reader_keeps_cde_identity_and_both_node_relationships() -> None:
    # Given complete flat rows with a shared CDE, a second CDE under sample, and an empty node.
    table = _published_table()
    repository = DynamoDbReferenceDataRepository(table)

    # When the complete model and the node-independent application view are loaded.
    model = repository.load_cdes(VERSION)
    collapsed = repository.load_model(VERSION)
    summaries = repository.list_models()

    # Then both relationships survive while the application sees one stable CDE ID.
    assert model.nodes == ("diagnosis", "sample", "unused")
    assert [cde.cde_key for cde in model.cdes] == ["age", "stage"]
    assert [(r.node_name, r.node_cde_id, r.source_node_cde_id) for r in model.cdes[0].node_relations] == [
        ("sample", 5946852043267552078, "Age123")
    ]
    assert [
        (r.node_name, r.node_cde_id, r.source_node_cde_id, r.description, r.data_type, r.is_key, r.is_required)
        for r in model.cdes[1].node_relations
    ] == [
        ("diagnosis", 4268857500206703555, "YuwtJC", "Stage at diagnosis", "string", True, True),
        ("sample", 7337509294113026323, "Abc123", "Stage on sample", "enum", False, False),
    ]
    stage = collapsed.catalog.get("stage")
    assert stage is not None
    assert stage.cde_id == 2457258298240256618
    assert stage.description is None
    assert collapsed.pvs.get("stage") == frozenset({"I", "II"})
    assert collapsed.pvs.get("age") == frozenset()
    assert [(s.data_model_key, [v.external_version_number for v in s.versions]) for s in summaries] == [
        ("model", ["1"])
    ]
    assert table.query_calls >= 4


def test_flat_reader_caches_the_complete_application_model() -> None:
    # Given a published flat model and a repository with an empty model cache.
    table = _published_table()
    repository = DynamoDbReferenceDataRepository(table)

    # When the same model is loaded twice through the application view.
    first = repository.load_model(VERSION)
    query_calls_after_first_load = table.query_calls
    second = repository.load_model(VERSION)

    # Then the second load checks the catalog and reuses the cached model without reading its rows.
    assert first is second
    assert query_calls_after_first_load > 1
    assert table.query_calls == query_calls_after_first_load + 2


@pytest.mark.parametrize("warm_cache", [False, True], ids=["old-rows", "cached-model"])
def test_flat_reader_requires_the_version_to_be_published(warm_cache: bool) -> None:
    # Given a version whose model rows remain after it is removed from a sealed catalog.
    table = _published_table()
    repository = DynamoDbReferenceDataRepository(table)
    if warm_cache:
        repository.load_model(VERSION)
    table.items.pop(("CATALOG", MODEL_PK))
    for key in ("META", "IMPORT"):
        table.items[("CATALOG", key)].update(
            model_count=0, digest="4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
        )

    # When the application asks to load the withdrawn version.
    with pytest.raises(ReferenceModelNotFoundError) as error:
        repository.load_model(VERSION)

    # Then the catalog determines retirement even when the model can still be read from its rows.
    assert str(error.value) == "Reference model is not published: model/1"


@pytest.mark.parametrize("failure", ["unpublished-catalog", "missing-model"])
def test_flat_reader_reports_missing_publication_data_as_unavailable(failure: str) -> None:
    # Given either an unpublished catalog or a listed version with no model rows.
    table = _published_table()
    repository = DynamoDbReferenceDataRepository(table)
    missing_partition = "CATALOG" if failure == "unpublished-catalog" else MODEL_PK
    table.items = {key: row for key, row in table.items.items() if key[0] != missing_partition}

    # When a workflow asks for the version.
    with pytest.raises(ReferenceDataUnavailableError) as error:
        repository.load_model(VERSION)

    # Then the repository reports an outage with the corresponding cause.
    assert str(error.value) == {
        "unpublished-catalog": "Reference catalog is not published",
        "missing-model": "Published reference model data is unavailable",
    }[failure]


def test_flat_reader_accepts_boto3_numbers_and_binary_values() -> None:
    # Given flat rows converted to the Decimal and Binary types returned by boto3.
    table = _published_table()
    serializer = TypeSerializer()
    deserializer = TypeDeserializer()
    table.items = {
        key: {name: deserializer.deserialize(serializer.serialize(value)) for name, value in row.items()}
        for key, row in table.items.items()
    }
    # When the runtime reader loads the model and catalog.
    repository = DynamoDbReferenceDataRepository(table)
    model = repository.load_cdes(VERSION)
    summaries = repository.list_models()

    # Then the model retains both CDE values and its catalog entry.
    assert isinstance(table.items[(MODEL_PK, "META")]["node_cde_count"], Decimal)
    assert isinstance(table.items[(MODEL_PK, "CDE#c3RhZ2U#VALUES#000000")]["payload"], Binary)
    assert next(cde.values for cde in model.cdes if cde.cde_key == "stage") == frozenset({"I", "II"})
    assert [summary.data_model_key for summary in summaries] == ["model"]


def test_flat_catalog_rejects_an_identity_that_disagrees_with_its_key() -> None:
    # Given a catalog row whose model key differs from its sort key.
    table = _published_table()
    table.items[("CATALOG", MODEL_PK)]["data_model_key"] = "wrong"
    repository = DynamoDbReferenceDataRepository(table)

    # When the catalog is loaded.
    with pytest.raises(ReferenceDataCorruptError) as error:
        repository.list_models()

    # Then the mismatched catalog identity is rejected.
    assert "identity does not match its key" in str(error.value)


def test_renaming_a_node_does_not_change_the_cde_wide_id() -> None:
    # Given valid rows where diagnosis was renamed to zzz and all related IDs were regenerated.
    table = _published_table()
    meta = table.items[(MODEL_PK, "META")]
    stage = table.items[(MODEL_PK, "CDE#c3RhZ2U#META")]
    relations = stage["node_relations"]
    assert isinstance(relations, list)
    renamed = relations[0]
    renamed["node_name"] = "zzz"
    renamed["node_cde_id"] = 3896016226965161549
    stage["node_relations"] = [relations[1], renamed]
    meta["nodes"] = ["sample", "unused", "zzz"]
    renamed_digest = "8c3aa81f2434d062affc4948edf27e16714562a194e855ede49b8a5fe1981a72"
    meta["digest"] = renamed_digest
    table.items[(MODEL_PK, "IMPORT")]["digest"] = renamed_digest

    # When the renamed model is read through the flat and application views.
    repository = DynamoDbReferenceDataRepository(table)
    flat = repository.load_cdes(VERSION)
    collapsed = repository.load_model(VERSION)

    # Then the relationship changes identity but the shared CDE identity does not.
    stage_cde = next(cde for cde in flat.cdes if cde.cde_key == "stage")
    assert [relation.node_name for relation in stage_cde.node_relations] == ["sample", "zzz"]
    assert stage_cde.node_relations[1].node_cde_id == 3896016226965161549
    assert stage_cde.node_relations[1].source_node_cde_id == "YuwtJC"
    collapsed_stage = collapsed.catalog.get("stage")
    assert collapsed_stage is not None
    assert collapsed_stage.cde_id == 2457258298240256618


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("model_marker", "no complete marker"),
        ("model_import", "no immutable import seal"),
        ("catalog_marker", "not completely published"),
        ("catalog_import", "no immutable import seal"),
        ("value_chunk", "chunks are incomplete"),
        ("unknown_node", "node relationships are invalid"),
        ("changed_id", "CDE id is invalid"),
    ],
)
def test_flat_reader_rejects_incomplete_or_corrupt_rows(damage: str, message: str) -> None:
    # Given a complete publication with one specified row or field damaged.
    table = _published_table()
    if damage == "model_marker":
        del table.items[(MODEL_PK, "META")]
    elif damage == "model_import":
        del table.items[(MODEL_PK, "IMPORT")]
    elif damage == "catalog_marker":
        del table.items[("CATALOG", "META")]
    elif damage == "catalog_import":
        del table.items[("CATALOG", "IMPORT")]
    elif damage == "value_chunk":
        del table.items[(MODEL_PK, "CDE#c3RhZ2U#VALUES#000000")]
    elif damage == "unknown_node":
        stage = table.items[(MODEL_PK, "CDE#c3RhZ2U#META")]
        relations = stage["node_relations"]
        assert isinstance(relations, list)
        relations[0]["node_name"] = "missing"
        relations[0]["node_cde_id"] = 5658806991821127333
    else:
        table.items[(MODEL_PK, "CDE#c3RhZ2U#META")]["cde_id"] = 1
    repository = DynamoDbReferenceDataRepository(table)

    # When the corresponding model or catalog is read.
    with pytest.raises(ReferenceDataCorruptError) as error:
        if damage in {"catalog_marker", "catalog_import"}:
            repository.list_models()
        else:
            repository.load_cdes(VERSION)

    # Then no incomplete or inconsistent publication is returned.
    assert message in str(error.value)


@pytest.mark.parametrize("field", ["node_count", "node_cde_count", "cde_key_count", "value_count"])
def test_flat_reader_checks_each_model_count_independently(field: str) -> None:
    # Given a complete flat model with one incorrect stored count.
    table = _published_table()
    table.items[(MODEL_PK, "META")][field] = 99
    repository = DynamoDbReferenceDataRepository(table)

    # When the complete flat model is read.
    with pytest.raises(ReferenceDataCorruptError) as error:
        repository.load_cdes(VERSION)

    # Then the specific incorrect count is rejected.
    assert f"Reference {field.replace('_', ' ')} is invalid" in str(error.value)


@pytest.mark.parametrize("omit_nodes", [True, False])
def test_schema_two_reads_a_sealed_catalog_without_nodes(omit_nodes: bool) -> None:
    # Given a schema-2 publication containing shared CDEs without node metadata.
    table = _published_table()
    meta = table.items[(MODEL_PK, "META")]
    if omit_nodes:
        del meta["nodes"]
    else:
        meta["nodes"] = []
    del meta["node_count"]
    del meta["node_cde_count"]
    del table.items[(MODEL_PK, "CDE#YWdl#META")]["node_relations"]
    table.items[(MODEL_PK, "CDE#c3RhZ2U#META")]["node_relations"] = []
    meta["digest"] = "656890ceb98a5070123018b160040fd3a91b8a66b0a4556db65bc6724569e960"
    table.items[(MODEL_PK, "IMPORT")]["digest"] = meta["digest"]
    repository = DynamoDbReferenceDataRepository(table)

    # When the normal repository entry point loads the reference model.
    model = repository.load_model(VERSION)

    # Then both shared fields retain their original identities and approved values.
    assert [(cde.cde_key, cde.cde_id) for cde in model.catalog] == [
        ("age", 4690995584867433052),
        ("stage", 2457258298240256618),
    ]
    assert dict(model.pvs.values) == {"age": frozenset(), "stage": frozenset({"I", "II"})}


def test_schema_two_accepts_fields_with_and_without_node_relationships() -> None:
    # Given one field without relationships and another retaining two node relationships.
    table = _published_table()
    meta = table.items[(MODEL_PK, "META")]
    del meta["nodes"]
    meta["node_count"] = 2
    meta["node_cde_count"] = 2
    del table.items[(MODEL_PK, "CDE#YWdl#META")]["node_relations"]
    meta["digest"] = "9dcf2c7e5b0508adad4bbab4679d11a31a8d124bc82769a95dd0667632878cea"
    table.items[(MODEL_PK, "IMPORT")]["digest"] = meta["digest"]
    repository = DynamoDbReferenceDataRepository(table)

    # When the adapter loads the sealed publication.
    reference = repository.load_cdes(VERSION)

    # Then it derives the nodes from the supplied relationships and retains both fields.
    assert reference.nodes == ("diagnosis", "sample")
    assert [(cde.cde_key, [relation.node_name for relation in cde.node_relations]) for cde in reference.cdes] == [
        ("age", []),
        ("stage", ["diagnosis", "sample"]),
    ]


@pytest.mark.parametrize(
    ("row", "field", "message"),
    [
        ("META", "nodes", "Reference model nodes are invalid"),
        ("CDE#YWdl#META", "node_relations", "Reference node relationships are invalid: age"),
    ],
)
def test_schema_two_rejects_malformed_optional_node_fields(row: str, field: str, message: str) -> None:
    # Given a published row supplying null for an optional list.
    table = _published_table()
    table.items[(MODEL_PK, row)][field] = None
    repository = DynamoDbReferenceDataRepository(table)

    # When the adapter decodes the publication.
    with pytest.raises(ReferenceDataCorruptError) as error:
        repository.load_model(VERSION)

    # Then the invalid field is rejected at the boundary.
    assert str(error.value) == message


def test_flat_reader_rejects_duplicate_node_relationships() -> None:
    # Given a CDE whose node relationships repeat the same node.
    table = _published_table()
    stage = table.items[(MODEL_PK, "CDE#c3RhZ2U#META")]
    relations = stage["node_relations"]
    assert isinstance(relations, list)
    relations.append(dict(relations[0]))
    repository = DynamoDbReferenceDataRepository(table)

    # When the complete flat model is read.
    with pytest.raises(ReferenceDataCorruptError) as error:
        repository.load_cdes(VERSION)

    # Then the duplicate relationship is rejected.
    assert "node CDE ids must be unique" in str(error.value)


@pytest.mark.parametrize(
    ("source_id", "message"),
    [(None, "field is invalid: source_node_cde_id"), (" ", "source node CDE id is invalid")],
)
def test_flat_reader_rejects_missing_or_blank_source_id(source_id: str | None, message: str) -> None:
    # Given a flat CDE row whose relationship lacks a usable source ID.
    table = _published_table()
    stage = table.items[(MODEL_PK, "CDE#c3RhZ2U#META")]
    relations = stage["node_relations"]
    assert isinstance(relations, list)
    if source_id is None:
        del relations[0]["source_node_cde_id"]
    else:
        relations[0]["source_node_cde_id"] = source_id
    repository = DynamoDbReferenceDataRepository(table)

    # When the flat model is read.
    with pytest.raises(ReferenceDataCorruptError) as error:
        repository.load_cdes(VERSION)

    # Then the missing or blank source ID is rejected.
    assert message in str(error.value)


def test_flat_reader_loads_values_from_multiple_published_chunks() -> None:
    # Given the published stage values split into two chunks under the same model seal.
    table = _published_table()
    table.items[(MODEL_PK, "CDE#c3RhZ2U#META")]["chunk_count"] = 2
    first = table.items[(MODEL_PK, "CDE#c3RhZ2U#VALUES#000000")]
    second_key = "CDE#c3RhZ2U#VALUES#000001"
    first.update(payload=gzip.compress(b'["I"]', mtime=0), uncompressed_size=len(b'["I"]'))
    table.items[(MODEL_PK, second_key)] = {
        **first,
        "sk": second_key,
        "chunk_index": 1,
        "payload": gzip.compress(b'["II"]', mtime=0),
        "uncompressed_size": len(b'["II"]'),
    }
    repository = DynamoDbReferenceDataRepository(table)

    # When the published model is loaded through the application view.
    model = repository.load_model(VERSION)

    # Then the original ID and both published values are present.
    stage = model.catalog.get("stage")
    assert stage is not None
    assert stage.cde_id == 2457258298240256618
    assert model.pvs.get("stage") == frozenset({"I", "II"})


@pytest.mark.parametrize(
    ("sort_key", "field", "value", "message"),
    [
        ("CDE#c3RhZ2U#META", "chunk_count", Decimal("1.5"), "Reference field is invalid: chunk_count"),
        ("CDE#c3RhZ2U#META", "chunk_count", 2, "Reference value chunks are incomplete: stage"),
        ("CDE#c3RhZ2U#META", "chunk_count", 0, "Reference value chunks are incomplete: stage"),
        ("CDE#c3RhZ2U#VALUES#000000", "chunk_index", 1, "Reference value chunks are incomplete: stage"),
        ("CDE#c3RhZ2U#META", "digest", "a" * 64, "Reference value digest is invalid: stage"),
    ],
)
def test_flat_reader_rejects_inconsistent_value_rows(
    sort_key: str, field: str, value: object, message: str
) -> None:
    # Given published value rows with one incorrect metadata or chunk field.
    table = _published_table()
    table.items[(MODEL_PK, sort_key)][field] = value
    repository = DynamoDbReferenceDataRepository(table)

    # When the published model is loaded through the application view.
    with pytest.raises(ReferenceDataCorruptError) as error:
        repository.load_model(VERSION)

    # Then the reader reports which catalog check failed.
    assert str(error.value) == message


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("payload", b"not gzip", "payload is corrupt"),
        ("payload", gzip.compress(b'["I","II"]', mtime=0)[:-8], "payload is corrupt"),
        ("uncompressed_size", 1, "payload size is invalid"),
    ],
)
def test_flat_reader_rejects_corrupt_value_chunks(field: str, value: object, message: str) -> None:
    # Given a flat CDE value chunk with a damaged payload or byte count.
    table = _published_table()
    table.items[(MODEL_PK, "CDE#c3RhZ2U#VALUES#000000")][field] = value
    repository = DynamoDbReferenceDataRepository(table)

    # When the complete flat model is read.
    with pytest.raises(ReferenceDataCorruptError) as error:
        repository.load_cdes(VERSION)

    # Then the damaged value chunk is rejected.
    assert message in str(error.value)
