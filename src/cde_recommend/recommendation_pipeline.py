"""Top-level recommendation pipeline for a batch of source columns.

Stages: result-cache lookup → column profiling → exact-match short-circuit →
bounded async fan-out to the column matcher → cache write. Order-preserving;
failed columns return empty matches. Changes when the pipeline's staging or
orchestration rules change.
"""

import asyncio
import logging
import re

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


async def match_columns_batch(  # noqa: C901 - keep the five pipeline stages together
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

    # Stage 1: precompute every cache key up front so batch_get_item only
    # hits DynamoDB once instead of once per column.
    cache_keys = [
        compute_cache_key(
            data_model_key,
            catalog_revision,
            column.column_name,
            column.column_values,
            top_k=top_k,
        )
        for column in columns
    ]

    try:
        cached = await cache.load_many(cache_keys)
    except Exception:
        logger.warning("Recommendation cache read failed; using the model", exc_info=True)
        cached = {}
    # Results is positionally aligned with `columns` from the start; cached hits
    # populate in place and misses stay None until resolved. This lets the
    # final return preserve input order without a second sort.
    results: list[ColumnResult | None] = [cached.get(cache_key) for cache_key in cache_keys]
    # Dispositions track which stage resolved each column, parallel to
    # `results`/`columns`. Populated as stages fire so the end-of-batch summary
    # can name the mechanism for every input.
    dispositions: list[str | None] = [
        _DISP_CACHE_HIT if result is not None else None for result in results
    ]

    # Stage 2: profile each cache-miss column once up front. Both the exact-match
    # path and the LLM path need the dtype to populate column_type on their
    # ColumnResult, so we compute it here rather than inside each path separately.
    # Cache hits already carry column_type; only misses need profiling.
    profiles: list[ColumnProfile | None] = [None] * len(columns)
    for index, column in enumerate(columns):
        if results[index] is None:
            profiles[index] = profile_column(column.column_name, column.column_values)

    # Stage 3: exact-match short-circuit. If a source column name normalizes to
    # a CDE key (e.g. "Age at Diagnosis" ↔ "age_at_diagnosis"), skip the LLM
    # entirely — there is no ambiguity to resolve.
    cde_by_normalized_key = {_normalize_key(cde.cde_key): cde for cde in all_cdes}
    for index, column in enumerate(columns):
        if results[index] is not None:
            continue  # already resolved via cache
        matched_cde = cde_by_normalized_key.get(_normalize_key(column.column_name))
        if matched_cde is not None:
            profile = profiles[index]
            assert profile is not None  # profiled in stage 2 for all cache misses
            results[index] = _exact_match_result(column.column_name, matched_cde, profile.dtype)
            dispositions[index] = _DISP_EXACT_MATCH

    # Stage 4: LLM fan-out for anything still unresolved. Nothing left to do
    # if every column was satisfied by cache or exact match.
    miss_indices = [index for index, result in enumerate(results) if result is None]
    if not miss_indices:
        _log_dispositions(columns, dispositions, results)
        return [result for result in results if result is not None]

    # Build the developer message once and share it across every column in the
    # batch. Providers can cache the stable prefix, so per-column calls only
    # change the user message (the column profile).
    developer_message = build_developer_message(
        all_cdes,
        top_k=top_k,
        max_pv_samples=max_pv_samples,
    )
    # One semaphore shared across all coroutines bounds total in-flight model
    # calls. Chunked matches within a single column also acquire from this
    # pool, so a single column with many chunks cannot starve the others.
    semaphore = asyncio.Semaphore(concurrency)

    async def _process_column(index: int) -> ColumnResult:
        profile = profiles[index]
        assert profile is not None  # profiled in stage 2 for all cache misses
        return await match_column(
            column=columns[index],
            profile=profile,
            all_cdes=all_cdes,
            ranker=ranker,
            semaphore=semaphore,
            developer_message=developer_message,
            final_k=top_k,
            chunk_threshold=chunk_threshold,
            cde_chunk_size=cde_chunk_size,
            per_chunk_k=per_chunk_k,
            max_pv_samples=max_pv_samples,
        )

    tasks = [asyncio.create_task(_process_column(index)) for index in miss_indices]
    # return_exceptions=True: a single column's failure (rate limit, malformed
    # model output) must not take down the whole batch. Failed columns stay in
    # the response as empty-match results so array-position consumers keep
    # request/response parity.
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Stage 5: merge fan-out results back into the aligned list and stage new
    # cache entries. Only successful outcomes get cached — we don't want to
    # memoize a transient failure for 30 days.
    new_entries: list[tuple[str, ColumnResult]] = []
    for position, task_result in enumerate(task_results):
        index = miss_indices[position]
        if isinstance(task_result, BaseException):
            logger.exception(
                "Matching failed for column %s",
                columns[index].column_name,
                exc_info=task_result,
            )
            profile = profiles[index]
            column_type: ColumnType = profile.dtype if profile is not None else "mixed"
            results[index] = ColumnResult(
                column_name=columns[index].column_name,
                matches=[],
                column_type=column_type,
                error=_column_error_for_exception(task_result),
            )
            dispositions[index] = _DISP_ERROR
            continue
        results[index] = task_result
        dispositions[index] = _DISP_LLM_MATCH if task_result.matches else _DISP_LLM_NO_MATCH
        new_entries.append((cache_keys[index], task_result))

    if new_entries:
        try:
            await cache.save_many(new_entries)
        except Exception:
            logger.warning(
                "Recommendation cache write failed; result remains usable",
                exc_info=True,
            )

    _log_dispositions(columns, dispositions, results)
    return [result for result in results if result is not None]


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


def _log_dispositions(
    columns: list[ColumnInput],
    dispositions: list[str | None],
    results: list[ColumnResult | None],
) -> None:
    """Emit one end-of-batch summary naming the mechanism that resolved each column.

    Rendered as a single newline-joined log record so CloudWatch keeps the
    column-by-column table intact instead of interleaving rows across
    concurrent requests. Top-match cde_key is included when present so the
    summary alone answers "why did column X become CDE Y?".
    """
    if not columns:
        return
    name_width = max(len(column.column_name) for column in columns)
    header = f"Column disposition summary ({len(columns)} columns):"
    rows = [
        f"  {column.column_name.ljust(name_width)}  "
        f"{(disposition or 'unresolved'):<13}  -> {_top_match_label(result)}"
        for column, disposition, result in zip(columns, dispositions, results, strict=True)
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
