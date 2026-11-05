import pytest

from src.domain.cde import CdeType
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_placements import CdePlacement, PlacedReferenceModel

VERSION = DataModelVersionReference("model", "1")


def _placed(
    placements: list[CdePlacement],
    values: dict[str, frozenset[str]],
    nodes: tuple[str, ...] | None = None,
) -> PlacedReferenceModel:
    return PlacedReferenceModel(
        version=VERSION,
        label="Model",
        nodes=nodes if nodes is not None else tuple({placement.node for placement in placements}),
        placements=tuple(placements),
        values=values,
    )


def test_placements_must_be_unique_per_node_and_cde_key() -> None:
    # Given the same CDE listed twice under one node.
    placements = [CdePlacement("diagnosis", "stage"), CdePlacement("diagnosis", "stage")]

    # When the placed model is created, then it rejects the duplicate.
    with pytest.raises(ValueError, match="unique per node"):
        _placed(placements, {"stage": frozenset()})


def test_placements_require_a_value_set_for_every_placement() -> None:
    # Given a placement with no matching value-set entry.
    with pytest.raises(ValueError, match="exactly match"):
        _placed([CdePlacement("diagnosis", "stage")], {})


def test_the_same_cde_key_may_appear_under_several_nodes() -> None:
    # Given one CDE key placed under two nodes.
    model = _placed(
        [CdePlacement("diagnosis", "stage", cde_id=2), CdePlacement("sample", "stage", cde_id=7)],
        {"stage": frozenset({"I"})},
    )

    # Then both placements survive, and the nodes are reported.
    assert len(model.placements) == 2
    assert model.nodes == ("diagnosis", "sample")


def test_collapse_merges_a_shared_key_into_one_cde() -> None:
    # Given one CDE key placed under two nodes with the same values.
    model = _placed(
        [CdePlacement("diagnosis", "stage", cde_id=2), CdePlacement("sample", "stage", cde_id=7)],
        {"stage": frozenset({"I", "II"})},
    )

    # When the model is collapsed, then the application sees a single CDE.
    collapsed = model.collapse()
    assert collapsed.catalog.keys() == ("stage",)
    assert collapsed.pvs.get("stage") == frozenset({"I", "II"})


def test_collapse_takes_the_cde_id_of_the_lowest_sorting_node() -> None:
    # Given placements supplied in an order that does not match node order.
    model = _placed(
        [CdePlacement("sample", "stage", cde_id=7), CdePlacement("diagnosis", "stage", cde_id=2)],
        {"stage": frozenset()},
    )

    # When the model is collapsed, then the surviving id does not depend on input order.
    assert model.collapse().catalog.keys() == ("stage",)
    cde = model.collapse().catalog.get("stage")
    assert cde is not None
    assert cde.cde_id == 2


def test_a_node_may_be_listed_without_holding_any_cde() -> None:
    # Given a model that lists a node no placement names.
    model = _placed(
        [CdePlacement("diagnosis", "stage")],
        {"stage": frozenset({"I"})},
        nodes=("diagnosis", "unused"),
    )

    # When the nodes are read, then the empty node is kept.
    assert model.nodes == ("diagnosis", "unused")


def test_placements_cannot_name_a_node_the_model_does_not_list() -> None:
    # Given a placement under a node missing from the model's node list.
    with pytest.raises(ValueError, match="does not list"):
        _placed([CdePlacement("diagnosis", "stage")], {"stage": frozenset()}, nodes=("sample",))


def test_value_sets_without_a_placement_are_rejected() -> None:
    # Given a value set for a CDE key that no node holds.
    with pytest.raises(ValueError, match="exactly match"):
        _placed(
            [CdePlacement("diagnosis", "stage")],
            {"stage": frozenset({"I"}), "unplaced": frozenset({"X"})},
        )


def test_collapse_derives_the_cde_type_from_the_merged_values() -> None:
    # Given one placement with values and one without.
    model = _placed(
        [CdePlacement("diagnosis", "stage"), CdePlacement("diagnosis", "note")],
        {"stage": frozenset({"I"}), "note": frozenset()},
    )

    # When the model is collapsed, then each CDE's type follows its values.
    collapsed = model.collapse()
    stage = collapsed.catalog.get("stage")
    note = collapsed.catalog.get("note")
    assert stage is not None and stage.cde_type is CdeType.PV
    assert note is not None and note.cde_type is CdeType.PASSTHROUGH
