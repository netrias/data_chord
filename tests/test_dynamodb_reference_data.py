from __future__ import annotations

import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from boto3.dynamodb.types import Binary, TypeDeserializer, TypeSerializer

from src.domain.cde import CdeType
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceDataCorruptError
from src.domain.reference_placements import CdePlacement, PlacedReferenceModel
from src.integrations.dynamodb_reference_data import (
    DynamoDbReferenceDataRepository,
    ReferenceDataImporter,
    _model_partition,
    _node_sort_key,
)

SOURCE_DIGEST = "a" * 64


@dataclass
class FakeTable:
    items: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)
    page_size: int = 2
    query_calls: int = 0

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
        self.query_calls += 1
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


class BlockingQueryTable(FakeTable):
    """DynamoDB fake that exposes concurrent query execution."""

    def __init__(self) -> None:
        super().__init__()
        self.first_query_started = threading.Event()
        self.release_queries = threading.Event()
        self.concurrent_query_started = threading.Event()
        self._active_queries = 0
        self._active_queries_lock = threading.Lock()

    def query(self, **kwargs: object) -> dict[str, object]:
        with self._active_queries_lock:
            self._active_queries += 1
            if self._active_queries > 1:
                self.concurrent_query_started.set()
            self.first_query_started.set()
        try:
            assert self.release_queries.wait(timeout=2)
            return super().query(**kwargs)
        finally:
            with self._active_queries_lock:
                self._active_queries -= 1


def _model(value_count: int = 120) -> PlacedReferenceModel:
    values = frozenset(f"value-{index:03d}" for index in range(value_count))
    return PlacedReferenceModel(
        version=DataModelVersionReference("model#one", "1/2"),
        label="Model One",
        nodes=("node#one",),
        placements=(
            CdePlacement("node#one", "cde#one", 42, "A CDE", "string", True),
            CdePlacement("node#one", "empty"),
        ),
        values={"cde#one": values, "empty": frozenset()},
    )


def _versioned_model(version: str) -> PlacedReferenceModel:
    model = _model(2)
    return PlacedReferenceModel(
        version=DataModelVersionReference("model#one", version),
        label=model.label,
        nodes=model.nodes,
        placements=model.placements,
        values=model.values,
    )


def _is_node_row(sk: str) -> bool:
    return sk.startswith("NODE#") and "#CDE#" not in sk


def _node_rows(table: FakeTable) -> dict[object, object]:
    """Each node's stored CDE count, keyed by node name."""
    return {item["node"]: item["cde_count"] for (_pk, sk), item in table.items.items() if _is_node_row(sk)}


def _shared_key_model() -> PlacedReferenceModel:
    """One CDE key placed under two nodes, which the flat model cannot hold."""
    return PlacedReferenceModel(
        version=DataModelVersionReference("model#one", "shared"),
        label="Model One",
        nodes=("diagnosis", "sample"),
        placements=(
            CdePlacement("diagnosis", "stage", 2, "Stage at diagnosis", "string", True, True),
            CdePlacement("sample", "stage", 7, "Stage at diagnosis", "enum", False, False),
        ),
        values={"stage": frozenset({"I", "II"})},
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
    assert loaded == _model().collapse()
    assert loaded.pvs.get("empty") == frozenset()


def test_one_cde_key_under_two_nodes_is_stored_separately_and_collapsed_on_read() -> None:
    # Given one CDE key placed under two nodes, which disagree on their metadata.
    table = FakeTable()
    model = _shared_key_model()
    ReferenceDataImporter(table).import_models([model], source_digest=SOURCE_DIGEST)
    repository = DynamoDbReferenceDataRepository(table)

    # Then storage keeps both placements, with each node's own metadata intact.
    stored = repository.load_placements(model.version)
    assert stored == model
    assert [(placement.node, placement.is_key) for placement in stored.placements] == [
        ("diagnosis", True),
        ("sample", False),
    ]
    assert [item["is_key"] for item in table.items.values() if "#CDE#" in item["sk"]] == [True, False]
    assert [(placement.node, placement.data_type) for placement in stored.placements] == [
        ("diagnosis", "string"),
        ("sample", "enum"),
    ]

    # And the application sees one CDE, carrying the lowest-sorting node's id.
    loaded = repository.load_model(model.version)
    assert loaded.catalog.keys() == ("stage",)
    stage = loaded.catalog.get("stage")
    assert stage is not None
    assert stage.cde_id == 2
    assert stage.cde_type is CdeType.PV


def test_a_shared_cde_keys_values_are_stored_once_for_all_its_nodes() -> None:
    # Given one CDE key placed under two nodes.
    table = FakeTable()
    model = _shared_key_model()
    ReferenceDataImporter(table).import_models([model], source_digest=SOURCE_DIGEST)

    # Then its values are written outside the node prefix, so neither node
    # carries its own copy of a list the source publishes only once.
    value_rows = [sk for (_pk, sk) in table.items if "#VALUES#" in sk]
    assert len(value_rows) == 1
    assert not any(sk.startswith("NODE#") for sk in value_rows)


def test_each_node_gets_a_row_holding_how_many_cdes_it_carries() -> None:
    # Given a model whose two nodes carry one CDE each.
    table = FakeTable()
    model = _shared_key_model()

    # When the model is imported.
    ReferenceDataImporter(table).import_models([model], source_digest=SOURCE_DIGEST)

    # Then each node has its own row, sorting above that node's placements.
    assert _node_rows(table) == {"diagnosis": 1, "sample": 1}


def test_a_node_that_holds_no_cdes_is_still_published_and_loaded() -> None:
    # Given a model that defines a node with no CDEs under it.
    table = FakeTable()
    model = PlacedReferenceModel(
        version=DataModelVersionReference("model#one", "empty-node"),
        label="Model One",
        nodes=("diagnosis", "unused"),
        placements=(CdePlacement("diagnosis", "stage"),),
        values={"stage": frozenset({"I"})},
    )

    # When the model is imported and loaded back.
    ReferenceDataImporter(table).import_models([model], source_digest=SOURCE_DIGEST)
    stored = DynamoDbReferenceDataRepository(table).load_placements(model.version)

    # Then the empty node survives the round trip, carrying a count of zero.
    assert stored == model
    assert stored.nodes == ("diagnosis", "unused")
    assert _node_rows(table) == {"diagnosis": 1, "unused": 0}


def test_load_rejects_placements_whose_node_row_was_deleted() -> None:
    # Given a published model whose node row was deleted, leaving its placements behind.
    table = FakeTable()
    model = _shared_key_model()
    ReferenceDataImporter(table).import_models([model], source_digest=SOURCE_DIGEST)
    del table.items[(_model_partition(model.version), _node_sort_key("diagnosis"))]

    # When the model is loaded.
    # Then the missing node row is named, rather than reported as a malformed model.
    with pytest.raises(ReferenceDataCorruptError, match="node with no row: diagnosis"):
        DynamoDbReferenceDataRepository(table).load_placements(model.version)


def test_the_model_digest_catches_a_renamed_node_that_holds_no_cdes() -> None:
    # Given a published model whose empty node was replaced by one under a different name.
    table = FakeTable()
    model = PlacedReferenceModel(
        version=DataModelVersionReference("model#one", "empty-node"),
        label="Model One",
        nodes=("diagnosis", "unused"),
        placements=(CdePlacement("diagnosis", "stage"),),
        values={"stage": frozenset({"I"})},
    )
    ReferenceDataImporter(table).import_models([model], source_digest=SOURCE_DIGEST)
    partition = _model_partition(model.version)
    stored = table.items.pop((partition, _node_sort_key("unused")))
    table.items[(partition, _node_sort_key("renamed"))] = {
        **stored,
        "sk": _node_sort_key("renamed"),
        "node": "renamed",
    }

    # When the model is loaded.
    # Then the digest catches the rename, which leaves every stored count correct.
    with pytest.raises(ReferenceDataCorruptError, match="model digest is invalid"):
        DynamoDbReferenceDataRepository(table).load_placements(model.version)


def test_load_rejects_a_node_row_whose_cde_count_was_changed() -> None:
    # Given a published model whose stored node count was edited in place.
    table = FakeTable()
    model = _shared_key_model()
    ReferenceDataImporter(table).import_models([model], source_digest=SOURCE_DIGEST)
    key = (_model_partition(model.version), _node_sort_key("diagnosis"))
    table.items[key] = {**table.items[key], "cde_count": 9}

    # When the model is loaded.
    # Then the node row is checked against the placements and the load fails.
    with pytest.raises(ReferenceDataCorruptError, match="node CDE count is invalid: diagnosis"):
        DynamoDbReferenceDataRepository(table).load_placements(model.version)


def test_stored_cde_count_includes_each_node_cde_pair() -> None:
    # Given a CDE key placed under two nodes.
    table = FakeTable()
    model = _shared_key_model()
    ReferenceDataImporter(table).import_models([model], source_digest=SOURCE_DIGEST)

    # Then the model CDE count agrees with the total of the node CDE counts.
    meta = next(item for (_pk, sk), item in table.items.items() if sk == "META" and "label" in item)
    assert meta["placement_count"] == 2
    assert meta["node_count"] == 2
    assert meta["cde_count"] == 2
    assert meta["cde_count"] == sum(_node_rows(table).values())
    # Two nodes hold the key, but its values are published and stored once.
    assert meta["value_count"] == 2


@pytest.mark.parametrize("field", ["placement_count", "node_count", "cde_count", "value_count"])
def test_load_rejects_a_count_that_disagrees_with_the_stored_rows(field: str) -> None:
    # Given a published model whose stored count was changed in place.
    table = FakeTable()
    model = _shared_key_model()
    ReferenceDataImporter(table).import_models([model], source_digest=SOURCE_DIGEST)
    meta_key = next(key for key in table.items if key[1] == "META" and "label" in table.items[key])
    table.items[meta_key][field] = 99

    # When the model is loaded, then the disagreement is rejected rather than collapsed over.
    with pytest.raises(ReferenceDataCorruptError, match="is invalid"):
        DynamoDbReferenceDataRepository(table).load_model(model.version)


def test_model_cache_hits_recent_versions_and_reloads_evicted_versions() -> None:
    # Given five published versions and a four-version in-process cache.
    table = FakeTable()
    models = [_versioned_model(str(version)) for version in range(1, 6)]
    ReferenceDataImporter(table).import_models(models, source_digest=SOURCE_DIGEST)
    repository = DynamoDbReferenceDataRepository(table)
    for model in models[:4]:
        assert repository.load_model(model.version) == model.collapse()
    queries_after_fill = table.query_calls

    # When a recent version is requested again, it is served from the cache.
    assert repository.load_model(models[0].version) == models[0].collapse()
    assert table.query_calls == queries_after_fill

    # When a fifth version is loaded, the least-recently-used version is evicted.
    assert repository.load_model(models[4].version) == models[4].collapse()
    queries_after_eviction = table.query_calls

    # Then the recent version still hits, while the evicted version reloads exactly.
    assert repository.load_model(models[0].version) == models[0].collapse()
    assert table.query_calls == queries_after_eviction
    assert repository.load_model(models[1].version) == models[1].collapse()
    assert table.query_calls > queries_after_eviction


def test_model_cache_serializes_full_model_loads() -> None:
    # Given two published versions that are not cached.
    table = BlockingQueryTable()
    models = [_versioned_model("1"), _versioned_model("2")]
    table.release_queries.set()
    ReferenceDataImporter(table).import_models(models, source_digest=SOURCE_DIGEST)
    table.release_queries.clear()
    table.first_query_started.clear()
    table.concurrent_query_started.clear()
    repository = DynamoDbReferenceDataRepository(table)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_load = executor.submit(repository.load_model, models[0].version)
        assert table.first_query_started.wait(timeout=2)
        second_load = executor.submit(repository.load_model, models[1].version)
        try:
            # Then the second full-model load waits instead of adding another
            # complete set of DynamoDB rows to process memory.
            assert not table.concurrent_query_started.wait(timeout=0.1)
        finally:
            table.release_queries.set()
        assert first_load.result(timeout=2) == models[0].collapse()
        assert second_load.result(timeout=2) == models[1].collapse()


def test_catalog_query_pages_and_groups_versions() -> None:
    # Given two complete model versions and a catalog page size of one.
    table = FakeTable(page_size=1)
    importer = ReferenceDataImporter(table)
    importer.import_models([_model(2), _versioned_model("2")], source_digest=SOURCE_DIGEST)

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
    assert loaded == _model().collapse()


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
    assert loaded == _model().collapse()


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
    assert DynamoDbReferenceDataRepository(table).load_model(_model(2).version) == _model(2).collapse()


def test_catalog_rejects_a_different_approved_source() -> None:
    # Given a complete catalog published from one approved source file.
    table = FakeTable()
    importer = ReferenceDataImporter(table)
    importer.import_models([_model(2)], source_digest=SOURCE_DIGEST)
    before = dict(table.items)

    # When a different source digest tries to add another model.
    with pytest.raises(RuntimeError, match="conditional"):
        importer.import_models([_model(2), _versioned_model("2")], source_digest="b" * 64)

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
    changed = PlacedReferenceModel(
        version=original.version,
        label=original.label,
        nodes=original.nodes,
        placements=(*original.placements, CdePlacement("node#one", "new")),
        values={**original.values, "new": frozenset({"new-value"})},
    )

    # When the changed copy is imported, then its seal conflicts before new rows are written.
    before = dict(table.items)
    with pytest.raises(RuntimeError, match="conditional"):
        importer.import_models([changed], source_digest=SOURCE_DIGEST)
    assert table.items == before
    assert DynamoDbReferenceDataRepository(table).load_model(original.version) == original.collapse()


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
    second = _versioned_model("2")

    # When the batch import is interrupted, then only its hidden source seal exists.
    with pytest.raises(RuntimeError, match="conditional write failed"):
        ReferenceDataImporter(table).import_models([first, second], source_digest=SOURCE_DIGEST)
    assert {key for key in table.items if key[0] == "CATALOG"} == {("CATALOG", "IMPORT")}
    assert DynamoDbReferenceDataRepository(table).list_models() == ()


def test_catalog_rows_are_not_trusted_before_the_batch_marker() -> None:
    # Given both model partitions verify but catalog publication stops before its final marker.
    # The catalog seal, both eight-row model partitions, then one catalog row.
    table = FailingTable(writes_before_failure=18)
    first = _model(2)
    second = _versioned_model("2")
    with pytest.raises(RuntimeError, match="conditional write failed"):
        ReferenceDataImporter(table).import_models([first, second], source_digest=SOURCE_DIGEST)

    # When catalog data is read, then the partial publication fails closed.
    assert any(pk == "CATALOG" for pk, _sk in table.items)
    with pytest.raises(ReferenceDataCorruptError, match="not completely published"):
        DynamoDbReferenceDataRepository(table).list_models()


def test_one_oversize_value_is_rejected_before_any_write() -> None:
    # Given one value that cannot fit inside the configured item boundary.
    table = FakeTable()
    model = PlacedReferenceModel(
        version=DataModelVersionReference("model", "large"),
        label="Large",
        nodes=("node",),
        placements=(CdePlacement("node", "large"),),
        values={"large": frozenset({"x" * 500})},
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
    model = PlacedReferenceModel(
        version=DataModelVersionReference("model", "large-metadata"),
        label="Large",
        nodes=("node",),
        placements=(CdePlacement("node", "field", description="x" * 400_000),),
        values={"field": frozenset()},
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
