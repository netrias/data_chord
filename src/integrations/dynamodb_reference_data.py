"""Strict DynamoDB storage for complete reference-model versions."""

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

SCHEMA_VERSION = 1
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
class _CdeRows:
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
        return _catalog_summaries(items)

    def load_model(self, version: DataModelVersionReference) -> ReferenceModel:
        cached = self._cached_model(version)
        if cached is not None:
            return cached
        # Serialize misses so large models are not decoded concurrently.
        with self._model_load_lock:
            cached = self._cached_model(version)
            if cached is not None:
                return cached
            model = self._load_uncached_model(version)
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
            self._models.move_to_end(version)
            if len(self._models) > _MAX_CACHED_MODELS:
                self._models.popitem(last=False)

    def _load_uncached_model(self, version: DataModelVersionReference) -> ReferenceModel:
        items = self._query_partition(_model_partition(version))
        if not items:
            raise ReferenceModelNotFoundError(
                f"Reference model is not published: {version.data_model_key}/{version.external_version_number}"
            )
        try:
            return _model_from_items(version, items)
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

    def import_models(self, models: Sequence[ReferenceModel], *, source_digest: str) -> None:
        _validate_import_source_digest(source_digest)
        ordered = _validated_import_models(models)
        catalog_items = [_catalog_item(model) for model in ordered]
        catalog_meta = _catalog_meta_item(catalog_items, source_digest)
        catalog_import = _catalog_import_item(catalog_items, source_digest)
        model_rows = [(model, _items_for_model(model, self._value_chunk_bytes)) for model in ordered]
        for item in [catalog_import, *catalog_items, catalog_meta]:
            _validate_item_for_write(item)
        self._put_immutable(catalog_import)
        repository = DynamoDbReferenceDataRepository(self._table)
        for model, items in model_rows:
            for item in items:
                self._put_immutable(item)
            if repository.load_model(model.version) != model:
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


def _items_for_model(model: ReferenceModel, value_chunk_bytes: int) -> list[Mapping[str, object]]:
    pk = _model_partition(model.version)
    model_payload = _canonical_model_payload(model)
    model_digest = _digest(model_payload)
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
    for cde in model.catalog:
        values = sorted(model.pvs.get(cde.cde_key) or ())
        encoded_key = _encode(cde.cde_key)
        chunks = _split_values(values, value_chunk_bytes)
        digest = _digest(values)
        cde_item: dict[str, object] = {
            "pk": pk,
            "sk": f"CDE#{encoded_key}#META",
            "schema_version": SCHEMA_VERSION,
            "cde_key": cde.cde_key,
            "description": cde.description,
            "cde_type": cde.cde_type.value,
            "value_count": len(values),
            "chunk_count": len(chunks),
            "digest": digest,
        }
        if cde.cde_id is not None:
            cde_item["cde_id"] = cde.cde_id
        items.append(cde_item)
        for index, values_chunk in enumerate(chunks):
            payload = _gzip_json(values_chunk)
            chunk_item: dict[str, object] = {
                "pk": pk,
                "sk": f"CDE#{encoded_key}#VALUES#{index:06d}",
                "schema_version": SCHEMA_VERSION,
                "cde_key": cde.cde_key,
                "chunk_index": index,
                "uncompressed_size": len(_canonical_json(values_chunk)),
                "payload": payload,
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
            "cde_count": len(model.catalog),
            "value_count": sum(len(values) for values in model.pvs.values.values()),
            "digest": model_digest,
        }
    )
    for item in items:
        _validate_item_for_write(item)
    return items


def _catalog_item(model: ReferenceModel) -> Mapping[str, object]:
    return {
        "pk": _CATALOG_PK,
        "sk": _catalog_sort_key(model.version.data_model_key, model.version.external_version_number),
        "schema_version": SCHEMA_VERSION,
        "data_model_key": model.version.data_model_key,
        "external_version_number": model.version.external_version_number,
        "label": model.label,
    }


@dataclass(frozen=True)
class _CatalogPublication:
    metadata: Mapping[str, object]
    seal: Mapping[str, object]


def _catalog_summaries(items: list[Mapping[str, object]]) -> tuple[DataModelSummary, ...]:
    rows = {_required_string(item, "sk"): item for item in items}
    if len(rows) != len(items):
        raise ReferenceDataCorruptError("Reference catalog contains duplicate rows")
    publication = _catalog_markers(rows)
    if publication is None:
        return ()
    entries = list(rows.values())
    summaries = _summaries_from_entries(entries)
    _validate_catalog_integrity(entries, publication)
    return summaries


def _summaries_from_entries(entries: list[Mapping[str, object]]) -> tuple[DataModelSummary, ...]:
    labels: dict[str, str] = {}
    versions: dict[str, set[str]] = {}
    for item in entries:
        _validate_schema(item)
        key = _required_string(item, "data_model_key")
        label = _required_string(item, "label")
        version = _required_string(item, "external_version_number")
        if _required_string(item, "sk") != _catalog_sort_key(key, version):
            raise ReferenceDataCorruptError("Reference catalog identity does not match its key")
        if labels.setdefault(key, label) != label:
            raise ReferenceDataCorruptError(f"Reference catalog labels disagree: {key}")
        versions.setdefault(key, set()).add(version)
    return tuple(
        DataModelSummary(key, labels[key], [DataModelVersionInfo(v) for v in sorted(available)])
        for key, available in sorted(versions.items())
    )


def _validate_catalog_integrity(entries: list[Mapping[str, object]], publication: _CatalogPublication) -> None:
    meta, seal = publication.metadata, publication.seal
    if _required_int(meta, "model_count") != len(entries):
        raise ReferenceDataCorruptError("Reference catalog model count is invalid")
    if _required_string(meta, "digest") != _digest(_catalog_payload(entries)):
        raise ReferenceDataCorruptError("Reference catalog digest is invalid")
    source_digest = _required_sha256(meta, "source_digest")
    if (
        _required_sha256(seal, "source_digest") != source_digest
        or _required_string(seal, "digest") != _required_string(meta, "digest")
        or _required_int(seal, "model_count") != _required_int(meta, "model_count")
    ):
        raise ReferenceDataCorruptError("Reference catalog import seal does not match its catalog")


def _catalog_markers(
    by_sk: dict[str, Mapping[str, object]],
) -> _CatalogPublication | None:
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
    return _CatalogPublication(meta, import_seal)


def _validate_import_source_digest(source_digest: str) -> None:
    if len(source_digest) != 64 or any(character not in "0123456789abcdef" for character in source_digest):
        raise ValueError("source_digest must be 64 lowercase hexadecimal characters")


def _validated_import_models(models: Sequence[ReferenceModel]) -> list[ReferenceModel]:
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


def _summaries_for_models(models: Sequence[ReferenceModel]) -> tuple[DataModelSummary, ...]:
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


def _model_from_items(
    requested: DataModelVersionReference,
    items: list[Mapping[str, object]],
) -> ReferenceModel:
    rows = _index_model_rows(items)
    metadata = _model_metadata(rows, requested)
    cde_rows = _group_cde_rows(rows)
    values = {key: _values_for_cde(key, row) for key, row in sorted(cde_rows.items())}
    model = ReferenceModel(
        version=requested,
        label=_required_string(metadata, "label"),
        catalog=CdeCatalog.from_cdes([_cde_from_metadata(key, row.metadata) for key, row in sorted(cde_rows.items())]),
        pvs=CdePvCatalog.from_mapping(values),
    )
    _validate_model_integrity(model, metadata)
    return model


def _index_model_rows(items: list[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    rows: dict[str, Mapping[str, object]] = {}
    for item in items:
        key = _required_string(item, "sk")
        if key in rows:
            raise ReferenceDataCorruptError(f"Duplicate reference row: {key}")
        rows[key] = item
    return rows


def _model_metadata(
    rows: dict[str, Mapping[str, object]],
    requested: DataModelVersionReference,
) -> Mapping[str, object]:
    metadata = rows.pop(_META_SK, None)
    if metadata is None:
        raise ReferenceDataCorruptError("Reference model has no complete marker")
    seal = rows.pop(_IMPORT_SK, None)
    if seal is None:
        raise ReferenceDataCorruptError("Reference model has no immutable import seal")
    _validate_schema(metadata)
    _validate_schema(seal)
    _validate_model_identity(metadata, requested, "Reference model identity does not match its key")
    _validate_model_identity(seal, requested, "Reference import seal does not match its model")
    if _required_string(seal, "digest") != _required_string(metadata, "digest"):
        raise ReferenceDataCorruptError("Reference import seal does not match its model")
    return metadata


def _validate_model_identity(
    item: Mapping[str, object],
    requested: DataModelVersionReference,
    message: str,
) -> None:
    if (
        _required_string(item, "data_model_key") != requested.data_model_key
        or _required_string(item, "external_version_number") != requested.external_version_number
    ):
        raise ReferenceDataCorruptError(message)


def _group_cde_rows(rows: Mapping[str, Mapping[str, object]]) -> dict[str, _CdeRows]:
    metadata: dict[str, Mapping[str, object]] = {}
    chunks: dict[str, list[Mapping[str, object]]] = {}
    for key, item in rows.items():
        _validate_schema(item)
        cde_key = _required_string(item, "cde_key")
        prefix = f"CDE#{_encode(cde_key)}"
        if key == f"{prefix}#META":
            metadata[cde_key] = item
        elif key.startswith(f"{prefix}#VALUES#"):
            chunks.setdefault(cde_key, []).append(item)
        else:
            raise ReferenceDataCorruptError(f"Unknown reference row: {key}")
    if set(chunks) - set(metadata):
        raise ReferenceDataCorruptError("Reference values exist for an unknown CDE")
    return {key: _CdeRows(item, tuple(chunks.get(key, ()))) for key, item in metadata.items()}


def _values_for_cde(cde_key: str, rows: _CdeRows) -> frozenset[str]:
    chunks = sorted(rows.chunks, key=lambda item: _required_int(item, "chunk_index"))
    indexes = [_required_int(item, "chunk_index") for item in chunks]
    if indexes != list(range(_required_int(rows.metadata, "chunk_count"))):
        raise ReferenceDataCorruptError(f"Reference value chunks are incomplete: {cde_key}")
    values: list[str] = []
    for chunk in chunks:
        index = _required_int(chunk, "chunk_index")
        if _required_string(chunk, "sk") != f"CDE#{_encode(cde_key)}#VALUES#{index:06d}":
            raise ReferenceDataCorruptError(f"Reference value chunk identity is invalid: {cde_key}")
        values.extend(_gunzip_values(chunk.get("payload"), _required_int(chunk, "uncompressed_size")))
    if len(values) != _required_int(rows.metadata, "value_count") or len(values) != len(set(values)):
        raise ReferenceDataCorruptError(f"Reference value count is invalid: {cde_key}")
    if _digest(values) != _required_string(rows.metadata, "digest"):
        raise ReferenceDataCorruptError(f"Reference value digest is invalid: {cde_key}")
    return frozenset(values)


def _cde_from_metadata(cde_key: str, metadata: Mapping[str, object]) -> CDEInfo:
    try:
        cde_type = CdeType(_required_string(metadata, "cde_type"))
    except ValueError as exc:
        raise ReferenceDataCorruptError(f"Reference CDE type is invalid: {cde_key}") from exc
    description = metadata.get("description")
    if description is not None and not isinstance(description, str):
        raise ReferenceDataCorruptError(f"Reference CDE description is invalid: {cde_key}")
    raw_id = metadata.get("cde_id")
    return CDEInfo(
        None if raw_id is None else _required_int(metadata, "cde_id"),
        cde_key,
        cast(str | None, description),
        cde_type,
    )


def _validate_model_integrity(model: ReferenceModel, metadata: Mapping[str, object]) -> None:
    if len(model.catalog) != _required_int(metadata, "cde_count"):
        raise ReferenceDataCorruptError("Reference CDE count is invalid")
    if sum(len(values) for values in model.pvs.values.values()) != _required_int(metadata, "value_count"):
        raise ReferenceDataCorruptError("Reference model value count is invalid")
    if _digest(_canonical_model_payload(model)) != _required_string(metadata, "digest"):
        raise ReferenceDataCorruptError("Reference model digest is invalid")


def _canonical_model_payload(model: ReferenceModel) -> Mapping[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "data_model_key": model.version.data_model_key,
        "external_version_number": model.version.external_version_number,
        "label": model.label,
        "cdes": [
            {
                "cde_key": cde.cde_key,
                "cde_id": cde.cde_id,
                "description": cde.description,
                "cde_type": cde.cde_type.value,
                "values": sorted(model.pvs.get(cde.cde_key) or ()),
            }
            for cde in sorted(model.catalog, key=lambda item: item.cde_key)
        ],
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
