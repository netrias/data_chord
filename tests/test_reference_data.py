import pytest

from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel


def test_reference_model_requires_an_explicit_value_set_for_each_cde() -> None:
    # Given CDE metadata with no matching value-set entry.
    catalog = CdeCatalog.from_cdes([CDEInfo(None, "missing", None, CdeType.PASSTHROUGH)])

    # When the complete reference model is created, then it rejects partial data.
    with pytest.raises(ValueError, match="exactly match"):
        ReferenceModel(
            version=DataModelVersionReference("model", "1"),
            label="Model",
            catalog=catalog,
            pvs=CdePvCatalog.empty(),
        )


def test_reference_model_keeps_an_explicit_empty_value_set() -> None:
    # Given a pass-through CDE with an explicit empty value set.
    model = ReferenceModel(
        version=DataModelVersionReference("model", "1"),
        label="Model",
        catalog=CdeCatalog.from_cdes([CDEInfo(None, "empty", None, CdeType.PASSTHROUGH)]),
        pvs=CdePvCatalog.from_mapping({"empty": frozenset()}),
    )

    # Then the empty set is present rather than missing.
    assert model.pvs.has("empty")
    assert model.pvs.get("empty") == frozenset()


@pytest.mark.parametrize(
    ("cde_type", "values"),
    [
        (CdeType.PV, frozenset()),
        (CdeType.PASSTHROUGH, frozenset({"unexpected"})),
    ],
)
def test_reference_model_rejects_a_type_that_disagrees_with_values(
    cde_type: CdeType,
    values: frozenset[str],
) -> None:
    # Given metadata that disagrees with the explicit value set.
    with pytest.raises(ValueError, match="type does not match"):
        ReferenceModel(
            version=DataModelVersionReference("model", "1"),
            label="Model",
            catalog=CdeCatalog.from_cdes([CDEInfo(None, "field", None, cde_type)]),
            pvs=CdePvCatalog.from_mapping({"field": values}),
        )
