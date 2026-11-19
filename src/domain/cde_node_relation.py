"""Optional node identity and node-specific metadata for a flat CDE."""

from dataclasses import dataclass


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
