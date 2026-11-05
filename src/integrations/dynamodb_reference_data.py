"""Strict DynamoDB storage for complete reference-model versions.

Rows are stored per node-CDE placement, so the stored copy keeps the shape the
source publishes and its row counts stay checkable against the publishing
manifest. ``load_model`` collapses that to the flat view the application reads;
``load_placements`` returns the stored form for verification and auditing.
"""

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
from src.domain.reference_data import (
    ReferenceDataCorruptError,
    ReferenceDataUnavailableError,
    ReferenceModel,
    ReferenceModelNotFoundError,
)
from src.domain.reference_placements import CdePlacement, PlacedReferenceModel, PlacementKey

SCHEMA_VERSION = 2
DEFAULT_VALUE_CHUNK_BYTES = 192 * 1024
MAX_ITEM_BYTES = 300 * 1024
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
            model = self.load_placements(version).collapse()
            with self._models_lock:
                self._models[version] = model
                self._models.move_to_end(version)
                if len(self._models) > _MAX_CACHED_MODELS:
                    self._models.popitem(last=False)
                return model

    def load_placements(self, version: DataModelVersionReference) -> PlacedReferenceModel:
        """The stored form, before any collapse. Every integrity rule runs here."""
        partition = _model_partition(version)
        items = self._query_partition(partition)
        if not items:
            raise ReferenceModelNotFoundError(
                f"Reference model is not published: {version.data_model_key}/{version.external_version_number}"
            )
        try:
            return _placed_from_items(version, items)
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


class ReferenceDataImporter:
    """Restartable writer used by the one-time migration command."""

    def __init__(
        self,
        table: DynamoTable,
        *,
        value_chunk_bytes: int = DEFAULT_VALUE_CHUNK_BYTES,
    ) -> None:
        if value_chunk_bytes < 16 or value_chunk_bytes > MAX_UNCOMPRESSED_CHUNK_BYTES:
            raise ValueError(f"value_chunk_bytes must be between 16 and {MAX_UNCOMPRESSED_CHUNK_BYTES}")
        self._table = table
        self._value_chunk_bytes = value_chunk_bytes

    def import_models(self, models: Sequence[PlacedReferenceModel], *, source_digest: str) -> None:
        _validate_import_source_digest(source_digest)
        ordered = _validated_import_models(models)
        catalog_items = [_catalog_item(model) for model in ordered]
        catalog_meta = _catalog_meta_item(catalog_items, source_digest)
        catalog_import = _catalog_import_item(catalog_items, source_digest)
        model_rows = [
            (model, _items_for_model(model, self._value_chunk_bytes))
            for model in ordered
        ]
        for item in [catalog_import, *catalog_items, catalog_meta]:
            _validate_item_for_write(item)
        self._put_immutable(catalog_import)
        repository = DynamoDbReferenceDataRepository(self._table)
        for model, items in model_rows:
            for item in items:
                self._put_immutable(item)
            if repository.load_placements(model.version) != model:
                raise RuntimeError("Imported reference model did not verify")
        for item in catalog_items:
            self._put_immutable(item)
        self._put_immutable(catalog_meta)
        if repository.list_models() != _summaries_for_models(ordered):
            raise RuntimeError("Imported reference catalog did not verify")

    def _put_immutable(self, item: Mapping[str, object]) -> None:
        key = {"pk": item["pk"], "sk": item["sk"]}
        existing = self._table.get_item(Key=key, ConsistentRead=True).get("Item")
        if existing is not None:
            if existing != item:
                raise RuntimeError(f"conditional write failed for {item['pk']}/{item['sk']}")
            return
        try:
            self._table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)")
        except Exception as exc:
            raced = self._table.get_item(Key=key, ConsistentRead=True).get("Item")
            if raced != item:
                raise RuntimeError(f"conditional write failed for {item['pk']}/{item['sk']}") from exc


def _items_for_model(model: PlacedReferenceModel, value_chunk_bytes: int) -> list[Mapping[str, object]]:
    pk = _model_partition(model.version)
    model_digest = _digest(_canonical_placed_payload(model))
    items: list[Mapping[str, object]] = [
        {
            "pk": pk,
            "sk": _IMPORT_SK,
            "schema_version": SCHEMA_VERSION,
            "data_model_key": model.version.data_model_key,
            "external_version_number": model.version.external_version_number,
            "digest": model_digest,
        }
    ]
    for node in model.nodes:
        items.append(
            {
                "pk": pk,
                "sk": _node_sort_key(node),
                "schema_version": SCHEMA_VERSION,
                "node": node,
                "cde_count": _cde_count_for_node(model, node),
            }
        )
    for placement in model.placements:
        placement_item: dict[str, object] = {
            "pk": pk,
            "sk": f"{_placement_prefix(placement.key)}#META",
            "schema_version": SCHEMA_VERSION,
            "node": placement.node,
            "cde_key": placement.cde_key,
            "description": placement.description,
            "data_type": placement.data_type,
            "is_required": placement.is_required,
        }
        if placement.cde_id is not None:
            placement_item["cde_id"] = placement.cde_id
        if placement.is_key is not None:
            placement_item["is_key"] = placement.is_key
        items.append(placement_item)
    for cde_key in model.cde_keys:
        values = sorted(model.values[cde_key])
        prefix = _values_prefix(cde_key)
        chunks = _split_values(values, value_chunk_bytes)
        items.append(
            {
                "pk": pk,
                "sk": f"{prefix}#META",
                "schema_version": SCHEMA_VERSION,
                "cde_key": cde_key,
                "value_count": len(values),
                "chunk_count": len(chunks),
                "digest": _digest(values),
            }
        )
        for index, values_chunk in enumerate(chunks):
            chunk_item: dict[str, object] = {
                "pk": pk,
                "sk": f"{prefix}#VALUES#{index:06d}",
                "schema_version": SCHEMA_VERSION,
                "cde_key": cde_key,
                "chunk_index": index,
                "uncompressed_size": len(_canonical_json(values_chunk)),
                "payload": _gzip_json(values_chunk),
            }
            items.append(chunk_item)
    items.append(
        {
            "pk": pk,
            "sk": _META_SK,
            "schema_version": SCHEMA_VERSION,
            "data_model_key": model.version.data_model_key,
            "external_version_number": model.version.external_version_number,
            "label": model.label,
            # Count CDEs per node: a key placed under two nodes counts twice.
            "placement_count": len(model.placements),
            "node_count": len(model.nodes),
            "cde_count": sum(_cde_count_for_node(model, node) for node in model.nodes),
            "value_count": sum(len(values) for values in model.values.values()),
            "digest": model_digest,
        }
    )
    for item in items:
        _validate_item_for_write(item)
    return items


def _catalog_item(model: PlacedReferenceModel) -> Mapping[str, object]:
    return {
        "pk": _CATALOG_PK,
        "sk": _catalog_sort_key(model.version.data_model_key, model.version.external_version_number),
        "schema_version": SCHEMA_VERSION,
        "data_model_key": model.version.data_model_key,
        "external_version_number": model.version.external_version_number,
        "label": model.label,
    }


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


def _validate_import_source_digest(source_digest: str) -> None:
    if len(source_digest) != 64 or any(character not in "0123456789abcdef" for character in source_digest):
        raise ValueError("source_digest must be 64 lowercase hexadecimal characters")


def _validated_import_models(models: Sequence[PlacedReferenceModel]) -> list[PlacedReferenceModel]:
    ordered = sorted(
        models,
        key=lambda model: (model.version.data_model_key, model.version.external_version_number),
    )
    identities = {(model.version.data_model_key, model.version.external_version_number) for model in ordered}
    if len(identities) != len(ordered):
        raise ValueError("Reference import contains duplicate model versions")
    labels: dict[str, str] = {}
    for model in ordered:
        existing_label = labels.setdefault(model.version.data_model_key, model.label)
        if existing_label != model.label:
            raise ValueError(f"Reference import has conflicting labels: {model.version.data_model_key}")
    return ordered


def _catalog_meta_item(
    items: Sequence[Mapping[str, object]],
    source_digest: str,
) -> Mapping[str, object]:
    return {
        "pk": _CATALOG_PK,
        "sk": _META_SK,
        "schema_version": SCHEMA_VERSION,
        "model_count": len(items),
        "digest": _digest(_catalog_payload(items)),
        "source_digest": source_digest,
    }


def _catalog_import_item(
    items: Sequence[Mapping[str, object]],
    source_digest: str,
) -> Mapping[str, object]:
    return {
        "pk": _CATALOG_PK,
        "sk": _IMPORT_SK,
        "schema_version": SCHEMA_VERSION,
        "model_count": len(items),
        "digest": _digest(_catalog_payload(items)),
        "source_digest": source_digest,
    }


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


def _summaries_for_models(models: Sequence[PlacedReferenceModel]) -> tuple[DataModelSummary, ...]:
    labels: dict[str, str] = {}
    versions: dict[str, list[DataModelVersionInfo]] = {}
    for model in models:
        labels[model.version.data_model_key] = model.label
        versions.setdefault(model.version.data_model_key, []).append(
            DataModelVersionInfo(model.version.external_version_number)
        )
    return tuple(
        DataModelSummary(key, labels[key], sorted(model_versions, key=lambda item: item.external_version_number))
        for key, model_versions in sorted(versions.items())
    )


def _placed_from_items(
    requested: DataModelVersionReference,
    items: list[Mapping[str, object]],
) -> PlacedReferenceModel:
    by_sk = _rows_by_sort_key(items)
    meta, import_seal = _model_markers(by_sk, requested)
    node_rows, placement_rows, value_rows = _model_rows(by_sk)
    unlisted = {node for node, _cde_key in placement_rows} - set(node_rows)
    if unlisted:
        raise ReferenceDataCorruptError(f"Reference placements name a node with no row: {min(unlisted)}")

    model = PlacedReferenceModel(
        version=requested,
        label=_required_string(meta, "label"),
        nodes=tuple(sorted(node_rows)),
        placements=tuple(
            _placement_from_metadata(key, metadata) for key, metadata in sorted(placement_rows.items())
        ),
        values={
            cde_key: frozenset(_values_for_cde(cde_key, rows.metadata, rows.chunks))
            for cde_key, rows in sorted(value_rows.items())
        },
    )
    _check_node_rows(model, node_rows)
    _check_model_counts(model, meta)
    if _digest(_canonical_placed_payload(model)) != _required_string(meta, "digest"):
        raise ReferenceDataCorruptError("Reference model digest is invalid")
    if _required_string(import_seal, "digest") != _required_string(meta, "digest"):
        raise ReferenceDataCorruptError("Reference import seal does not match its model")
    return model


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


def _model_rows(
    by_sk: dict[str, Mapping[str, object]],
) -> tuple[dict[str, Mapping[str, object]], dict[PlacementKey, Mapping[str, object]], dict[str, _ValueRows]]:
    """Split a model's rows into its nodes, its per-placement metadata and its per-CDE values."""
    node_rows: dict[str, Mapping[str, object]] = {}
    placement_rows: dict[PlacementKey, Mapping[str, object]] = {}
    value_metadata: dict[str, Mapping[str, object]] = {}
    chunk_rows: dict[str, list[Mapping[str, object]]] = {}
    for sk, item in by_sk.items():
        _validate_schema(item)
        node = _optional_string(item, "node", sk)
        if node is not None and sk == _node_sort_key(node):
            node_rows[node] = item
            continue
        cde_key = _required_string(item, "cde_key")
        if node is not None:
            if sk != f"{_placement_prefix((node, cde_key))}#META":
                raise ReferenceDataCorruptError(f"Unknown reference row: {sk}")
            placement_rows[(node, cde_key)] = item
            continue
        prefix = _values_prefix(cde_key)
        if sk == f"{prefix}#META":
            value_metadata[cde_key] = item
        elif sk.startswith(f"{prefix}#VALUES#"):
            chunk_rows.setdefault(cde_key, []).append(item)
        else:
            raise ReferenceDataCorruptError(f"Unknown reference row: {sk}")
    if set(chunk_rows) - set(value_metadata):
        raise ReferenceDataCorruptError("Reference values exist for an unknown CDE")
    values = {
        cde_key: _ValueRows(metadata=metadata, chunks=tuple(chunk_rows.get(cde_key, ())))
        for cde_key, metadata in value_metadata.items()
    }
    return node_rows, placement_rows, values


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


def _placement_from_metadata(key: PlacementKey, metadata: Mapping[str, object]) -> CdePlacement:
    node, cde_key = key
    label = f"{node}/{cde_key}"
    raw_cde_id = metadata.get("cde_id")
    return CdePlacement(
        node=node,
        cde_key=cde_key,
        cde_id=None if raw_cde_id is None else _required_int(metadata, "cde_id"),
        description=_optional_string(metadata, "description", label),
        data_type=_optional_string(metadata, "data_type", label),
        is_required=_optional_bool(metadata, "is_required", label),
        is_key=_optional_bool(metadata, "is_key", label),
    )


def _cde_count_for_node(model: PlacedReferenceModel, node: str) -> int:
    return sum(1 for placement in model.placements if placement.node == node)


def _check_node_rows(model: PlacedReferenceModel, node_rows: Mapping[str, Mapping[str, object]]) -> None:
    """A node's CDE count restates what the placements say, so it is checked, not trusted.

    A count of zero is legitimate: a model can define a node that holds no CDEs.
    """
    for node, item in node_rows.items():
        if _required_int(item, "cde_count") != _cde_count_for_node(model, node):
            raise ReferenceDataCorruptError(f"Reference node CDE count is invalid: {node}")


def _check_model_counts(model: PlacedReferenceModel, meta: Mapping[str, object]) -> None:
    """Each count is stored and checked separately so one cannot mask another."""
    expected = {
        "placement_count": len(model.placements),
        "node_count": len(model.nodes),
        "cde_count": sum(_cde_count_for_node(model, node) for node in model.nodes),
        "value_count": sum(len(values) for values in model.values.values()),
    }
    for field, actual in expected.items():
        if _required_int(meta, field) != actual:
            raise ReferenceDataCorruptError(f"Reference {field.replace('_', ' ')} is invalid")


def _canonical_placed_payload(model: PlacedReferenceModel) -> Mapping[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "data_model_key": model.version.data_model_key,
        "external_version_number": model.version.external_version_number,
        "label": model.label,
        "nodes": list(model.nodes),
        "placements": [
            ({
                "node": placement.node,
                "cde_key": placement.cde_key,
                "cde_id": placement.cde_id,
                "description": placement.description,
                "data_type": placement.data_type,
                "is_required": placement.is_required,
            } | ({"is_key": placement.is_key} if placement.is_key is not None else {}))
            for placement in model.placements
        ],
        "values": {cde_key: sorted(model.values[cde_key]) for cde_key in model.cde_keys},
    }


def _split_values(values: list[str], target_bytes: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    for value in values:
        candidate = [*current, value]
        if len(_canonical_json(candidate)) <= target_bytes:
            current = candidate
            continue
        if not current:
            raise ValueError("One permissible value is too large for a DynamoDB item")
        chunks.append(current)
        current = [value]
        if len(_canonical_json(current)) > target_bytes:
            raise ValueError("One permissible value is too large for a DynamoDB item")
    if current:
        chunks.append(current)
    return chunks


def _gzip_json(value: object) -> bytes:
    return gzip.compress(_canonical_json(value), mtime=0)


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


def _placement_prefix(key: PlacementKey) -> str:
    """Node leads the sort key so one node's rows read as a single range."""
    node, cde_key = key
    return f"NODE#{_encode(node)}#CDE#{_encode(cde_key)}"


def _node_sort_key(node: str) -> str:
    """A node's own row sorts directly above the placements that name it."""
    return f"NODE#{_encode(node)}"


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


def _optional_bool(item: Mapping[str, object], field: str, label: str) -> bool | None:
    value = item.get(field)
    if value is None:
        return None
    if not isinstance(value, bool):
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


def _item_size(item: Mapping[str, object]) -> int:
    size = 0
    for key, value in item.items():
        size += len(key.encode())
        if isinstance(value, bytes):
            size += len(value)
        elif isinstance(value, str):
            size += len(value.encode())
        else:
            size += len(str(value).encode())
    return size


def _validate_item_for_write(item: Mapping[str, object]) -> None:
    pk = _required_string(item, "pk")
    sk = _required_string(item, "sk")
    if len(pk.encode()) > 2048 or len(sk.encode()) > 1024:
        raise ValueError("A reference-data key is too large for DynamoDB")
    if _item_size(item) > MAX_ITEM_BYTES:
        raise ValueError(f"One reference-data item is too large: {pk}/{sk}")


__all__ = [
    "DEFAULT_VALUE_CHUNK_BYTES",
    "DynamoDbReferenceDataRepository",
    "DynamoResource",
    "DynamoTable",
    "MAX_ITEM_BYTES",
    "MAX_UNCOMPRESSED_CHUNK_BYTES",
    "ReferenceDataImporter",
    "SCHEMA_VERSION",
]
