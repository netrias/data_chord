"""Complete deterministic harmonization results for the packaged demo."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.domain.harmonization import MatchFidelity
from src.domain.harmonization_cache import (
    HarmonizationCacheEntry,
    HarmonizationCacheKey,
)

_MATCHES: Mapping[tuple[str, str], str] = {
    ("primary_diagnosis", "breast ca"): "Breast Cancer",
    ("specimen_type", "tumor tissue"): "Tissue",
    ("treatment_status", "done"): "Complete",
}


class DemoHarmonizationCache:
    """Answer every demo term so the normal harmonizer never opens Bedrock."""

    def load_many(
        self,
        keys: Sequence[HarmonizationCacheKey],
    ) -> Mapping[HarmonizationCacheKey, HarmonizationCacheEntry]:
        return {key: self._entry(key) for key in keys}

    def save_many(self, entries: Sequence[HarmonizationCacheEntry]) -> None:
        return None

    @staticmethod
    def _entry(key: HarmonizationCacheKey) -> HarmonizationCacheEntry:
        matched_value = _MATCHES.get((key.cde_key, key.source_value))
        return HarmonizationCacheEntry(
            key=key,
            matched_value=matched_value,
            match_fidelity=(
                MatchFidelity.STRONG if matched_value is not None else MatchFidelity.NONE
            ),
        )


__all__ = ["DemoHarmonizationCache"]
