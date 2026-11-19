"""Reference model and optional node metadata supplied by Auto-Model."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from src.domain.reference_data import ReferenceModel


@dataclass(frozen=True, order=True)
class NodeName:
    """A node's name within one reference-model version."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("Reference node name is required")


@dataclass(frozen=True)
class CdeNodeRelation:
    """Metadata belonging to one CDE's relationship with a node.

    The source relationship ID is independent of the CDE-wide ID. Descriptions,
    data-type labels and flags describe this relationship only; they do not
    replace the CDE-wide description, approved values or harmonization type.
    """

    node: NodeName
    source_node_cde_id: int | None = None
    description: str | None = None
    data_type: str | None = None
    is_required: bool | None = None
    is_key: bool | None = None

    def __post_init__(self) -> None:
        source_id = self.source_node_cde_id
        if source_id is not None and (isinstance(source_id, bool) or not isinstance(source_id, int) or source_id < 0):
            raise ValueError("Source node-CDE ID must be a nonnegative integer")


@dataclass(frozen=True)
class AutoModelReference:
    """Keep Auto-Model metadata next to the flat model passed to Data Chord."""

    model: ReferenceModel
    node_relations: Mapping[str, tuple[CdeNodeRelation, ...]] = field(default_factory=dict)
    declared_nodes: frozenset[NodeName] = frozenset()

    def __post_init__(self) -> None:
        relations_by_cde: dict[str, tuple[CdeNodeRelation, ...]] = {}
        for cde_key, relations in self.node_relations.items():
            if self.model.catalog.get(cde_key) is None:
                raise ValueError(f"Node relationships refer to an unknown CDE: {cde_key}")
            if len({relation.node for relation in relations}) != len(relations):
                raise ValueError(f"CDE node relationships must be unique per node: {cde_key}")
            if relations:
                relations_by_cde[cde_key] = tuple(sorted(relations, key=lambda relation: relation.node))
        object.__setattr__(self, "node_relations", MappingProxyType(relations_by_cde))
        object.__setattr__(self, "declared_nodes", frozenset(self.declared_nodes))

    @property
    def nodes(self) -> frozenset[NodeName]:
        """Include related nodes and declared nodes with no CDEs."""
        return self.declared_nodes.union(
            relation.node for relations in self.node_relations.values() for relation in relations
        )
