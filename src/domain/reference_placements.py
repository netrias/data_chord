"""Reference data as the source publishes it: one CDE per node that holds it.

Source data models place the same CDE under several nodes, and the placements
    can disagree on ``data_type``, ``is_key`` and ``is_required``. ``ReferenceModel`` keys CDEs
by key alone, so it cannot hold that shape. Storage keeps the placed form so the
stored copy stays faithful to the source and its counts stay checkable against
the publishing manifest; the application reads the collapsed form.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel

PlacementKey = tuple[str, str]


@dataclass(frozen=True)
class CdePlacement:
    """One CDE as it appears under one node."""

    node: str
    cde_key: str
    cde_id: int | None = None
    description: str | None = None
    data_type: str | None = None
    is_required: bool | None = None
    is_key: bool | None = None

    @property
    def key(self) -> PlacementKey:
        return (self.node, self.cde_key)


@dataclass(frozen=True)
class PlacedReferenceModel:
    """Every node-CDE placement for one model version, with its value sets.

    Placements are held per node-CDE pair because nodes disagree: a CDE shared
    by several nodes can carry a different ``data_type``, ``is_key`` or ``is_required``
    under each one, so one record per CDE key would have to discard the
    difference.

    Values are held per CDE key because that disagreement does not extend to
    them. Permissible values are published against a model and a property with
    no node in the request, so every node holding a property reads one list.

    Nodes are listed in their own field rather than derived from the
    placements, because a model can define a node that holds no CDEs at all.
    """

    version: DataModelVersionReference
    label: str
    nodes: tuple[str, ...]
    placements: tuple[CdePlacement, ...]
    values: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("Reference model label is required")
        if any(not node.strip() for node in self.nodes):
            raise ValueError("Reference model node is required")
        if len(self.nodes) != len(set(self.nodes)):
            raise ValueError("Reference model nodes must be unique")
        for placement in self.placements:
            if not placement.node.strip():
                raise ValueError("Reference placement node is required")
            if not placement.cde_key.strip():
                raise ValueError("Reference placement CDE key is required")
        keys = [placement.key for placement in self.placements]
        if len(keys) != len(set(keys)):
            raise ValueError("Reference placements must be unique per node and CDE key")
        if {placement.cde_key for placement in self.placements} != set(self.values.keys()):
            raise ValueError("Reference placement and value-set keys must exactly match")
        if not {placement.node for placement in self.placements} <= set(self.nodes):
            raise ValueError("Reference placements name a node the model does not list")
        object.__setattr__(self, "nodes", tuple(sorted(self.nodes)))
        object.__setattr__(self, "placements", tuple(sorted(self.placements, key=lambda item: item.key)))
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @property
    def cde_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.values.keys()))

    def collapse(self) -> ReferenceModel:
        """Merge placements of the same CDE key into the application's flat view.

        The surviving ``cde_id`` comes from the lowest-sorting node. Any choice
        is arbitrary, but ``cde_id`` is published through the programmatic API
        and persisted into mapping manifests, so it has to stay stable across
        reloads rather than follow whatever order rows arrive in.
        """
        merged: dict[str, CdePlacement] = {}
        for placement in self.placements:
            merged.setdefault(placement.cde_key, placement)
        cdes = [
            CDEInfo(
                cde_id=placement.cde_id,
                cde_key=cde_key,
                description=placement.description,
                cde_type=CdeType.PV if self.values[cde_key] else CdeType.PASSTHROUGH,
            )
            for cde_key, placement in sorted(merged.items())
        ]
        return ReferenceModel(
            version=self.version,
            label=self.label,
            catalog=CdeCatalog.from_cdes(cdes),
            pvs=CdePvCatalog.from_mapping(dict(self.values)),
        )


__all__ = ["CdePlacement", "PlacedReferenceModel", "PlacementKey"]
