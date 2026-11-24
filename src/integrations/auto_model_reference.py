"""Auto-Model CDEs and their optional node relationships."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel


@dataclass(frozen=True)
class NodeCdeRelation:
    """Metadata belonging to one node's use of a CDE."""

    node_name: str
    node_cde_id: int
    source_node_cde_id: str
    description: str | None
    data_type: str
    is_key: bool
    is_required: bool


@dataclass(frozen=True)
class ReferenceCde:
    """One CDE identity and value set, shared by all its node relationships."""

    cde_key: str
    cde_id: int
    values: frozenset[str]
    node_relations: tuple[NodeCdeRelation, ...] = ()


@dataclass(frozen=True)
class FlatReferenceModel:
    """One model version with flat CDE records and optional node metadata."""

    version: DataModelVersionReference
    label: str
    nodes: tuple[str, ...]
    cdes: tuple[ReferenceCde, ...]

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("Reference model label is required")
        if any(not node.strip() for node in self.nodes) or len(set(self.nodes)) != len(self.nodes):
            raise ValueError("Reference model nodes must be nonempty and unique")
        keys = [cde.cde_key for cde in self.cdes]
        if any(not key.strip() for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("Reference model CDE keys must be nonempty and unique")
        ids = [cde.cde_id for cde in self.cdes]
        if len(set(ids)) != len(ids):
            raise ValueError("Reference model CDE ids must be unique")
        relation_ids = [relation.node_cde_id for cde in self.cdes for relation in cde.node_relations]
        if len(set(relation_ids)) != len(relation_ids):
            raise ValueError("Reference node CDE ids must be unique")
        allowed_nodes = frozenset(self.nodes)
        for cde in self.cdes:
            _validate_cde_relationships(cde, allowed_nodes)
        object.__setattr__(self, "nodes", tuple(sorted(self.nodes)))
        object.__setattr__(
            self,
            "cdes",
            tuple(
                sorted(
                    (
                        ReferenceCde(
                            cde_key=cde.cde_key,
                            cde_id=cde.cde_id,
                            values=cde.values,
                            node_relations=tuple(sorted(cde.node_relations, key=lambda item: item.node_name)),
                        )
                        for cde in self.cdes
                    ),
                    key=lambda item: item.cde_key,
                )
            ),
        )

    def collapse(self) -> ReferenceModel:
        """Return only CDE-wide fields to callers without node context."""
        return ReferenceModel(
            version=self.version,
            label=self.label,
            catalog=CdeCatalog.from_cdes(
                CDEInfo(cde.cde_id, cde.cde_key, None, CdeType.PV if cde.values else CdeType.PASSTHROUGH)
                for cde in self.cdes
            ),
            pvs=CdePvCatalog.from_mapping({cde.cde_key: cde.values for cde in self.cdes}),
        )


def _validate_cde_relationships(cde: ReferenceCde, allowed_nodes: frozenset[str]) -> None:
    if any(not value.strip() for value in cde.values):
        raise ValueError(f"Reference CDE values must be nonempty: {cde.cde_key}")
    names = [relation.node_name for relation in cde.node_relations]
    if len(set(names)) != len(names) or not set(names) <= allowed_nodes:
        raise ValueError(f"Reference CDE node relationships are invalid: {cde.cde_key}")


__all__ = ["FlatReferenceModel", "NodeCdeRelation", "ReferenceCde"]
