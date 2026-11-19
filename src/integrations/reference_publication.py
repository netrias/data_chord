"""Guard publication formats until they can preserve optional node metadata."""

from collections.abc import Sequence

from src.domain.reference_data import ReferenceModel, ReferenceNodeMetadataUnsupportedError


def require_node_free_publication(models: Sequence[ReferenceModel]) -> None:
    """Reject an unsupported batch before any rows or files are written."""
    if any(model.nodes for model in models):
        raise ReferenceNodeMetadataUnsupportedError("Node metadata requires a node-aware reference publisher")
