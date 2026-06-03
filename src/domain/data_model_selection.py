"""Selected target data model version used across mapping and harmonization."""

from __future__ import annotations

from dataclasses import dataclass

_LEGACY_LATEST_VERSION = "latest"


@dataclass(frozen=True)
class DataModelSelection:
    """Canonical representation for the chosen target data model version."""

    key: str
    external_version_number: str

    def __post_init__(self) -> None:
        cleaned = _clean_external_version(self.external_version_number)
        object.__setattr__(self, "external_version_number", cleaned)

    @classmethod
    def from_external_version_number(cls, key: str, external_version_number: str) -> DataModelSelection:
        return cls(key=key, external_version_number=external_version_number)

    @classmethod
    def from_legacy_version_number(cls, key: str, version_number: int) -> DataModelSelection:
        """Compatibility shim for old stored state and URLs that used internal numbers."""
        return cls(key=key, external_version_number=str(version_number))


def _clean_external_version(external_version_number: str) -> str:
    cleaned = external_version_number.strip()
    if not cleaned:
        raise ValueError("external_version_number is required")
    if cleaned == _LEGACY_LATEST_VERSION:
        raise ValueError("external_version_number cannot be latest")
    return cleaned


__all__ = ["DataModelSelection"]
