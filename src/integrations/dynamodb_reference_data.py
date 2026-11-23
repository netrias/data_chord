"""Read flat schema-3 CDEs and their node relationships from DynamoDB."""

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

from src.domain.cde import CDEInfo, CdeType, DataModelSummary, DataModelVersionInfo
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import (
    ReferenceDataCorruptError,
    ReferenceDataUnavailableError,
    ReferenceModel,
    ReferenceModelNotFoundError,
)
from src.integrations.auto_model_reference import AutoModelReference, CdeNodeRelation, NodeName

SCHEMA_VERSION = 3
MAX_UNCOMPRESSED_CHUNK_BYTES = 300 * 1024
_CATALOG_PK = "CATALOG"
_META_SK = "META"
_IMPORT_SK = "IMPORT"
_MAX_CACHED_MODELS = 4


class DynamoTable(Protocol):
    def query(self, **kwargs: object) -> Mapping[str, object]: ...


class DynamoResource(Protocol):
    def Table(self, _table_name: str) -> DynamoTable: ...  # noqa: N802 - boto3 framework name


@dataclass(frozen=True)
class _ValueRows:
    metadata: Mapping[str, object]
    chunks: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class _DecodedCde:
    info: CDEInfo
    values: frozenset[str]
    relations: tuple[CdeNodeRelation, ...]


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
        cached = self._cached_model(version)
        if cached is not None:
            return cached

        # Bound peak memory by serializing loads of uncached model partitions.
        with self._model_load_lock:
            cached = self._cached_model(version)
            if cached is not None:
                return cached
            model = self.load_cdes(version).model
            self._cache_model(version, model)
            return model

    def _cached_model(self, version: DataModelVersionReference) -> ReferenceModel | None:
        with self._models_lock:
            cached = self._models.get(version)
            if cached is not None:
                self._models.move_to_end(version)
            return cached

    def _cache_model(self, version: DataModelVersionReference, model: ReferenceModel) -> None:
        with self._models_lock:
            self._models[version] = model
            if len(self._models) > _MAX_CACHED_MODELS:
                self._models.popitem(last=False)

    def load_cdes(self, version: DataModelVersionReference) -> AutoModelReference:
        """Read the verified model with its optional relationship metadata."""
        items = self._query_partition(_model_partition(version))
        if not items:
            raise ReferenceModelNotFoundError(
                f"Reference model is not published: {version.data_model_key}/{version.external_version_number}"
            )
        try:
            return _flat_from_items(version, items)
        except ReferenceDataCorruptError:
            raise
        except ValueError as exc:
            raise ReferenceDataCorruptError(str(exc)) from exc

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
    entries = _catalog_payload(list(by_sk.values()))
    _validate_catalog_seal(meta, import_seal, entries)
    return _catalog_summaries(entries)


def _validate_catalog_seal(
    meta: Mapping[str, object],
    import_seal: Mapping[str, object],
    entries: list[Mapping[str, str]],
) -> None:
    digest = _required_string(meta, "digest")
    if _required_int(meta, "model_count") != len(entries):
        raise ReferenceDataCorruptError("Reference catalog model count is invalid")
    if digest != _digest(entries):
        raise ReferenceDataCorruptError("Reference catalog digest is invalid")
    if (
        _required_sha256(import_seal, "source_digest") != _required_sha256(meta, "source_digest")
        or _required_string(import_seal, "digest") != digest
        or _required_int(import_seal, "model_count") != len(entries)
    ):
        raise ReferenceDataCorruptError("Reference catalog import seal does not match its catalog")


def _catalog_summaries(entries: list[Mapping[str, str]]) -> tuple[DataModelSummary, ...]:
    labels: dict[str, str] = {}
    versions_by_model: dict[str, list[DataModelVersionInfo]] = {}
    for entry in entries:
        key = entry["data_model_key"]
        label = entry["label"]
        if labels.setdefault(key, label) != label:
            raise ReferenceDataCorruptError(f"Reference catalog labels disagree: {key}")
        versions_by_model.setdefault(key, []).append(DataModelVersionInfo(entry["external_version_number"]))
    return tuple(DataModelSummary(key, labels[key], versions) for key, versions in versions_by_model.items())


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
    entries: list[Mapping[str, str]] = []
    for item in items:
        _validate_schema(item)
        key = _required_string(item, "data_model_key")
        version = _required_string(item, "external_version_number")
        if _required_string(item, "sk") != _catalog_sort_key(key, version):
            raise ReferenceDataCorruptError("Reference catalog identity does not match its key")
        entries.append({
            "data_model_key": key,
            "external_version_number": version,
            "label": _required_string(item, "label"),
        })
    return sorted(entries, key=lambda item: (item["data_model_key"], item["external_version_number"]))


def _flat_from_items(
    requested: DataModelVersionReference,
    items: list[Mapping[str, object]],
) -> AutoModelReference:
    """Decode and verify the model before exposing it to callers."""
    by_sk = _rows_by_sort_key(items)
    meta, import_seal = _model_markers(by_sk, requested)
    cde_rows = _flat_cde_rows(by_sk)
    cdes = [_flat_cde(requested, key, rows) for key, rows in sorted(cde_rows.items())]
    reference = _build_reference(requested, meta, cdes)
    _validate_model_seal(reference, meta, import_seal)
    return reference


def _build_reference(
    requested: DataModelVersionReference, meta: Mapping[str, object], cdes: list[_DecodedCde],
) -> AutoModelReference:
    if len({cde.info.cde_id for cde in cdes}) != len(cdes):
        raise ReferenceDataCorruptError("Reference model CDE ids must be unique")
    model = ReferenceModel(
        version=requested,
        label=_required_string(meta, "label"),
        catalog=CdeCatalog.from_cdes(cde.info for cde in cdes),
        pvs=CdePvCatalog.from_mapping({cde.info.cde_key: cde.values for cde in cdes}),
        publication_digest=_required_sha256(meta, "digest"),
    )
    declared_nodes = _flat_nodes(meta)
    relations = {cde.info.cde_key: cde.relations for cde in cdes}
    _validate_model_relationships(relations, declared_nodes)
    return AutoModelReference(model, relations, declared_nodes or frozenset())


def _validate_model_relationships(
    by_cde: Mapping[str, tuple[CdeNodeRelation, ...]], declared_nodes: frozenset[NodeName] | None,
) -> None:
    seen: set[int] = set()
    for cde_key, relations in by_cde.items():
        for relation in relations:
            if declared_nodes is not None and relation.node not in declared_nodes:
                raise ReferenceDataCorruptError(f"Reference CDE node relationships are invalid: {cde_key}")
            if relation.node_cde_id in seen:
                raise ReferenceDataCorruptError("Reference node CDE ids must be unique")
            seen.add(relation.node_cde_id)


def _validate_model_seal(
    reference: AutoModelReference, meta: Mapping[str, object], import_seal: Mapping[str, object],
) -> None:
    expected = {
        "cde_count": len(reference.model.catalog),
        "value_count": sum(map(len, reference.model.pvs.values.values())),
    }
    if "node_count" in meta:
        expected["node_count"] = len(reference.nodes)
    if "node_cde_count" in meta:
        expected["node_cde_count"] = sum(map(len, reference.node_relations.values()))
    for field, count in expected.items():
        if _required_int(meta, field) != count:
            raise ReferenceDataCorruptError(f"Reference {field.replace('_', ' ')} is invalid")
    digest = reference.model.publication_digest
    if _digest(_canonical_flat_payload(reference)) != digest:
        raise ReferenceDataCorruptError("Reference model digest is invalid")
    if _required_string(import_seal, "digest") != digest:
        raise ReferenceDataCorruptError("Reference import seal does not match its model")


def _flat_nodes(meta: Mapping[str, object]) -> frozenset[NodeName] | None:
    if "nodes" not in meta:
        return None
    raw = meta["nodes"]
    if not isinstance(raw, list) or any(not isinstance(node, str) or not node.strip() for node in raw):
        raise ReferenceDataCorruptError("Reference model nodes are invalid")
    nodes = frozenset(NodeName(node) for node in cast(list[str], raw))
    if len(nodes) != len(raw):
        raise ReferenceDataCorruptError("Reference model nodes must be unique")
    return nodes


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
) -> _DecodedCde:
    metadata = rows.metadata
    cde_id = _required_int(metadata, "cde_id")
    if cde_id != _generated_cde_id(version, cde_key):
        raise ReferenceDataCorruptError(f"Reference CDE id is invalid: {cde_key}")
    raw_relations = metadata.get("node_relations", [])
    if not isinstance(raw_relations, list):
        raise ReferenceDataCorruptError(f"Reference node relationships are invalid: {cde_key}")
    values = frozenset(_values_for_cde(cde_key, metadata, rows.chunks))
    description = _optional_string(metadata, "description", cde_key)
    cde_type = CdeType.PV if values else CdeType.PASSTHROUGH
    return _DecodedCde(
        info=CDEInfo(cde_id, cde_key, description, cde_type),
        values=values,
        relations=tuple(_flat_relation(version, cde_key, raw) for raw in cast(list[object], raw_relations)),
    )


def _flat_relation(
    version: DataModelVersionReference,
    cde_key: str,
    raw: object,
) -> CdeNodeRelation:
    if not isinstance(raw, Mapping):
        raise ReferenceDataCorruptError(f"Reference node relationship is invalid: {cde_key}")
    fields = cast(Mapping[str, object], raw)
    node = NodeName(_required_string(fields, "node_name"))
    node_cde_id = _required_int(fields, "node_cde_id")
    if node_cde_id != _generated_cde_id(version, cde_key, node.value):
        raise ReferenceDataCorruptError(f"Reference node CDE id is invalid: {node.value}/{cde_key}")
    return CdeNodeRelation(
        node=node,
        node_cde_id=node_cde_id,
        source_node_cde_id=_optional_nonblank_string(fields, "source_node_cde_id", cde_key),
        description=_optional_string(fields, "description", cde_key),
        data_type=_optional_nonblank_string(fields, "data_type", cde_key),
        is_key=_optional_bool(fields, "is_key"),
        is_required=_optional_bool(fields, "is_required"),
    )


def _optional_nonblank_string(fields: Mapping[str, object], field: str, key: str) -> str | None:
    value = _optional_string(fields, field, key)
    if value is not None and not value.strip():
        raise ReferenceDataCorruptError(f"Reference field is invalid: {field}")
    return value


def _optional_bool(fields: Mapping[str, object], field: str) -> bool | None:
    value = fields.get(field)
    if value is None or isinstance(value, bool):
        return value
    raise ReferenceDataCorruptError(f"Reference field is invalid: {field}")


def _canonical_flat_payload(reference: AutoModelReference) -> Mapping[str, object]:
    """Canonical flat model data used for the publication digest."""
    model = reference.model
    return {
        "schema_version": SCHEMA_VERSION,
        "data_model_key": model.version.data_model_key,
        "external_version_number": model.version.external_version_number,
        "label": model.label,
        "nodes": sorted(node.value for node in reference.nodes),
        "cdes": [
            {
                "cde_key": cde.cde_key,
                "cde_id": cde.cde_id,
                "description": cde.description,
                "values": sorted(model.pvs.values[cde.cde_key]),
                "node_relations": [
                    _canonical_relation(relation)
                    for relation in reference.node_relations.get(cde.cde_key, ())
                ],
            }
            for cde in model.catalog
        ],
    }


def _canonical_relation(relation: CdeNodeRelation) -> Mapping[str, object]:
    return {
        "node_name": relation.node.value,
        "node_cde_id": relation.node_cde_id,
        "source_node_cde_id": relation.source_node_cde_id,
        "description": relation.description,
        "data_type": relation.data_type,
        "is_key": relation.is_key,
        "is_required": relation.is_required,
    }


def _generated_cde_id(version: DataModelVersionReference, cde_key: str, node_name: str | None = None) -> int:
    """Hash a typed identity to a JSON-safe integer."""
    parts = ["cde", version.data_model_key, version.external_version_number, cde_key]
    if node_name is not None:
        parts = ["node-cde", version.data_model_key, version.external_version_number, node_name, cde_key]
    digest = hashlib.sha256(_canonical_json(parts)).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 53) - 1)


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
    if not isinstance(decoded, list) or any(not isinstance(value, str) or not value.strip() for value in decoded):
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
