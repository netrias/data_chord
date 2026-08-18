"""Reusable harmonization results scoped by reference-data identity."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.harmonization import MatchFidelity


class HarmonizationCacheError(Exception):
    """Base error for a cache that cannot safely return or store results."""


class HarmonizationCacheUnavailableError(HarmonizationCacheError):
    """The cache provider could not complete an operation."""


class HarmonizationCacheCorruptError(HarmonizationCacheError):
    """A stored cache row did not match its trusted domain identity."""


@dataclass(frozen=True)
class HarmonizationCacheKey:
    """One raw source value under one versioned data-standard CDE."""

    data_model_version: DataModelVersionReference
    cde_key: str
    source_value: str

    def __post_init__(self) -> None:
        if not self.cde_key.strip():
            raise ValueError("Harmonization cache CDE key is required")
        if not self.source_value.strip():
            raise ValueError("Harmonization cache source value is required")


@dataclass(frozen=True)
class HarmonizationCacheEntry:
    """One reusable result. The identity intentionally excludes implementation details."""

    key: HarmonizationCacheKey
    matched_value: str | None
    match_fidelity: MatchFidelity

    def __post_init__(self) -> None:
        if self.matched_value is not None and not self.matched_value.strip():
            raise ValueError("Cached matched value must be non-empty")
        if (self.matched_value is None) != (self.match_fidelity is MatchFidelity.NONE):
            raise ValueError("Cached match fidelity must agree with its matched value")


class HarmonizationCache(Protocol):
    def load_many(
        self, keys: Sequence[HarmonizationCacheKey]
    ) -> Mapping[HarmonizationCacheKey, HarmonizationCacheEntry]: ...

    def save_many(self, entries: Sequence[HarmonizationCacheEntry]) -> None: ...


class EmptyHarmonizationCache:
    """Default for local callers that do not configure external cache storage."""

    def load_many(
        self, keys: Sequence[HarmonizationCacheKey]
    ) -> Mapping[HarmonizationCacheKey, HarmonizationCacheEntry]:
        return {}

    def save_many(self, entries: Sequence[HarmonizationCacheEntry]) -> None:
        return None


__all__ = [
    "EmptyHarmonizationCache",
    "HarmonizationCache",
    "HarmonizationCacheCorruptError",
    "HarmonizationCacheEntry",
    "HarmonizationCacheError",
    "HarmonizationCacheKey",
    "HarmonizationCacheUnavailableError",
]
