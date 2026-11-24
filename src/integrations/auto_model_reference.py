"""A flat reference model with optional metadata supplied by Auto-Model."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from src.domain.reference_data import ReferenceModel


@dataclass(frozen=True, order=True)
class NodeName:
    """A node's name within one reference model version."""

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("Reference node name is required")


@dataclass(frozen=True)
class CdeNodeRelation:
    """Metadata that applies to a CDE when it is used in one node."""

    node: NodeName
    node_cde_id: int
    source_node_cde_id: str | None = None
    description: str | None = None
    data_type: str | None = None
    is_key: bool | None = None
    is_required: bool | None = None


@dataclass(frozen=True)
class AutoModelReference:
    """Keep optional node metadata beside Data Chord's node-independent model."""

    model: ReferenceModel
    node_relations: Mapping[str, tuple[CdeNodeRelation, ...]] = field(default_factory=dict)
    declared_nodes: frozenset[NodeName] = frozenset()

    def __post_init__(self) -> None:
        relations_by_cde: dict[str, tuple[CdeNodeRelation, ...]] = {}
        for cde_key, relations in self.node_relations.items():
            if self.model.catalog.get(cde_key) is None:
                raise ValueError(f"Node relationships refer to an unknown CDE: {cde_key}")
            if len({relation.node for relation in relations}) != len(relations):
                raise ValueError(f"Reference CDE node relationships are invalid: {cde_key}")
            if relations:
                relations_by_cde[cde_key] = tuple(sorted(relations, key=lambda relation: relation.node))
        object.__setattr__(self, "node_relations", MappingProxyType(relations_by_cde))

    @property
    def nodes(self) -> frozenset[NodeName]:
        """Include nodes that have relationships and declared nodes with no CDEs."""
        return self.declared_nodes.union(
            relation.node for relations in self.node_relations.values() for relation in relations
        )
