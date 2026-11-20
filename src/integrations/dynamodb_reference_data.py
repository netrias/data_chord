"""Read flat schema-2 CDEs and their node relationships from DynamoDB."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from threading import Lock
from typing import Protocol, cast

from boto3.dynamodb.types import Binary

from src.domain.cde import DataModelSummary, DataModelVersionInfo
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_cdes import FlatReferenceModel, NodeCdeRelation, ReferenceCde
from src.domain.reference_data import (
    ReferenceDataCorruptError,
    ReferenceDataUnavailableError,
    ReferenceModel,
    ReferenceModelNotFoundError,
)

SCHEMA_VERSION = 2
MAX_UNCOMPRESSED_CHUNK_BYTES = 300 * 1024
_CATALOG_PK = "CATALOG"
_META_SK = "META"
_IMPORT_SK = "IMPORT"
_MAX_CACHED_MODELS = 4


class DynamoTable(Protocol):
    def put_item(self, *, Item: Mapping[str, object], ConditionExpression: str | None = None) -> object: ...

    def get_item(self, *, Key: Mapping[str, object], ConsistentRead: bool) -> Mapping[str, object]: ...

    def query(self, **kwargs: object) -> Mapping[str, object]: ...


class DynamoResource(Protocol):
    def Table(self, table_name: str) -> DynamoTable: ...  # noqa: N802 - boto3 framework name


@dataclass(frozen=True)
class _ValueRows:
    metadata: Mapping[str, object]
    chunks: tuple[Mapping[str, object], ...]


class DynamoDbReferenceDataRepository:
    """Read-only runtime adapter. Every load validates the full model."""

    def __init__(self, table: DynamoTable) -> None:
        self._table = table
        self._models: OrderedDict[DataModelVersionReference, ReferenceModel] = OrderedDict()
        self._models_lock = Lock()
        self._model_load_lock = Lock()

    def list_models(self) -> tuple[DataModelSummary, ...]:
        items = self._query_partition(_CATALOG_PK)
        if not items:
            return ()
        return _summaries_from_catalog(items)

    def load_model(self, version: DataModelVersionReference) -> ReferenceModel:
        with self._models_lock:
            cached = self._models.get(version)
            if cached is not None:
                self._models.move_to_end(version)
                return cached

        # A model and its DynamoDB rows can both be large. Serialize cache
        # misses so concurrent requests cannot build several full models.
        with self._model_load_lock:
            with self._models_lock:
                cached = self._models.get(version)
                if cached is not None:
                    self._models.move_to_end(version)
                    return cached
            items = self._query_partition(_model_partition(version))
            if not items:
                raise ReferenceModelNotFoundError(
                    f"Reference model is not published: {version.data_model_key}/{version.external_version_number}"
                )
            try:
                model = _flat_from_items(version, items).collapse()
            except ReferenceDataCorruptError:
                raise
            except Exception as exc:
                raise ReferenceDataCorruptError("Reference model is malformed") from exc
            with self._models_lock:
                self._models[version] = model
                self._models.move_to_end(version)
                if len(self._models) > _MAX_CACHED_MODELS:
                    self._models.popitem(last=False)
                return model

    def load_cdes(self, version: DataModelVersionReference) -> FlatReferenceModel:
        """Read the complete flat CDE form, including node-specific relationships."""
        items = self._query_partition(_model_partition(version))
        if not items:
            raise ReferenceModelNotFoundError(
                f"Reference model is not published: {version.data_model_key}/{version.external_version_number}"
            )
        try:
            return _flat_from_items(version, items)
        except ReferenceDataCorruptError:
            raise
        except Exception as exc:
            raise ReferenceDataCorruptError("Reference model is malformed") from exc

    def _query_partition(self, pk: str) -> list[Mapping[str, object]]:
        items: list[Mapping[str, object]] = []
        exclusive_start_key: object | None = None
        try:
            while True:
                arguments: dict[str, object] = {
                    "KeyConditionExpression": "pk = :pk",
                    "ExpressionAttributeValues": {":pk": pk},
                    "ConsistentRead": True,
                }
                if exclusive_start_key is not None:
                    arguments["ExclusiveStartKey"] = exclusive_start_key
                response = self._table.query(**arguments)
                page = response.get("Items", [])
                if not isinstance(page, list):
                    raise ReferenceDataCorruptError("DynamoDB query returned invalid items")
                items.extend(cast(list[Mapping[str, object]], page))
                exclusive_start_key = response.get("LastEvaluatedKey")
                if exclusive_start_key is None:
                    return items
        except ReferenceDataCorruptError:
            raise
        except Exception as exc:
            raise ReferenceDataUnavailableError("Reference data is unavailable") from exc


def _summaries_from_catalog(items: list[Mapping[str, object]]) -> tuple[DataModelSummary, ...]:
    """Validate a complete catalog and return its model summaries."""
    by_sk = {_required_string(item, "sk"): item for item in items}
    if len(by_sk) != len(items):
        raise ReferenceDataCorruptError("Reference catalog contains duplicate rows")
    markers = _catalog_markers(by_sk)
    if markers is None:
        return ()
    meta, import_seal = markers
    labels: dict[str, str] = {}
    versions_by_model: dict[str, set[str]] = {}
    catalog_items: list[Mapping[str, object]] = []
    for item in by_sk.values():
        _validate_schema(item)
        key = _required_string(item, "data_model_key")
        label = _required_string(item, "label")
        version = _required_string(item, "external_version_number")
        if _required_string(item, "sk") != _catalog_sort_key(key, version):
            raise ReferenceDataCorruptError("Reference catalog identity does not match its key")
        existing_label = labels.setdefault(key, label)
        if existing_label != label:
            raise ReferenceDataCorruptError(f"Reference catalog labels disagree: {key}")
        versions_by_model.setdefault(key, set()).add(version)
        catalog_items.append(item)
    if _required_int(meta, "model_count") != len(catalog_items):
        raise ReferenceDataCorruptError("Reference catalog model count is invalid")
    if _required_string(meta, "digest") != _digest(_catalog_payload(catalog_items)):
        raise ReferenceDataCorruptError("Reference catalog digest is invalid")
    source_digest = _required_sha256(meta, "source_digest")
    if (
        _required_sha256(import_seal, "source_digest") != source_digest
        or _required_string(import_seal, "digest") != _required_string(meta, "digest")
        or _required_int(import_seal, "model_count") != _required_int(meta, "model_count")
    ):
        raise ReferenceDataCorruptError("Reference catalog import seal does not match its catalog")
    return tuple(
        DataModelSummary(
            data_model_key=key,
            label=labels[key],
            versions=[DataModelVersionInfo(version) for version in sorted(versions)],
        )
        for key, versions in sorted(versions_by_model.items())
    )


def _catalog_markers(
    by_sk: dict[str, Mapping[str, object]],
) -> tuple[Mapping[str, object], Mapping[str, object]] | None:
    meta = by_sk.pop(_META_SK, None)
    import_seal = by_sk.pop(_IMPORT_SK, None)
    if meta is None:
        if import_seal is None or by_sk:
            raise ReferenceDataCorruptError("Reference catalog is not completely published")
        _validate_schema(import_seal)
        _required_sha256(import_seal, "source_digest")
        _required_string(import_seal, "digest")
        _required_int(import_seal, "model_count")
        return None
    if import_seal is None:
        raise ReferenceDataCorruptError("Reference catalog has no immutable import seal")
    _validate_schema(meta)
    _validate_schema(import_seal)
    return meta, import_seal


def _catalog_payload(items: Sequence[Mapping[str, object]]) -> list[Mapping[str, str]]:
    return sorted(
        [
            {
                "data_model_key": _required_string(item, "data_model_key"),
                "external_version_number": _required_string(item, "external_version_number"),
                "label": _required_string(item, "label"),
            }
            for item in items
        ],
        key=lambda item: (item["data_model_key"], item["external_version_number"]),
    )


def _flat_from_items(
    requested: DataModelVersionReference,
    items: list[Mapping[str, object]],
) -> FlatReferenceModel:
    """Decode flat rows without selecting a node's metadata for a CDE-wide field."""
    by_sk = _rows_by_sort_key(items)
    meta, import_seal = _model_markers(by_sk, requested)
    nodes = _flat_nodes(meta)
    cde_rows = _flat_cde_rows(by_sk)
    cdes = tuple(_flat_cde(requested, cde_key, rows) for cde_key, rows in sorted(cde_rows.items()))
    try:
        model = FlatReferenceModel(
            version=requested,
            label=_required_string(meta, "label"),
            nodes=nodes,
            cdes=cdes,
        )
    except ValueError as exc:
        raise ReferenceDataCorruptError(str(exc)) from exc
    expected = {
        "node_count": len(model.nodes),
        "node_cde_count": sum(len(cde.node_relations) for cde in model.cdes),
        "cde_key_count": len(model.cdes),
        "value_count": sum(len(cde.values) for cde in model.cdes),
    }
    for field, count in expected.items():
        if _required_int(meta, field) != count:
            raise ReferenceDataCorruptError(f"Reference {field.replace('_', ' ')} is invalid")
    if _digest(_canonical_flat_payload(model)) != _required_string(meta, "digest"):
        raise ReferenceDataCorruptError("Reference model digest is invalid")
    if _required_string(import_seal, "digest") != _required_string(meta, "digest"):
        raise ReferenceDataCorruptError("Reference import seal does not match its model")
    return model


def _flat_nodes(meta: Mapping[str, object]) -> tuple[str, ...]:
    raw = meta.get("nodes")
    if not isinstance(raw, list) or any(not isinstance(node, str) or not node.strip() for node in raw):
        raise ReferenceDataCorruptError("Reference model nodes are invalid")
    nodes = cast(list[str], raw)
    if not nodes or len(nodes) != len(set(nodes)):
        raise ReferenceDataCorruptError("Reference model nodes are invalid")
    return tuple(nodes)


def _flat_cde_rows(by_sk: Mapping[str, Mapping[str, object]]) -> dict[str, _ValueRows]:
    """Group the CDE metadata and value chunks; reject all node-level row keys."""
    metadata: dict[str, Mapping[str, object]] = {}
    chunks: dict[str, list[Mapping[str, object]]] = {}
    for sk, item in by_sk.items():
        _validate_schema(item)
        cde_key = _required_string(item, "cde_key")
        prefix = _values_prefix(cde_key)
        if sk == f"{prefix}#META":
            metadata[cde_key] = item
        elif sk.startswith(f"{prefix}#VALUES#"):
            chunks.setdefault(cde_key, []).append(item)
        else:
            raise ReferenceDataCorruptError(f"Unknown reference row: {sk}")
    if set(chunks) - set(metadata):
        raise ReferenceDataCorruptError("Reference values exist for an unknown CDE")
    return {key: _ValueRows(item, tuple(chunks.get(key, ()))) for key, item in metadata.items()}


def _flat_cde(
    version: DataModelVersionReference,
    cde_key: str,
    rows: _ValueRows,
) -> ReferenceCde:
    metadata = rows.metadata
    cde_id = _required_int(metadata, "cde_id")
    if cde_id != _generated_cde_id(version, cde_key):
        raise ReferenceDataCorruptError(f"Reference CDE id is invalid: {cde_key}")
    raw_relations = metadata.get("node_relations")
    if not isinstance(raw_relations, list) or not raw_relations:
        raise ReferenceDataCorruptError(f"Reference node relationships are invalid: {cde_key}")
    relations = tuple(_flat_relation(version, cde_key, raw) for raw in cast(list[object], raw_relations))
    return ReferenceCde(
        cde_key=cde_key,
        cde_id=cde_id,
        values=frozenset(_values_for_cde(cde_key, metadata, rows.chunks)),
        node_relations=relations,
    )


def _flat_relation(
    version: DataModelVersionReference,
    cde_key: str,
    raw: object,
) -> NodeCdeRelation:
    if not isinstance(raw, Mapping):
        raise ReferenceDataCorruptError(f"Reference node relationship is invalid: {cde_key}")
    node_name = _required_string(raw, "node_name")
    node_cde_id = _required_int(raw, "node_cde_id")
    if node_cde_id != _generated_cde_id(version, cde_key, node_name):
        raise ReferenceDataCorruptError(f"Reference node CDE id is invalid: {node_name}/{cde_key}")
    source_node_cde_id = _required_string(raw, "source_node_cde_id")
    if not source_node_cde_id.strip():
        raise ReferenceDataCorruptError(f"Reference source node CDE id is invalid: {node_name}/{cde_key}")
    description = _optional_string(raw, "description", cde_key)
    data_type = _required_string(raw, "data_type")
    if not data_type.strip():
        raise ReferenceDataCorruptError(f"Reference data type is invalid: {node_name}/{cde_key}")
    is_key, is_required = raw.get("is_key"), raw.get("is_required")
    if not isinstance(is_key, bool) or not isinstance(is_required, bool):
        raise ReferenceDataCorruptError(f"Reference node flags are invalid: {node_name}/{cde_key}")
    return NodeCdeRelation(node_name, node_cde_id, source_node_cde_id, description, data_type, is_key, is_required)


def _canonical_flat_payload(model: FlatReferenceModel) -> Mapping[str, object]:
    """Canonical flat model data used for the publication digest."""
    return {
        "schema_version": SCHEMA_VERSION,
        "data_model_key": model.version.data_model_key,
        "external_version_number": model.version.external_version_number,
        "label": model.label,
        "nodes": list(model.nodes),
        "cdes": [
            {
                "cde_key": cde.cde_key,
                "cde_id": cde.cde_id,
                "values": sorted(cde.values),
                "node_relations": [
                    {
                        "node_name": relation.node_name,
                        "node_cde_id": relation.node_cde_id,
                        "source_node_cde_id": relation.source_node_cde_id,
                        "description": relation.description,
                        "data_type": relation.data_type,
                        "is_key": relation.is_key,
                        "is_required": relation.is_required,
                    }
                    for relation in cde.node_relations
                ],
            }
            for cde in model.cdes
        ],
    }


def _generated_cde_id(version: DataModelVersionReference, cde_key: str, node_name: str | None = None) -> int:
    """Generate a 63-bit ID from CDE identity, adding the node only for a relationship."""
    parts = ["CDE", version.data_model_key, version.external_version_number]
    if node_name is not None:
        parts.append(node_name)
    parts.append(cde_key)
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


def _rows_by_sort_key(items: list[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    by_sk: dict[str, Mapping[str, object]] = {}
    for item in items:
        sk = _required_string(item, "sk")
        if sk in by_sk:
            raise ReferenceDataCorruptError(f"Duplicate reference row: {sk}")
        by_sk[sk] = item
    return by_sk


def _model_markers(
    by_sk: dict[str, Mapping[str, object]],
    requested: DataModelVersionReference,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    meta = by_sk.pop(_META_SK, None)
    if meta is None:
        raise ReferenceDataCorruptError("Reference model has no complete marker")
    import_seal = by_sk.pop(_IMPORT_SK, None)
    if import_seal is None:
        raise ReferenceDataCorruptError("Reference model has no immutable import seal")
    _validate_schema(meta)
    _validate_schema(import_seal)
    for marker, message in ((meta, "model"), (import_seal, "import seal")):
        if (
            _required_string(marker, "data_model_key") != requested.data_model_key
            or _required_string(marker, "external_version_number") != requested.external_version_number
        ):
            raise ReferenceDataCorruptError(f"Reference {message} identity does not match its key")
    return meta, import_seal


def _values_for_cde(
    cde_key: str,
    metadata: Mapping[str, object],
    chunks: tuple[Mapping[str, object], ...],
) -> list[str]:
    ordered = sorted(chunks, key=lambda item: _required_int(item, "chunk_index"))
    indexes = [_required_int(item, "chunk_index") for item in ordered]
    if indexes != list(range(_required_int(metadata, "chunk_count"))):
        raise ReferenceDataCorruptError(f"Reference value chunks are incomplete: {cde_key}")
    values: list[str] = []
    for chunk in ordered:
        chunk_index = _required_int(chunk, "chunk_index")
        if _required_string(chunk, "sk") != f"{_values_prefix(cde_key)}#VALUES#{chunk_index:06d}":
            raise ReferenceDataCorruptError(f"Reference value chunk identity is invalid: {cde_key}")
        values.extend(_gunzip_values(chunk.get("payload"), _required_int(chunk, "uncompressed_size")))
    if len(values) != _required_int(metadata, "value_count") or len(values) != len(set(values)):
        raise ReferenceDataCorruptError(f"Reference value count is invalid: {cde_key}")
    if _digest(values) != _required_string(metadata, "digest"):
        raise ReferenceDataCorruptError(f"Reference value digest is invalid: {cde_key}")
    return values


def _gunzip_values(payload: object, expected_size: int) -> list[str]:
    if isinstance(payload, bytes):
        encoded = payload
    elif isinstance(payload, Binary):
        encoded = cast(bytes, payload.value)
    else:
        raise ReferenceDataCorruptError("Reference value payload is not binary")
    if expected_size > MAX_UNCOMPRESSED_CHUNK_BYTES:
        raise ReferenceDataCorruptError("Reference value payload is too large")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(encoded)) as compressed:
            raw = compressed.read(expected_size + 1)
        if len(raw) != expected_size:
            raise ReferenceDataCorruptError("Reference value payload size is invalid")
        decoded = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReferenceDataCorruptError("Reference value payload is corrupt") from exc
    if not isinstance(decoded, list) or any(not isinstance(value, str) for value in decoded):
        raise ReferenceDataCorruptError("Reference value payload is invalid")
    return cast(list[str], decoded)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _model_partition(version: DataModelVersionReference) -> str:
    return f"MODEL#{_encode(version.data_model_key)}#VERSION#{_encode(version.external_version_number)}"


def _values_prefix(cde_key: str) -> str:
    """Values sit outside the node prefix because they are stored once per CDE key."""
    return f"CDE#{_encode(cde_key)}"


def _catalog_sort_key(data_model_key: str, external_version_number: str) -> str:
    return f"MODEL#{_encode(data_model_key)}#VERSION#{_encode(external_version_number)}"


def _validate_schema(item: Mapping[str, object]) -> None:
    if _required_int(item, "schema_version") != SCHEMA_VERSION:
        raise ReferenceDataCorruptError("Reference schema version is unsupported")


def _required_string(item: Mapping[str, object], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value:
        raise ReferenceDataCorruptError(f"Reference field is invalid: {field}")
    return value


def _optional_string(item: Mapping[str, object], field: str, label: str) -> str | None:
    value = item.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReferenceDataCorruptError(f"Reference CDE {field} is invalid: {label}")
    return value


def _required_sha256(item: Mapping[str, object], field: str) -> str:
    value = _required_string(item, field)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ReferenceDataCorruptError(f"Reference field is not a SHA-256 digest: {field}")
    return value


def _required_int(item: Mapping[str, object], field: str) -> int:
    value = item.get(field)
    if isinstance(value, bool):
        raise ReferenceDataCorruptError(f"Reference field is invalid: {field}")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, Decimal) and value.is_finite() and value >= 0 and value == value.to_integral_value():
        return int(value)
    raise ReferenceDataCorruptError(f"Reference field is invalid: {field}")


__all__ = [
    "DynamoDbReferenceDataRepository",
    "DynamoResource",
    "DynamoTable",
    "MAX_UNCOMPRESSED_CHUNK_BYTES",
    "SCHEMA_VERSION",
]
