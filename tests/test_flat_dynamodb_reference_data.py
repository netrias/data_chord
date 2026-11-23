"""Read independently specified flat schema-3 rows through Data Chord's adapter."""

from __future__ import annotations

import gzip
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from boto3.dynamodb.types import Binary, TypeDeserializer, TypeSerializer

from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceDataCorruptError
from src.integrations.dynamodb_reference_data import DynamoDbReferenceDataRepository

MODEL_PK = "MODEL#bW9kZWw#VERSION#MQ"
VERSION = DataModelVersionReference("model", "1")
MODEL_DIGEST = "6feaeeb15071466bfa85a54be5634ae1cece63fde630e1b8f1fa11357fc28127"
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
            "node_cde_id": 6354300659888498,
            "source_node_cde_id": "YuwtJC",
            "description": "Stage at diagnosis",
            "data_type": "string",
            "is_key": True,
            "is_required": True,
        },
        {
            "node_name": "sample",
            "node_cde_id": 7395480636342982,
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
            "schema_version": 3,
            "data_model_key": "model",
            "external_version_number": "1",
            "digest": MODEL_DIGEST,
        },
        {
            "pk": MODEL_PK,
            "sk": "CDE#YWdl#META",
            "schema_version": 3,
            "cde_key": "age",
            "cde_id": 148808179147131,
            "value_count": 0,
            "chunk_count": 0,
            "digest": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        },
        {
            "pk": MODEL_PK,
            "sk": "CDE#c3RhZ2U#META",
            "schema_version": 3,
            "cde_key": "stage",
            "cde_id": 8549938031380504,
            "description": "Disease stage",
            "node_relations": relations,
            "value_count": 2,
            "chunk_count": 1,
            "digest": "ad38db5821a8ce1688543c4c290da23e68116a15c3d715a3f5002125f1dc0ff0",
        },
        {
            "pk": MODEL_PK,
            "sk": "CDE#c3RhZ2U#VALUES#000000",
            "schema_version": 3,
            "cde_key": "stage",
            "chunk_index": 0,
            "uncompressed_size": len(b'["I","II"]'),
            "payload": gzip.compress(b'["I","II"]', mtime=0),
        },
        {
            "pk": MODEL_PK,
            "sk": "META",
            "schema_version": 3,
            "data_model_key": "model",
            "external_version_number": "1",
            "label": "Model",
            "nodes": ["diagnosis", "sample", "unused"],
            "node_count": 3,
            "node_cde_count": 2,
            "cde_count": 2,
            "value_count": 2,
            "digest": MODEL_DIGEST,
        },
        {
            "pk": "CATALOG",
            "sk": "IMPORT",
            "schema_version": 3,
            "model_count": 1,
            "digest": CATALOG_DIGEST,
            "source_digest": "a" * 64,
        },
        {
            "pk": "CATALOG",
            "sk": "MODEL#bW9kZWw#VERSION#MQ",
            "schema_version": 3,
            "data_model_key": "model",
            "external_version_number": "1",
            "label": "Model",
        },
        {
            "pk": "CATALOG",
            "sk": "META",
            "schema_version": 3,
            "model_count": 1,
            "digest": CATALOG_DIGEST,
            "source_digest": "a" * 64,
        },
    ]
    return Table({(str(row["pk"]), str(row["sk"])): row for row in rows})


def test_flat_reader_keeps_both_node_relationships() -> None:
    # Given a shared CDE, an unrelated CDE and a declared node with no CDEs.
    repository = DynamoDbReferenceDataRepository(_published_table())

    # When the integration loads the model and its node relationships.
    reference = repository.load_cdes(VERSION)

    # Then the node and relationship metadata survives under the shared CDE key.
    assert {node.value for node in reference.nodes} == {"diagnosis", "sample", "unused"}
    assert list(reference.node_relations) == ["stage"]
    assert [
        (r.node.value, r.node_cde_id, r.source_node_cde_id, r.description, r.data_type, r.is_key, r.is_required)
        for r in reference.node_relations["stage"]
    ] == [
        ("diagnosis", 6354300659888498, "YuwtJC", "Stage at diagnosis", "string", True, True),
        ("sample", 7395480636342982, "Abc123", "Stage on sample", "enum", False, False),
    ]


def test_application_projection_preserves_shared_identity_and_description() -> None:
    # Given a shared CDE with a CDE-wide description and two distinct placement descriptions.
    repository = DynamoDbReferenceDataRepository(_published_table())

    # When the application loads the model without node context.
    model = repository.load_model(VERSION)

    # Then it receives one JSON-safe ID and shared description, values and publication digest.
    stage = model.catalog.get("stage")
    assert stage is not None
    assert stage.cde_id == 8549938031380504
    assert stage.description == "Disease stage"
    assert model.pvs.get("stage") == frozenset({"I", "II"})
    assert model.pvs.get("age") == frozenset()
    assert model.publication_digest == MODEL_DIGEST


def test_flat_catalog_lists_published_versions() -> None:
    # Given a sealed catalog with model version 1.
    repository = DynamoDbReferenceDataRepository(_published_table())

    # When the model picker lists the published versions.
    summaries = repository.list_models()

    # Then it receives only the published model and version.
    assert [(s.data_model_key, [v.external_version_number for v in s.versions]) for s in summaries] == [
        ("model", ["1"])
    ]


def test_flat_catalog_rejects_old_node_first_schema() -> None:
    # Given a catalog advertised under the previous schema version.
    table = _published_table()
    table.items[("CATALOG", "META")]["schema_version"] = 2
    repository = DynamoDbReferenceDataRepository(table)

    # When the model picker lists the catalog.
    with pytest.raises(ReferenceDataCorruptError) as error:
        repository.list_models()

    # Then it rejects the old format before offering any models to the user.
    assert str(error.value) == "Reference schema version is unsupported"


def test_flat_reader_caches_the_complete_application_model() -> None:
    # Given a published flat model and a repository with an empty model cache.
    table = _published_table()
    repository = DynamoDbReferenceDataRepository(table)

    # When the same model is loaded twice through the application view.
    first = repository.load_model(VERSION)
    query_calls_after_first_load = table.query_calls
    second = repository.load_model(VERSION)

    # Then the second load returns the cached model without another table query.
    assert first is second
    assert query_calls_after_first_load > 1
    assert table.query_calls == query_calls_after_first_load


def test_flat_reader_accepts_boto3_numbers_and_binary_values() -> None:
    # Given flat rows converted to the Decimal and Binary types returned by boto3.
    table = _published_table()
    serializer = TypeSerializer()
    deserializer = TypeDeserializer()
    table.items = {
        key: {name: deserializer.deserialize(serializer.serialize(value)) for name, value in row.items()}
        for key, row in table.items.items()
    }
    # When the runtime reader loads the model from the paginated table.
    repository = DynamoDbReferenceDataRepository(table)
    model = repository.load_cdes(VERSION)

    # Then both values survive and the reader has followed pagination.
    assert isinstance(table.items[(MODEL_PK, "META")]["node_cde_count"], Decimal)
    assert isinstance(table.items[(MODEL_PK, "CDE#c3RhZ2U#VALUES#000000")]["payload"], Binary)
    assert model.model.pvs.get("stage") == frozenset({"I", "II"})
    assert table.query_calls > 1


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
    renamed["node_cde_id"] = 5878482677783671
    stage["node_relations"] = [relations[1], renamed]
    meta["nodes"] = ["sample", "unused", "zzz"]
    renamed_digest = "1161710d89160ca76e8581920d8054c4c168eaa6ce8ed9a1ed3e20eee867a805"
    meta["digest"] = renamed_digest
    table.items[(MODEL_PK, "IMPORT")]["digest"] = renamed_digest

    repository = DynamoDbReferenceDataRepository(table)

    # When the renamed model is read with its relationship metadata.
    reference = repository.load_cdes(VERSION)

    # Then the relationship changes identity but the shared CDE identity does not.
    relations = reference.node_relations["stage"]
    assert [relation.node.value for relation in relations] == ["sample", "zzz"]
    assert relations[1].node_cde_id == 5878482677783671
    assert relations[1].source_node_cde_id == "YuwtJC"
    collapsed_stage = reference.model.catalog.get("stage")
    assert collapsed_stage is not None
    assert collapsed_stage.cde_id == 8549938031380504


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
        relations[0]["node_cde_id"] = 4303952171635402
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


@pytest.mark.parametrize("field", ["node_count", "node_cde_count", "cde_count", "value_count"])
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
def test_flat_reader_accepts_a_model_without_nodes(omit_nodes: bool) -> None:
    # Given a published model without node relationships or declarations.
    table = _published_table()
    meta = table.items[(MODEL_PK, "META")]
    meta["nodes"] = []
    if omit_nodes:
        del meta["nodes"]
    del meta["node_count"]
    del meta["node_cde_count"]
    del table.items[(MODEL_PK, "CDE#c3RhZ2U#META")]["node_relations"]
    digest = "338fb6971d99934420570025ca55dad24091d4784757cb5789a403fb041e0fd3"
    meta["digest"] = digest
    table.items[(MODEL_PK, "IMPORT")]["digest"] = digest
    repository = DynamoDbReferenceDataRepository(table)

    # When the integration loads the model.
    reference = repository.load_cdes(VERSION)

    # Then the flat CDE catalog is usable and contains no node metadata.
    assert reference.nodes == frozenset()
    assert dict(reference.node_relations) == {}
    assert reference.model.catalog.keys() == ("age", "stage")
    assert reference.model.pvs.get("stage") == frozenset({"I", "II"})


def test_flat_reader_infers_nodes_when_only_relationships_are_present() -> None:
    # Given a model with relationships and no separately declared nodes.
    table = _published_table()
    meta = table.items[(MODEL_PK, "META")]
    del meta["nodes"]
    meta["node_count"] = 2
    digest = "e60c594e340fd5d5d1a34cf798818b5f6cc74fff5f7c6febfbca16827a3b18d6"
    meta["digest"] = digest
    table.items[(MODEL_PK, "IMPORT")]["digest"] = digest
    repository = DynamoDbReferenceDataRepository(table)

    # When the integration reads the model.
    reference = repository.load_cdes(VERSION)

    # Then only the nodes named on relationships are present.
    assert {node.value for node in reference.nodes} == {"diagnosis", "sample"}


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
    "source_id", ["", " ", 17],
)
def test_flat_reader_rejects_malformed_source_id(source_id: object) -> None:
    # Given a CDE relationship with a source ID in an invalid form.
    table = _published_table()
    stage = table.items[(MODEL_PK, "CDE#c3RhZ2U#META")]
    relations = stage["node_relations"]
    assert isinstance(relations, list)
    relations[0]["source_node_cde_id"] = source_id
    repository = DynamoDbReferenceDataRepository(table)

    # When the flat model is read.
    with pytest.raises(ReferenceDataCorruptError) as error:
        repository.load_cdes(VERSION)

    # Then the supplied invalid source ID is rejected.
    assert "source_node_cde_id" in str(error.value)


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
