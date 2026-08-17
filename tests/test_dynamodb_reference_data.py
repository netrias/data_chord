from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from boto3.dynamodb.types import Binary, TypeDeserializer, TypeSerializer

from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceDataCorruptError, ReferenceModel
from src.integrations.dynamodb_reference_data import (
    DynamoDbReferenceDataRepository,
    ReferenceDataImporter,
)

SOURCE_DIGEST = "a" * 64


@dataclass
class FakeTable:
    items: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)
    page_size: int = 2

    def put_item(self, *, Item: Mapping[str, object], ConditionExpression: str | None = None) -> dict[str, object]:
        key = (str(Item["pk"]), str(Item["sk"]))
        existing = self.items.get(key)
        if ConditionExpression is not None and existing is not None and existing != dict(Item):
            raise RuntimeError("conditional write failed")
        self.items[key] = dict(Item)
        return {}

    def get_item(self, *, Key: Mapping[str, object], ConsistentRead: bool) -> dict[str, object]:
        assert ConsistentRead is True
        item = self.items.get((str(Key["pk"]), str(Key["sk"])))
        return {"Item": item} if item is not None else {}

    def query(self, **kwargs: object) -> dict[str, object]:
        assert kwargs["ConsistentRead"] is True
        values = kwargs["ExpressionAttributeValues"]
        assert isinstance(values, Mapping)
        pk = str(values[":pk"])
        rows = [item for (item_pk, _), item in sorted(self.items.items()) if item_pk == pk]
        start_key = kwargs.get("ExclusiveStartKey")
        start = 0
        if isinstance(start_key, Mapping):
            start_sk = str(start_key["sk"])
            start = next(index + 1 for index, item in enumerate(rows) if item["sk"] == start_sk)
        page = rows[start : start + self.page_size]
        response: dict[str, object] = {"Items": page}
        if start + self.page_size < len(rows):
            response["LastEvaluatedKey"] = {"pk": pk, "sk": page[-1]["sk"]}
        return response


@dataclass
class FailingTable(FakeTable):
    writes_before_failure: int = 1

    def put_item(self, *, Item: Mapping[str, object], ConditionExpression: str | None = None) -> dict[str, object]:
        if self.writes_before_failure == 0:
            raise RuntimeError("interrupted import")
        self.writes_before_failure -= 1
        return super().put_item(Item=Item, ConditionExpression=ConditionExpression)


def _model(value_count: int = 120) -> ReferenceModel:
    values = frozenset(f"value-{index:03d}" for index in range(value_count))
    return ReferenceModel(
        version=DataModelVersionReference("model#one", "1/2"),
        label="Model One",
        catalog=CdeCatalog.from_cdes(
            [
                CDEInfo(42, "cde#one", "A CDE", CdeType.PV),
                CDEInfo(None, "empty", None, CdeType.PASSTHROUGH),
            ]
        ),
        pvs=CdePvCatalog.from_mapping({"cde#one": values, "empty": frozenset()}),
    )


def test_import_and_load_preserve_multi_page_multi_chunk_model() -> None:
    # Given a model that needs several DynamoDB query pages and value chunks.
    table = FakeTable(page_size=2)
    importer = ReferenceDataImporter(table, value_chunk_bytes=180)

    # When it is imported and loaded through the repository.
    importer.import_models([_model()], source_digest=SOURCE_DIGEST)
    loaded = DynamoDbReferenceDataRepository(table).load_model(
        DataModelVersionReference("model#one", "1/2")
    )

    # Then all metadata and exact values are restored, including explicit empty values.
    assert loaded == _model()
    assert loaded.pvs.get("empty") == frozenset()


def test_catalog_query_pages_and_groups_versions() -> None:
    # Given two complete model versions and a catalog page size of one.
    table = FakeTable(page_size=1)
    importer = ReferenceDataImporter(table)
    second = ReferenceModel(
        version=DataModelVersionReference("model#one", "2"),
        label="Model One",
        catalog=_model(2).catalog,
        pvs=_model(2).pvs,
    )
    importer.import_models([_model(2), second], source_digest=SOURCE_DIGEST)

    # When catalog entries are listed.
    summaries = DynamoDbReferenceDataRepository(table).list_models()

    # Then versions are grouped under one model in stable order.
    assert len(summaries) == 1
    assert [version.external_version_number for version in summaries[0].versions] == ["1/2", "2"]


def test_catalog_rejects_an_identity_that_does_not_match_its_key() -> None:
    # Given one catalog row whose stored identity was changed in place.
    table = FakeTable()
    ReferenceDataImporter(table).import_models([_model(2)], source_digest=SOURCE_DIGEST)
    catalog_key = next(key for key in table.items if key[0] == "CATALOG" and key[1].startswith("MODEL#"))
    table.items[catalog_key]["external_version_number"] = "different"

    # When catalog data is listed, then the corrupt identity is rejected.
    with pytest.raises(ReferenceDataCorruptError, match="identity"):
        DynamoDbReferenceDataRepository(table).list_models()


def test_load_accepts_the_binary_type_returned_by_boto3() -> None:
    # Given DynamoDB returns its Binary wrapper for stored chunk payloads.
    table = FakeTable()
    ReferenceDataImporter(table, value_chunk_bytes=180).import_models([_model()], source_digest=SOURCE_DIGEST)
    for item in table.items.values():
        if "payload" in item:
            item["payload"] = Binary(item["payload"])

    # When the repository loads the model, then boto3 binary values decode exactly.
    loaded = DynamoDbReferenceDataRepository(table).load_model(DataModelVersionReference("model#one", "1/2"))
    assert loaded == _model()


def test_load_accepts_the_number_and_binary_types_returned_by_boto3() -> None:
    # Given stored rows pass through boto3's real DynamoDB value conversion.
    table = FakeTable()
    ReferenceDataImporter(table, value_chunk_bytes=180).import_models([_model()], source_digest=SOURCE_DIGEST)
    serializer = TypeSerializer()
    deserializer = TypeDeserializer()
    for key, item in list(table.items.items()):
        table.items[key] = {
            field: deserializer.deserialize(serializer.serialize(value))
            for field, value in item.items()
        }

    # When the repository loads the model, then integral Decimal fields are accepted.
    assert any(isinstance(item.get("schema_version"), Decimal) for item in table.items.values())
    loaded = DynamoDbReferenceDataRepository(table).load_model(DataModelVersionReference("model#one", "1/2"))
    assert loaded == _model()


def test_same_import_is_idempotent_and_changed_content_stops() -> None:
    # Given one complete imported model.
    table = FakeTable()
    importer = ReferenceDataImporter(table)
    importer.import_models([_model(2)], source_digest=SOURCE_DIGEST)

    # When the same model is imported again, then it succeeds without changing rows.
    before = dict(table.items)
    importer.import_models([_model(2)], source_digest=SOURCE_DIGEST)
    assert table.items == before

    # When content at the same model identity changes, then the import stops.
    with pytest.raises(RuntimeError, match="conditional"):
        importer.import_models([_model(3)], source_digest=SOURCE_DIGEST)
    assert DynamoDbReferenceDataRepository(table).load_model(_model(2).version) == _model(2)


def test_catalog_rejects_a_different_approved_source() -> None:
    # Given a complete catalog published from one approved source file.
    table = FakeTable()
    importer = ReferenceDataImporter(table)
    importer.import_models([_model(2)], source_digest=SOURCE_DIGEST)
    before = dict(table.items)

    changed_source = ReferenceModel(
        version=DataModelVersionReference("model#one", "2"),
        label="Model One",
        catalog=_model(2).catalog,
        pvs=_model(2).pvs,
    )

    # When a different source digest tries to add another model.
    with pytest.raises(RuntimeError, match="conditional"):
        importer.import_models([_model(2), changed_source], source_digest="b" * 64)

    # Then the original source marker and all table rows remain unchanged.
    assert table.items == before
    assert table.items[("CATALOG", "META")]["source_digest"] == SOURCE_DIGEST
    assert len(DynamoDbReferenceDataRepository(table).list_models()) == 1


def test_changed_reimport_cannot_add_rows_to_a_published_model() -> None:
    # Given one published model and a changed copy with an extra CDE.
    table = FakeTable()
    importer = ReferenceDataImporter(table)
    original = _model(2)
    importer.import_models([original], source_digest=SOURCE_DIGEST)
    changed = ReferenceModel(
        version=original.version,
        label=original.label,
        catalog=CdeCatalog.from_cdes([
            *original.catalog,
            CDEInfo(None, "new", None, CdeType.PV),
        ]),
        pvs=CdePvCatalog.from_mapping({
            **original.pvs.values,
            "new": frozenset({"new-value"}),
        }),
    )

    # When the changed copy is imported, then its seal conflicts before new rows are written.
    before = dict(table.items)
    with pytest.raises(RuntimeError, match="conditional"):
        importer.import_models([changed], source_digest=SOURCE_DIGEST)
    assert table.items == before
    assert DynamoDbReferenceDataRepository(table).load_model(original.version) == original


def test_interrupted_import_is_not_listed_and_can_resume() -> None:
    # Given an import stops before the complete marker and catalog row.
    table = FailingTable(writes_before_failure=2)
    with pytest.raises(RuntimeError, match="conditional write failed"):
        ReferenceDataImporter(table, value_chunk_bytes=180).import_models(
            [_model()], source_digest=SOURCE_DIGEST
        )

    # Then the incomplete model is not listed.
    assert DynamoDbReferenceDataRepository(table).list_models() == ()

    # When the same import resumes, then matching partial rows are accepted and the model is published.
    table.writes_before_failure = 1_000
    ReferenceDataImporter(table, value_chunk_bytes=180).import_models(
        [_model()], source_digest=SOURCE_DIGEST
    )
    assert len(DynamoDbReferenceDataRepository(table).list_models()) == 1


def test_interrupted_multi_model_import_does_not_publish_a_partial_catalog() -> None:
    # Given an import stops after the first model is complete but before the batch is complete.
    table = FailingTable(writes_before_failure=6)
    first = _model(2)
    second = ReferenceModel(
        version=DataModelVersionReference("model#one", "2"),
        label=first.label,
        catalog=first.catalog,
        pvs=first.pvs,
    )

    # When the batch import is interrupted, then only its hidden source seal exists.
    with pytest.raises(RuntimeError, match="conditional write failed"):
        ReferenceDataImporter(table).import_models([first, second], source_digest=SOURCE_DIGEST)
    assert {key for key in table.items if key[0] == "CATALOG"} == {("CATALOG", "IMPORT")}
    assert DynamoDbReferenceDataRepository(table).list_models() == ()


def test_catalog_rows_are_not_trusted_before_the_batch_marker() -> None:
    # Given both model partitions verify but catalog publication stops before its final marker.
    table = FailingTable(writes_before_failure=12)
    first = _model(2)
    second = ReferenceModel(
        version=DataModelVersionReference("model#one", "2"),
        label=first.label,
        catalog=first.catalog,
        pvs=first.pvs,
    )
    with pytest.raises(RuntimeError, match="conditional write failed"):
        ReferenceDataImporter(table).import_models([first, second], source_digest=SOURCE_DIGEST)

    # When catalog data is read, then the partial publication fails closed.
    assert any(pk == "CATALOG" for pk, _sk in table.items)
    with pytest.raises(ReferenceDataCorruptError, match="not completely published"):
        DynamoDbReferenceDataRepository(table).list_models()


def test_one_oversize_value_is_rejected_before_any_write() -> None:
    # Given one value that cannot fit inside the configured item boundary.
    table = FakeTable()
    model = ReferenceModel(
        version=DataModelVersionReference("model", "large"),
        label="Large",
        catalog=CdeCatalog.from_cdes([CDEInfo(None, "large", None, CdeType.PV)]),
        pvs=CdePvCatalog.from_mapping({"large": frozenset({"x" * 500})}),
    )

    # When import starts, then the model is rejected before DynamoDB is changed.
    with pytest.raises(ValueError, match="too large"):
        ReferenceDataImporter(table, value_chunk_bytes=100).import_models(
            [model], source_digest=SOURCE_DIGEST
        )
    assert table.items == {}


def test_oversize_metadata_is_rejected_before_any_write() -> None:
    # Given metadata that cannot fit within the conservative item boundary.
    table = FakeTable()
    model = ReferenceModel(
        version=DataModelVersionReference("model", "large-metadata"),
        label="Large",
        catalog=CdeCatalog.from_cdes([CDEInfo(None, "field", "x" * 400_000, CdeType.PASSTHROUGH)]),
        pvs=CdePvCatalog.from_mapping({"field": frozenset()}),
    )

    # When import starts, then all rows are validated before DynamoDB is changed.
    with pytest.raises(ValueError, match="item is too large"):
        ReferenceDataImporter(table).import_models([model], source_digest=SOURCE_DIGEST)
    assert table.items == {}


@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong-size", "extra"])
def test_load_rejects_incomplete_or_corrupt_chunks(damage: str) -> None:
    # Given a complete model whose stored value chunks are then damaged.
    table = FakeTable()
    ReferenceDataImporter(table, value_chunk_bytes=180).import_models(
        [_model()], source_digest=SOURCE_DIGEST
    )
    chunk_keys = [key for key in table.items if "#VALUES#" in key[1]]
    assert chunk_keys
    if damage == "missing":
        table.items.pop(chunk_keys[0])
    elif damage == "corrupt":
        table.items[chunk_keys[0]]["payload"] = b"not gzip"
    elif damage == "wrong-size":
        table.items[chunk_keys[0]]["uncompressed_size"] = 1
    else:
        extra = dict(table.items[chunk_keys[0]])
        extra["sk"] = f'{extra["sk"]}#EXTRA'
        table.items[(str(extra["pk"]), str(extra["sk"]))] = extra

    # When the model is loaded, then damaged reference data is never returned.
    with pytest.raises(ReferenceDataCorruptError):
        DynamoDbReferenceDataRepository(table).load_model(
            DataModelVersionReference("model#one", "1/2")
        )
