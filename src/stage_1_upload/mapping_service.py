"""Fetch column recommendations via the Netrias client SDK."""

from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, cast

from .schemas import ModelSuggestion

logger = logging.getLogger(__name__)
DEFAULT_SAMPLE_LIMIT = 50

manual_cde_id_mappings = {
    1: "therapeutic_agents",
    2: "primary_diagnosis",
    3: "morphology",
    4: "tissue_or_organ_of_origin",
    5: "sample_anatomic_site",
}



class MappingClientProtocol(Protocol):
    """why: describe just the methods we consume from NetriasClient."""

    def configure(self, *, api_key: str) -> None:
        ...

    def discover_mapping_from_csv(
        self,
        *,
        source_csv: Path,
        target_schema: str,
        sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    ) -> object:
        ...

    def discover_cde_mapping(
        self,
        *,
        source_csv: Path,
        target_schema: str,
        sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    ) -> object:
        ...

class MappingDiscoveryService:
    """why: wrap Netrias mapping discovery with safe fallbacks."""

    def __init__(self) -> None:
        self._api_key: str | None = os.getenv("NETRIAS_API_KEY")
        self._client: MappingClientProtocol | None = self._build_client()
        if not self._client:
            raise RuntimeError("Netrias mapping discovery requires NETRIAS_API_KEY and netrias_client.")

    def _build_client(self) -> MappingClientProtocol | None:
        try:
            module = importlib.import_module("netrias_client")
            client_cls = cast(type[MappingClientProtocol], getattr(module, "NetriasClient"))
        except (ModuleNotFoundError, AttributeError):
            logger.warning("netrias_client not available; mapping suggestions will be empty.")
            return None
        if not self._api_key:
            logger.warning("NETRIAS_API_KEY missing; mapping suggestions will be empty.")
            return None
        client = client_cls()  # type: ignore[call-arg]
        configure = cast(Callable[..., None], getattr(client, "configure"))
        configure(api_key=self._api_key, confidence_threshold=0.0)
        return client

    def available(self) -> bool:
        return self._client is not None

    def discover(
        self,
        *,
        csv_path: Path,
        target_schema: str,
        sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    ) -> tuple[dict[str, list[ModelSuggestion]], dict[str, str]]:
        if not self._client:
            raise RuntimeError("Netrias mapping discovery client is not configured.")
        try:
            if hasattr(self._client, "discover_cde_mapping"):
                raw_result = self._client.discover_cde_mapping(
                    source_csv=csv_path,
                    target_schema=target_schema,
                    sample_limit=sample_limit,
                )
            else:
                raw_result = self._client.discover_mapping_from_csv(
                    source_csv=csv_path,
                    target_schema=target_schema,
                    sample_limit=sample_limit,
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Netrias mapping discovery failed", exc_info=exc)
            return {}, {}

        suggestions = cast(Sequence[object], getattr(raw_result, "suggestions", ()))
        column_suggestions: dict[str, list[ModelSuggestion]] = {}
        manual_overrides: dict[str, str] = {}
        for suggestion in suggestions:
            column = cast(str | None, getattr(suggestion, "source_column", None))
            if not column:
                continue
            option_candidates = cast(Sequence[object], getattr(suggestion, "options", ())) or ()
            parsed_options = [_model_suggestion_from_sequence_item(option) for option in option_candidates]
            options = [option for option in parsed_options if option is not None]
            if options:
                column_suggestions[column] = options

        raw_payload = _raw_payload_from_result(raw_result)
        column_entries = _extract_column_entries(raw_payload)
        if column_entries:
            _merge_column_entry_suggestions(column_suggestions, column_entries)

        recognized_payload = raw_payload.get("recognized_mappings") or raw_payload.get("recognizedMappings")
        recognized: dict[str, int] = {}
        if isinstance(recognized_payload, dict):
            recognized_dict = cast(dict[object, object], recognized_payload)
            for column_key_obj, cde_id_value_obj in recognized_dict.items():
                if isinstance(column_key_obj, str) and isinstance(cde_id_value_obj, int):
                    recognized[column_key_obj] = cde_id_value_obj

        if not recognized:
            for column, entry in column_entries.items():
                cde_id = entry.get("cdeId") or entry.get("cde_id")
                if isinstance(cde_id, int):
                    recognized[column] = cde_id

        filtered_mapping: dict[str, list[ModelSuggestion]] = {}
        for column, cde_id in recognized.items():
            target_label = manual_cde_id_mappings.get(cde_id)
            if not target_label:
                continue
            manual_overrides[column] = target_label
            options = column_suggestions.get(column, [])
            constrained = [opt for opt in options if opt.target == target_label]
            if not constrained:
                constrained = [ModelSuggestion(target=target_label, similarity=1.0)]
            else:
                constrained.sort(key=lambda opt: opt.similarity, reverse=True)
            filtered_mapping[column] = constrained
        logger.info(
            "Recognized manual mappings",
            extra={"recognized": recognized, "manual_overrides": manual_overrides},
        )
        if filtered_mapping:
            preview = {key: [opt.target for opt in value[:3]] for key, value in list(filtered_mapping.items())[:5]}
            logger.info(
                "Netrias mapping discovery returned data",
                extra={
                    "column_count": len(filtered_mapping),
                    "preview": preview,
                    "recognized_count": len(recognized),
                    "raw_keys": list(raw_payload.keys()),
                },
            )
        else:
            logger.warning(
                "Netrias mapping discovery returned zero columns",
                extra={"target_schema": target_schema, "raw_keys": list(raw_payload.keys())},
            )
        return filtered_mapping, manual_overrides


def _raw_payload_from_result(result: object) -> dict[str, object]:
    """why: normalize the varying shapes netrias_client may return."""

    candidate = getattr(result, "raw", None)
    mapping = _dict_with_string_keys(candidate)
    if mapping is not None:
        return mapping
    mapping = _dict_with_string_keys(result)
    return mapping if mapping is not None else {}


def _dict_with_string_keys(value: object) -> dict[str, object] | None:
    """why: defensive helper that copies mappings while dropping non-str keys."""

    if not isinstance(value, Mapping):
        return None
    typed: dict[str, object] = {}
    for key, entry in value.items():
        if isinstance(key, str):
            typed[key] = entry
    return typed


def _extract_column_entries(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """why: collect column entry dicts from any supported payload key."""

    keys = ("column_entries", "columnEntries", "column_mappings", "columnMappings")
    for key in keys:
        entries = _normalize_column_entries(payload.get(key))
        if entries:
            return entries
    return {}


def _normalize_column_entries(value: object) -> dict[str, dict[str, object]]:
    """why: coerce list/dict column entry containers into a uniform mapping."""

    if isinstance(value, dict):
        entries: dict[str, dict[str, object]] = {}
        for key, entry in value.items():
            column = key if isinstance(key, str) else None
            entry_dict = _dict_with_string_keys(entry)
            if column and entry_dict is not None:
                entries[column] = entry_dict
        return entries
    if isinstance(value, list):
        entries = {}
        for item in value:
            entry_dict = _dict_with_string_keys(item)
            if entry_dict is None:
                continue
            column = _column_name_from_entry(entry_dict)
            if column:
                entries[column] = entry_dict
        return entries
    return {}


def _column_name_from_entry(entry: Mapping[str, object]) -> str | None:
    """why: determine the source column name for a raw entry."""

    for key in ("sourceColumn", "source_column", "column", "field", "name"):
        value = entry.get(key)
        if isinstance(value, str):
            column = value.strip()
            if column:
                return column
    return None


def _merge_column_entry_suggestions(
    mapping: dict[str, list[ModelSuggestion]],
    column_entries: Mapping[str, Mapping[str, object]],
) -> None:
    """why: extend mapping entries with options provided in raw column data."""

    for column, entry in column_entries.items():
        options = _options_from_entry(entry)
        if not options:
            continue
        existing = mapping.setdefault(column, [])
        _append_unique(existing, options)


def _options_from_entry(entry: Mapping[str, object]) -> list[ModelSuggestion]:
    """why: parse known option containers on a column entry."""

    for key in ("suggestions", "options", "targets"):
        parsed = _options_from_sequence(entry.get(key))
        if parsed:
            return parsed
    fallback_target = _extract_target_value(entry)
    if fallback_target:
        return [ModelSuggestion(target=fallback_target, similarity=1.0)]
    return []


def _options_from_sequence(raw: object) -> list[ModelSuggestion]:
    """why: convert array-like option containers into ModelSuggestion records."""

    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    suggestions: list[ModelSuggestion] = []
    for item in raw:
        option = _model_suggestion_from_mapping(item)
        if option is not None:
            suggestions.append(option)
    return sorted(suggestions, key=lambda opt: opt.similarity, reverse=True)


def _model_suggestion_from_sequence_item(option: object) -> ModelSuggestion | None:
    """why: convert MappingSuggestion.option objects (or mappings) to ModelSuggestion."""

    target = cast(str | None, getattr(option, "target", None))
    confidence = getattr(option, "confidence", None)
    score = float(confidence) if isinstance(confidence, (int, float)) else None
    if target:
        return ModelSuggestion(target=target, similarity=score or 0.0)
    return _model_suggestion_from_mapping(option)


def _model_suggestion_from_mapping(option: object) -> ModelSuggestion | None:
    """why: best-effort coercion for raw mapping objects describing a suggestion."""

    mapping = _dict_with_string_keys(option)
    if mapping is None:
        return None
    target = _extract_target_value(mapping)
    if not target:
        return None
    similarity = _extract_similarity(mapping)
    return ModelSuggestion(target=target, similarity=similarity if similarity is not None else 0.0)


def _extract_target_value(container: Mapping[str, object]) -> str | None:
    """why: resolve the textual CDE target field from a mapping container."""

    for key in (
        "target",
        "targetField",
        "target_field",
        "field",
        "name",
        "cde",
        "cde_name",
        "qualified_name",
    ):
        value = container.get(key)
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed
    return None


def _extract_similarity(container: Mapping[str, object]) -> float | None:
    """why: pull a numeric similarity/confidence score if one exists."""

    for key in ("similarity", "confidence", "score", "probability"):
        value = container.get(key)
        maybe = _coerce_float(value)
        if maybe is not None:
            return maybe
    return None


def _coerce_float(value: object) -> float | None:
    """why: convert common numeric representations into floats."""

    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            return float(trimmed)
        except ValueError:
            return None
    return None


def _append_unique(target: list[ModelSuggestion], additions: Sequence[ModelSuggestion]) -> None:
    """why: extend suggestion lists without duplicating targets."""

    existing_targets = {option.target for option in target}
    for option in additions:
        if option.target in existing_targets:
            continue
        target.append(option)
