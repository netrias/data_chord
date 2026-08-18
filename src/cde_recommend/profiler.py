"""Pure functions for column profiling and serialization.

Changes when profiling heuristics change.
"""

import random
import re
from collections import Counter
from collections.abc import Sequence

from src.cde_recommend.types import ColumnProfile, ColumnType

# Hard cap on values we consider for stats. Larger datasets don't sharpen the
# distribution tests meaningfully, and this keeps profiling O(1) per column.
_MAX_STATS_SAMPLE = 5000
# Type inference sees at most 200 values — float/regex tests per value are the
# hot loop, and additional values rarely flip the classification.
_MAX_TYPE_SAMPLE = 200
# A column is numeric if ≥90% of sampled values parse as float. The threshold
# leaves room for occasional junk (e.g. "N/A" in an otherwise-numeric column)
# without misclassifying truly mixed columns.
_NUMERIC_THRESHOLD = 0.9
# Id-like columns are almost-unique (>90% distinct values) and alnum-heavy.
# Accession numbers, UUIDs, and surrogate keys all fit this shape.
_UNIQUENESS_THRESHOLD = 0.9
_ALNUM_THRESHOLD = 0.8
# Low uniqueness ratio OR short average length indicates a categorical column
# (e.g. gender, race, ethnicity) vs free-text (descriptions, notes). Either
# condition is sufficient — both would be over-restrictive.
_CATEGORICAL_UNIQUENESS_THRESHOLD = 0.3
_CATEGORICAL_AVG_LEN_THRESHOLD = 25


def profile_column(
    name: str,
    raw_values: Sequence[object],
    *,
    seed: int = 0,
    max_samples: int = 12,
) -> ColumnProfile:
    # Clean first (strip, dedupe empty-string equivalents) so every downstream
    # step sees the same normalized values — type inference, sampling, and
    # dedup must agree on what counts as a "value."
    cleaned = [v for v in (_clean_value(x) for x in raw_values) if v is not None]
    stats_sample = cleaned[:_MAX_STATS_SAMPLE]
    dtype = _infer_type(stats_sample)
    sample_values = _sample_values(stats_sample, dtype, max_samples, seed)

    return ColumnProfile(
        column_name=name,
        dtype=dtype,
        sample_values=sample_values,
    )


# --- Private helpers ---


def _clean_value(v: object, max_len: int = 120) -> str | None:
    """Normalize a raw value into a cleaned string, or None if it's effectively empty.

    Returning None (not empty string) lets callers drop null-equivalents with a
    single `is not None` filter. Whitespace normalization matters because the
    cleaned values end up in the LLM prompt — embedded newlines and tabs would
    waste tokens and confuse line-based parsing.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    # Truncate obvious outliers (free-text comments, serialized blobs) with an
    # ellipsis so the model still sees the truncated sample was a long value.
    if len(s) > max_len:
        s = s[:max_len] + "\u2026"
    return s


def _infer_type(values: list[str]) -> ColumnType:
    # Empty input is "mixed" rather than a specific type — we can't
    # meaningfully classify nothing, and downstream code already short-circuits
    # the empty case.
    if not values:
        return "mixed"

    sample = values[:_MAX_TYPE_SAMPLE]
    sample_len = len(sample)

    # Order matters: numeric must win over id_like (digit strings pass both),
    # and id_like must win over categorical (high uniqueness would otherwise
    # force categorical-or-free-text to decide between two ill-fitting bins).
    if _is_numeric(sample, sample_len):
        return "numeric"
    if _is_id_like(values, sample, sample_len):
        return "id_like"
    return _categorical_or_free_text(values, sample, sample_len)


def _is_numeric(sample: list[str], sample_len: int) -> bool:
    num = 0
    for s in sample:
        try:
            float(s)
            num += 1
        except ValueError:
            pass
    return num / max(1, sample_len) >= _NUMERIC_THRESHOLD


def _is_id_like(values: list[str], sample: list[str], sample_len: int) -> bool:
    # Uniqueness is computed over all values (not the capped sample) so very
    # large columns aren't penalized by sample collisions that inflate the
    # unique-ratio denominator.
    uniq_ratio = len(set(values)) / max(1, len(values))
    if uniq_ratio <= _UNIQUENESS_THRESHOLD:
        return False
    # Allowed characters match realistic identifier shapes: UUIDs, accession
    # numbers, row keys, slash-separated codes. Punctuation characters not
    # in this set suggest free-text, not identifiers.
    alnum_count = sum(
        1 for s in sample if re.fullmatch(r"[A-Za-z0-9._\-:/]+", s) is not None
    )
    return alnum_count / max(1, sample_len) > _ALNUM_THRESHOLD


def _categorical_or_free_text(
    values: list[str], sample: list[str], sample_len: int
) -> ColumnType:
    uniq_ratio = len(set(values)) / max(1, len(values))
    avg_len = sum(len(s) for s in sample) / max(1, sample_len)
    if uniq_ratio < _CATEGORICAL_UNIQUENESS_THRESHOLD or avg_len < _CATEGORICAL_AVG_LEN_THRESHOLD:
        return "categorical"
    return "free_text"


def _sample_values(
    cleaned: list[str], dtype: ColumnType, max_samples: int, seed: int
) -> list[str]:
    """Choose values to show the LLM; strategy depends on column type.

    Seeded RNG: the same input must always produce the same sample so cache
    keys stay stable across application restarts and replay.
    """
    if not cleaned:
        return []

    # Seeded pseudo-random order keeps prompt samples stable. It is not security data.
    rng = random.Random(seed)  # nosec B311

    # Categorical/mixed columns benefit from frequency-weighted sampling so the
    # model sees the dominant labels first. For identifier-heavy or free-text
    # columns a plain shuffle is better — frequency information is meaningless
    # when every value is distinct.
    if dtype in ("categorical", "mixed"):
        raw = _sample_categorical(cleaned, max_samples, rng)
    else:
        pool = list(cleaned)
        rng.shuffle(pool)
        raw = pool[:max_samples]

    return _dedupe_preserve_order(raw, max_samples)


def _sample_categorical(cleaned: list[str], max_samples: int, rng: random.Random) -> list[str]:
    """Top-frequency dominants + random tail for rare-label coverage.

    Showing only the top 5 loses long-tail labels; showing a pure random sample
    misses dominant categories on skewed distributions. The hybrid hits both.
    """
    counts = Counter(cleaned[:_MAX_STATS_SAMPLE])
    # Top 5 by frequency is a small fixed budget — the model only needs a few
    # examples to anchor on dominant values.
    top = [v for v, _ in counts.most_common(5)]
    # Remaining budget goes to random tail values (deduplicated against top)
    # so the model sees some rare labels without re-seeing the dominants.
    tail = list(set(cleaned[:_MAX_STATS_SAMPLE]) - set(top))
    rng.shuffle(tail)
    return top + tail[: max(0, max_samples - len(top))]


def _dedupe_preserve_order(values: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
        if len(out) >= limit:
            break
    return out
