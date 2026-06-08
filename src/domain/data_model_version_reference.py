"""Data model identity plus selected external version."""

from __future__ import annotations

from dataclasses import dataclass

_DISALLOWED_LATEST_VERSION = "latest"


@dataclass(frozen=True)
class DataModelVersionReference:
    """Canonical reference to one selected data model version."""

    data_model_key: str
    external_version_number: str

    def __post_init__(self) -> None:
        cleaned = _clean_external_version(self.external_version_number)
        object.__setattr__(self, "external_version_number", cleaned)

def _clean_external_version(external_version_number: str) -> str:
    cleaned = external_version_number.strip()
    if not cleaned:
        raise ValueError("external_version_number is required")
    if cleaned == _DISALLOWED_LATEST_VERSION:
        raise ValueError("external_version_number cannot be latest")
    return cleaned


__all__ = ["DataModelVersionReference"]
