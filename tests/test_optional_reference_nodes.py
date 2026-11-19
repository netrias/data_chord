from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from src.cde_recommend.result_cache import EmptyRecommendationCache
from src.cde_recommend.types import PotentialMatchIndex
from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_node_relation import CdeNodeRelation, NodeName
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.cde_recommendation import ProfiledColumn
from src.domain.cde_type_classification import refine_cde_types_from_pvs
from src.domain.column_profile import build_column_profile
from src.domain.columns import ColumnIdentity, column_key_for_index
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel, ReferenceNodeMetadataUnsupportedError
from src.integrations.cde_recommendation import CdeRecommendationAdapter
from src.integrations.dynamodb_reference_data import ReferenceDataImporter
from src.integrations.reference_data_file import save_reference_models
from src.integrations.sqlite_reference_data import SqliteReferenceDataImporter


def _relation(node: NodeName, source_id: int = 2) -> CdeNodeRelation:
    return CdeNodeRelation(
        node=node,
        source_node_cde_id=source_id,
        description=f"Stage for {node.value}",
        data_type="string",
        is_required=True,
        is_key=False,
    )


def _model(cde: CDEInfo, nodes: frozenset[NodeName] = frozenset()) -> ReferenceModel:
    return ReferenceModel(
        version=DataModelVersionReference("model", "1"),
        label="Model",
        catalog=CdeCatalog.from_cdes([cde]),
        pvs=CdePvCatalog.from_mapping({cde.cde_key: frozenset({"I", "II"})}),
        nodes=nodes,
    )


def test_existing_reference_constructors_keep_node_free_defaults() -> None:
    # Given the original CDE constructor with a CDE-wide ID and description.
    cde = CDEInfo(42, "stage", "Stage")

    # When an existing caller builds its reference model without node arguments.
    model = _model(cde)

    # Then the flat CDE and value set are unchanged and no node metadata is invented.
    assert model.nodes == frozenset()
    assert cde.node_relations == ()
    assert model.catalog.to_list() == [CDEInfo(42, "stage", "Stage", CdeType.PV)]
    assert model.pvs.values == {"stage": frozenset({"I", "II"})}


def test_shared_cde_keeps_distinct_node_metadata_and_one_value_set() -> None:
    # Given two nodes with distinct source IDs, descriptions, types and flags.
    diagnosis = _relation(NodeName("diagnosis"))
    sample = CdeNodeRelation(NodeName("sample"), 7, "Sample stage", "enum", False, True)
    cde = CDEInfo(42, "stage", "Shared stage", node_relations=(sample, diagnosis))

    # When the flat model is constructed with both relationships.
    model = _model(cde)

    # Then one CDE owns the shared values while both complete relationships survive.
    assert model.catalog.to_list() == [cde]
    assert cde.node_relations == (diagnosis, sample)
    assert model.nodes == frozenset({NodeName("diagnosis"), NodeName("sample")})
    assert model.pvs.values == {"stage": frozenset({"I", "II"})}
    assert cde.cde_id == 42 and cde.description == "Shared stage"


def test_model_keeps_nodes_without_cdes() -> None:
    # Given a CDE in diagnosis and a separately declared node with no CDEs.
    cde = CDEInfo(42, "stage", None, node_relations=(_relation(NodeName("diagnosis")),))
    unused = NodeName("unused")

    # When the model combines its relationships and explicitly declared nodes.
    model = _model(cde, frozenset({unused}))

    # Then both node names survive without creating a CDE for the unused node.
    assert model.nodes == frozenset({NodeName("diagnosis"), unused})
    assert model.catalog.keys() == ("stage",)


@pytest.mark.parametrize("name", ["", " ", "\t\n"])
def test_blank_node_names_are_rejected(name: str) -> None:
    # Given a raw node name containing no meaningful characters.
    raw_name = name

    # When it is converted to the domain's node identity.
    with pytest.raises(ValueError) as error:
        NodeName(raw_name)

    # Then the constructor reports the invalid identity.
    assert str(error.value) == "Reference node name is required"


@pytest.mark.parametrize("source_id", [-1, True])
def test_invalid_source_relationship_ids_are_rejected(source_id: int) -> None:
    # Given a source ID outside the existing nonnegative integer ID contract.
    node = NodeName("diagnosis")

    # When a relationship is constructed with that ID.
    with pytest.raises(ValueError) as error:
        CdeNodeRelation(node, source_node_cde_id=source_id)

    # Then it rejects the ID rather than confusing it with a CDE-wide ID.
    assert str(error.value) == "Source node-CDE ID must be a nonnegative integer"


def test_duplicate_relationships_for_the_same_cde_and_node_are_rejected() -> None:
    # Given two relationships to the same node with different source metadata.
    node = NodeName("diagnosis")
    relationships = (_relation(node), _relation(node, 7))

    # When the CDE is constructed with both relationships.
    with pytest.raises(ValueError) as error:
        CDEInfo(42, "stage", None, node_relations=relationships)

    # Then ambiguity is rejected instead of selecting one relationship by order.
    assert str(error.value) == "CDE node relationships must be unique per node: stage"


def test_refining_cde_type_preserves_node_metadata() -> None:
    # Given a CDE whose type needs refinement and whose relationships carry metadata.
    cde = CDEInfo(42, "stage", "Shared stage", node_relations=(_relation(NodeName("diagnosis")),))
    catalog = CdeCatalog.from_cdes([cde])

    # When fetched values show that this CDE is pass-through.
    refined = refine_cde_types_from_pvs(catalog, CdePvCatalog.from_mapping({"stage": frozenset()}))

    # Then only the type changes; the identity and node metadata remain intact.
    assert refined.to_list() == [replace(cde, cde_type=CdeType.PASSTHROUGH)]
    assert cde.cde_type is CdeType.PV


class _UnexpectedRanker:
    async def rank(self, developer_message: str, user_message: str) -> list[PotentialMatchIndex]:
        raise AssertionError("The exact CDE name must not call a model provider")


@pytest.mark.asyncio
async def test_recommendations_keep_cde_identity_when_nodes_are_reordered_or_renamed() -> None:
    # Given a shared CDE and variants with reversed relationships or a renamed node.
    diagnosis = _relation(NodeName("diagnosis"))
    sample = _relation(NodeName("sample"), 7)
    variants = [(diagnosis, sample), (sample, diagnosis), (replace(diagnosis, node=NodeName("zzz")), sample)]
    key = column_key_for_index(0)
    column = ProfiledColumn(ColumnIdentity(key, "stage"), build_column_profile(key, ["I", "II"]))
    recommender = CdeRecommendationAdapter(_UnexpectedRanker(), EmptyRecommendationCache())

    # When the real recommendation pipeline evaluates each reference model.
    manifests = [
        await recommender.recommend([column], _model(CDEInfo(42, "stage", "Shared stage", node_relations=relations)))
        for relations in variants
    ]

    # Then all saved mappings use the same CDE-wide identity and target.
    assert [manifest.records[key].cde_id for manifest in manifests] == [42, 42, 42]
    assert [manifest.records[key].cde_key for manifest in manifests] == ["stage", "stage", "stage"]
    assert all(manifest == manifests[0] for manifest in manifests)


def _publication_models() -> list[ReferenceModel]:
    flat = _model(CDEInfo(42, "stage", "Shared stage"))
    related = replace(
        _model(CDEInfo(42, "stage", None, node_relations=(_relation(NodeName("diagnosis")),))),
        version=DataModelVersionReference("model", "2"),
    )
    return [flat, related]


def test_file_publication_rejects_nodes_without_overwriting_existing_content(tmp_path: Path) -> None:
    # Given a destination with existing content and a batch containing node metadata.
    destination = tmp_path / "reference.json"
    destination.write_bytes(b"existing approved export")
    models = _publication_models()

    # When the current node-free export format is asked to publish the batch.
    with pytest.raises(ReferenceNodeMetadataUnsupportedError) as error:
        save_reference_models(destination, models)

    # Then it reports the unsupported metadata and leaves the whole file untouched.
    assert str(error.value) == "Node metadata requires a node-aware reference publisher"
    assert destination.read_bytes() == b"existing approved export"


def test_sqlite_publication_rejects_an_empty_node_without_changing_the_database(tmp_path: Path) -> None:
    # Given a populated database and a batch that also declares a node with no CDEs.
    database = tmp_path / "reference.sqlite"
    importer = SqliteReferenceDataImporter(database)
    flat = _model(CDEInfo(42, "stage", "Shared stage"))
    importer.import_models([flat])
    before = database.read_bytes()
    empty_node_model = replace(
        flat, version=DataModelVersionReference("model", "2"), nodes=frozenset({NodeName("unused")})
    )

    # When the current SQLite format is asked to publish the unsupported batch.
    with pytest.raises(ReferenceNodeMetadataUnsupportedError) as error:
        importer.import_models([flat, empty_node_model])

    # Then no partial batch or metadata loss occurs.
    assert str(error.value) == "Node metadata requires a node-aware reference publisher"
    assert database.read_bytes() == before


@dataclass
class _NoDynamoCalls:
    calls: list[str] = field(default_factory=list)

    def get_item(self, Key: Mapping[str, object], ConsistentRead: bool) -> Mapping[str, object]:
        self.calls.append("get_item")
        raise AssertionError("The unsupported batch must be rejected before any DynamoDB call")

    def put_item(self, Item: Mapping[str, object], ConditionExpression: str | None = None) -> object:
        self.calls.append("put_item")
        raise AssertionError("The unsupported batch must be rejected before any DynamoDB call")

    def query(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append("query")
        raise AssertionError("The unsupported batch must be rejected before any DynamoDB call")


def test_dynamo_publication_rejects_nodes_before_any_table_calls() -> None:
    # Given a batch with a flat model followed by a node-bearing model.
    table = _NoDynamoCalls()
    models = _publication_models()

    # When the current DynamoDB publisher is asked to publish the batch.
    with pytest.raises(ReferenceNodeMetadataUnsupportedError) as error:
        ReferenceDataImporter(table).import_models(models, source_digest="a" * 64)

    # Then the complete batch is rejected before any read or write to DynamoDB.
    assert str(error.value) == "Node metadata requires a node-aware reference publisher"
    assert table.calls == []
