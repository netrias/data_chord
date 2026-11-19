from dataclasses import replace

import pytest

from src.cde_recommend.result_cache import EmptyRecommendationCache
from src.cde_recommend.types import PotentialMatchIndex
from src.domain.cde import CDEInfo
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.cde_recommendation import ProfiledColumn
from src.domain.column_profile import build_column_profile
from src.domain.columns import ColumnIdentity, column_key_for_index
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel
from src.integrations.auto_model_reference import AutoModelReference, CdeNodeRelation, NodeName
from src.integrations.cde_recommendation import CdeRecommendationAdapter


@pytest.fixture
def flat_model() -> ReferenceModel:
    return ReferenceModel(
        version=DataModelVersionReference("model", "1"),
        label="Model",
        catalog=CdeCatalog.from_cdes([CDEInfo(42, "stage", "Shared stage")]),
        pvs=CdePvCatalog.from_mapping({"stage": frozenset({"I", "II"})}),
    )


def test_auto_model_can_omit_node_metadata(flat_model: ReferenceModel) -> None:
    # Given a reference model with one flat CDE and no node metadata.
    assert flat_model.catalog.keys() == ("stage",)

    # When Auto-Model supplies the model alone.
    reference = AutoModelReference(flat_model)

    # Then there are no node relationships and the original model is available to the application.
    assert reference.node_relations == {}
    assert reference.nodes == frozenset()
    assert reference.model is flat_model


def test_auto_model_keeps_distinct_relationships_and_empty_nodes(flat_model: ReferenceModel) -> None:
    # Given two relationships for the same CDE and a declared node without any CDEs.
    diagnosis = CdeNodeRelation(NodeName("diagnosis"), 2, "Diagnosis stage", "string", True, False)
    sample = CdeNodeRelation(NodeName("sample"), 7, "Sample stage", "enum", False, True)
    unused = NodeName("unused")
    relations = {"stage": (sample, diagnosis)}

    # When the integration retains the node metadata and the caller clears its input.
    reference = AutoModelReference(flat_model, relations, frozenset({unused}))
    relations.clear()

    # Then it owns a snapshot of the relationships, includes the empty node and shares one value set.
    assert reference.node_relations == {"stage": (diagnosis, sample)}
    assert reference.nodes == frozenset({diagnosis.node, sample.node, unused})
    assert reference.model.catalog.to_list() == [CDEInfo(42, "stage", "Shared stage")]
    assert reference.model.pvs.values == {"stage": frozenset({"I", "II"})}


def test_auto_model_rejects_relationships_for_unknown_cdes(flat_model: ReferenceModel) -> None:
    # Given a relationship for a CDE absent from the flat catalog.
    relations = {"absent": (CdeNodeRelation(NodeName("diagnosis")),)}

    # When the integration receives that metadata.
    with pytest.raises(ValueError) as error:
        AutoModelReference(flat_model, relations)

    # Then the error identifies the missing CDE.
    assert str(error.value) == "Node relationships refer to an unknown CDE: absent"


def test_auto_model_rejects_duplicate_relationships(flat_model: ReferenceModel) -> None:
    # Given two source records for the same CDE and node.
    node = NodeName("diagnosis")
    relations = {"stage": (CdeNodeRelation(node, 2), CdeNodeRelation(node, 7))}

    # When the integration receives both records.
    with pytest.raises(ValueError) as error:
        AutoModelReference(flat_model, relations)

    # Then the error identifies the CDE with ambiguous relationships.
    assert str(error.value) == "CDE node relationships must be unique per node: stage"


def test_blank_node_name_is_rejected() -> None:
    # Given a source node name consisting of whitespace.
    raw_name = " \t "

    # When the integration constructs a node name.
    with pytest.raises(ValueError) as error:
        NodeName(raw_name)

    # Then it reports that the node name is required.
    assert str(error.value) == "Reference node name is required"


@pytest.mark.parametrize("source_id", [-1, True])
def test_invalid_source_relationship_ids_are_rejected(source_id: int) -> None:
    # Given a node and a source relationship ID outside the nonnegative integer contract.
    node = NodeName("diagnosis")

    # When the integration constructs the relationship.
    with pytest.raises(ValueError) as error:
        CdeNodeRelation(node, source_node_cde_id=source_id)

    # Then it reports the invalid relationship ID.
    assert str(error.value) == "Source node-CDE ID must be a nonnegative integer"


class _UnexpectedRanker:
    async def rank(self, developer_message: str, user_message: str) -> list[PotentialMatchIndex]:
        raise AssertionError("The exact CDE name must not call a model provider")


@pytest.mark.asyncio
async def test_recommendations_keep_cde_identity_when_node_names_change(flat_model: ReferenceModel) -> None:
    # Given an Auto-Model reference before and after renaming a node.
    diagnosis = CdeNodeRelation(NodeName("diagnosis"), 2)
    sample = CdeNodeRelation(NodeName("sample"), 7)
    before = AutoModelReference(flat_model, {"stage": (diagnosis, sample)})
    after = AutoModelReference(flat_model, {"stage": (sample, replace(diagnosis, node=NodeName("zzz")))})
    key = column_key_for_index(0)
    column = ProfiledColumn(ColumnIdentity(key, "stage"), build_column_profile(key, ["I", "II"]))
    recommender = CdeRecommendationAdapter(_UnexpectedRanker(), EmptyRecommendationCache())

    # When the recommendation pipeline evaluates both flat models.
    original = await recommender.recommend([column], before.model)
    renamed = await recommender.recommend([column], after.model)

    # Then each recommendation uses the same CDE-wide identity and target.
    assert renamed == original
    assert renamed.records[key].cde_key == "stage"
    assert renamed.records[key].cde_id == 42
