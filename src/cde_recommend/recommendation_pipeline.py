"""Top-level recommendation pipeline for a batch of source columns.

Stages: result-cache lookup → column profiling → exact-match short-circuit →
bounded async fan-out to the column matcher → cache write. Order-preserving;
failed columns return empty matches. Changes when the pipeline's staging or
orchestration rules change.
"""

import asyncio
import logging
import re
from dataclasses import dataclass

from src.cde_recommend.candidate_ranker import CandidateRanker
from src.cde_recommend.column_matcher import match_column
from src.cde_recommend.profiler import profile_column
from src.cde_recommend.prompt_builder import build_developer_message
from src.cde_recommend.result_cache import RecommendationCache, compute_cache_key
from src.cde_recommend.types import (
    CDE,
    CDEMatch,
    ColumnError,
    ColumnInput,
    ColumnProfile,
    ColumnResult,
    ColumnType,
    compute_harmonization,
)

logger = logging.getLogger(__name__)

# Disposition labels — which pipeline stage actually resolved each column.
# Emitted in the end-of-batch summary log so operators can tell at a glance
# whether a column was decided by cache, the name short-circuit, or the LLM,
# and in the LLM case whether the model returned a match at all. Kept as
# module-level constants so the summary renderer and the stage code share one
# source of truth for the label strings.
_DISP_CACHE_HIT = "cache_hit"
_DISP_EXACT_MATCH = "exact_match"
_DISP_LLM_MATCH = "llm_match"
_DISP_LLM_NO_MATCH = "llm_no_match"
_DISP_ERROR = "error"


@dataclass
class _BatchColumn:
    source: ColumnInput
    cache_key: str
    profile: ColumnProfile | None
    result: ColumnResult | None
    disposition: str | None


@dataclass(frozen=True)
class _MatchingSettings:
    top_k: int
    max_pv_samples: int
    chunk_threshold: int
    cde_chunk_size: int
    per_chunk_k: int
    concurrency: int


async def match_columns_batch(
    columns: list[ColumnInput],
    all_cdes: list[CDE],
    *,
    ranker: CandidateRanker,
    cache: RecommendationCache,
    data_model_key: str,
    catalog_revision: str,
    # Concurrency bounds simultaneous model requests while letting a single
    # batch fan out without queueing on the client side.
    concurrency: int = 50,
    top_k: int = 5,
    # 12 PV samples per CDE is the sweet spot where the developer message
    # still fits comfortably in the cached prefix for catalogs up to ~500
    # CDEs; see prompt_builder for the rendering.
    max_pv_samples: int = 12,
    # Above this catalog size we switch from single-call to chunked shortlisting
    # — see column_matcher for the algorithm and rationale.
    chunk_threshold: int = 500,
    cde_chunk_size: int = 50,
    per_chunk_k: int = 3,
) -> list[ColumnResult]:
    if not 1 <= top_k <= 10:
        raise ValueError("top_k must be between 1 and 10")
    batch = await _prepare_batch(columns, cache, data_model_key, catalog_revision, top_k)
    _match_exact_columns(batch, all_cdes)
    settings = _MatchingSettings(top_k, max_pv_samples, chunk_threshold, cde_chunk_size, per_chunk_k, concurrency)
    await _match_unresolved_columns(batch, all_cdes, ranker, cache, settings)
    _log_dispositions(batch)
    return [column.result for column in batch if column.result is not None]


async def _prepare_batch(
    columns: list[ColumnInput],
    cache: RecommendationCache,
    data_model_key: str,
    catalog_revision: str,
    top_k: int,
) -> list[_BatchColumn]:
    keys = [
        compute_cache_key(data_model_key, catalog_revision, column.column_name, column.column_values, top_k=top_k)
        for column in columns
    ]
    cached = await _load_cached_results(cache, keys)
    return [_prepare_column(column, key, cached.get(key)) for column, key in zip(columns, keys, strict=True)]


async def _load_cached_results(cache: RecommendationCache, keys: list[str]) -> dict[str, ColumnResult]:
    try:
        return await cache.load_many(keys)
    except Exception:
        logger.warning("Recommendation cache read failed; using the model", exc_info=True)
        return {}


def _prepare_column(column: ColumnInput, key: str, cached: ColumnResult | None) -> _BatchColumn:
    return _BatchColumn(
        source=column,
        cache_key=key,
        profile=profile_column(column.column_name, column.column_values) if cached is None else None,
        result=cached,
        disposition=_DISP_CACHE_HIT if cached is not None else None,
    )


def _match_exact_columns(batch: list[_BatchColumn], all_cdes: list[CDE]) -> None:
    by_name = {_normalize_key(cde.cde_key): cde for cde in all_cdes}
    for column in batch:
        if column.result is not None:
            continue
        cde = by_name.get(_normalize_key(column.source.column_name))
        if cde is not None:
            assert column.profile is not None
            column.result = _exact_match_result(column.source.column_name, cde, column.profile.dtype)
            column.disposition = _DISP_EXACT_MATCH


async def _match_unresolved_columns(
    batch: list[_BatchColumn],
    all_cdes: list[CDE],
    ranker: CandidateRanker,
    cache: RecommendationCache,
    settings: _MatchingSettings,
) -> None:
    unresolved = [column for column in batch if column.result is None]
    if not unresolved:
        return
    message = build_developer_message(all_cdes, top_k=settings.top_k, max_pv_samples=settings.max_pv_samples)
    semaphore = asyncio.Semaphore(settings.concurrency)
    tasks = [
        asyncio.create_task(_match_profiled_column(column, all_cdes, ranker, semaphore, message, settings))
        for column in unresolved
    ]
    # One failed column must not cancel successful siblings or change input order.
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    successful = _merge_matches(unresolved, outcomes)
    await _save_new_results(cache, successful)


async def _match_profiled_column(
    column: _BatchColumn,
    all_cdes: list[CDE],
    ranker: CandidateRanker,
    semaphore: asyncio.Semaphore,
    message: str,
    settings: _MatchingSettings,
) -> ColumnResult:
    assert column.profile is not None
    return await match_column(
        column=column.source,
        profile=column.profile,
        all_cdes=all_cdes,
        ranker=ranker,
        semaphore=semaphore,
        developer_message=message,
        final_k=settings.top_k,
        chunk_threshold=settings.chunk_threshold,
        cde_chunk_size=settings.cde_chunk_size,
        per_chunk_k=settings.per_chunk_k,
        max_pv_samples=settings.max_pv_samples,
    )


def _merge_matches(
    columns: list[_BatchColumn],
    outcomes: list[ColumnResult | BaseException],
) -> list[_BatchColumn]:
    successful: list[_BatchColumn] = []
    for column, outcome in zip(columns, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            column.result = _failed_column_result(column, outcome)
            column.disposition = _DISP_ERROR
        else:
            column.result = outcome
            column.disposition = _DISP_LLM_MATCH if outcome.matches else _DISP_LLM_NO_MATCH
            successful.append(column)
    return successful


def _failed_column_result(column: _BatchColumn, error: BaseException) -> ColumnResult:
    logger.exception("Matching failed for column %s", column.source.column_name, exc_info=error)
    return ColumnResult(
        column_name=column.source.column_name,
        matches=[],
        column_type=column.profile.dtype if column.profile is not None else "mixed",
        error=_column_error_for_exception(error),
    )


async def _save_new_results(cache: RecommendationCache, columns: list[_BatchColumn]) -> None:
    if not columns:
        return
    try:
        # Adapt the batch's named state to the existing cache boundary.
        await cache.save_many([(column.cache_key, column.result) for column in columns if column.result is not None])
    except Exception:
        logger.warning("Recommendation cache write failed; result remains usable", exc_info=True)


def _normalize_key(name: str) -> str:
    """CDE key equivalence rule: 'Age at Diagnosis' ↔ 'age_at_diagnosis'.

    The LLM matching layer has its own semantic-similarity logic; this
    function only detects the trivial case where a source column's header is
    literally the CDE key modulo case, whitespace, and separator differences.
    Anything subtler (synonyms, abbreviations, plurals) falls through to the
    LLM as intended.
    """
    normalized = name.lower().strip()
    # Collapse any run of whitespace or hyphens into a single underscore, then
    # collapse consecutive underscores — handles messy inputs like
    # "Age at  Diagnosis" and "age__at__diagnosis".
    normalized = re.sub(r"[\s\-]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_")


def _log_dispositions(batch: list[_BatchColumn]) -> None:
    """Emit one ordered summary naming the mechanism that resolved each column."""
    if not batch:
        return
    width = max(len(column.source.column_name) for column in batch)
    header = f"Column disposition summary ({len(batch)} columns):"
    rows = [
        f"  {column.source.column_name.ljust(width)}  "
        f"{(column.disposition or 'unresolved'):<13}  -> {_top_match_label(column.result)}"
        for column in batch
    ]
    logger.info("\n".join([header, *rows]))


def _top_match_label(result: ColumnResult | None) -> str:
    if result is None or not result.matches:
        return "-"
    return result.matches[0].cde_key


def _exact_match_result(column_name: str, cde: CDE, column_type: ColumnType) -> ColumnResult:
    """Exact-match short-circuit result. Caller passes the profiler's dtype
    so the exact-match path populates column_type consistently with LLM results."""
    # Confidence 1.0 and rank 1: the normalized-key match is by construction
    # the best possible outcome, so no need for the LLM to re-score it.
    return ColumnResult(
        column_name=column_name,
        matches=[
            CDEMatch(
                cde_id=cde.cde_id,
                cde_key=cde.cde_key,
                rank=1,
                confidence=1.0,
                harmonization=compute_harmonization(column_type, cde),
            )
        ],
        column_type=column_type,
    )


def _column_error_for_exception(exc: BaseException) -> ColumnError:
    if _is_rate_limit_error(exc):
        return ColumnError(
            code="rate_limited",
            message="Mapping was rate limited after retries. Please review or retry.",
        )
    return ColumnError(
        code="matching_failed",
        message="Mapping failed for this column. Please review or retry.",
    )


def _is_rate_limit_error(exc: BaseException) -> bool:
    return (
        getattr(exc, "status_code", None) == 429
        or exc.__class__.__name__ == "RateLimitError"
        or getattr(exc, "code", None) == "provider_rate_limited"
    )
