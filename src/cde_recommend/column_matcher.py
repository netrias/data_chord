"""Evaluate one source column against the CDE catalog.

Routes to a single-prompt ranking when the catalog fits under the chunk
threshold; otherwise chunks, shortlists, aggregates, and re-ranks.
Changes when the per-column matching algorithm changes.
"""

import asyncio
import logging
from collections.abc import Iterator

from src.cde_recommend.candidate_ranker import CandidateRanker
from src.cde_recommend.prompt_builder import build_developer_message, build_user_message
from src.cde_recommend.types import (
    CDE,
    CDEMatch,
    ColumnInput,
    ColumnProfile,
    ColumnResult,
    ColumnType,
    PotentialMatchIndex,
    compute_harmonization,
)

logger = logging.getLogger(__name__)


async def match_column(
    column: ColumnInput,
    profile: ColumnProfile,
    all_cdes: list[CDE],
    *,
    ranker: CandidateRanker,
    semaphore: asyncio.Semaphore,
    developer_message: str,
    final_k: int = 5,
    chunk_threshold: int = 500,
    cde_chunk_size: int = 50,
    per_chunk_k: int = 3,
    max_pv_samples: int = 12,
) -> ColumnResult:
    """Evaluate one column end-to-end. No dtype-based refusal —
    every column reaches the LLM (or the exact-match short-circuit upstream).
    Returned ColumnResult always carries profile.dtype as column_type."""
    user_message = build_user_message(profile)

    # Catalog size drives the strategy. A single prompt is simpler and lets the
    # LLM see every CDE at once, but we cannot fit thousands of CDEs into one
    # context cheaply — so above the threshold we shortlist per chunk then
    # re-rank the aggregated survivors.
    if len(all_cdes) <= chunk_threshold:
        matches = await _match_single_call(
            column_name=column.column_name,
            column_type=profile.dtype,
            all_cdes=all_cdes,
            ranker=ranker,
            semaphore=semaphore,
            developer_message=developer_message,
            user_message=user_message,
            final_k=final_k,
        )
    else:
        matches = await _match_chunked(
            column_name=column.column_name,
            column_type=profile.dtype,
            all_cdes=all_cdes,
            ranker=ranker,
            semaphore=semaphore,
            user_message=user_message,
            final_k=final_k,
            cde_chunk_size=cde_chunk_size,
            per_chunk_k=per_chunk_k,
            max_pv_samples=max_pv_samples,
        )
    return ColumnResult(
        column_name=column.column_name,
        matches=matches,
        column_type=profile.dtype,
    )


async def _match_single_call(
    *,
    column_name: str,
    column_type: ColumnType,
    all_cdes: list[CDE],
    ranker: CandidateRanker,
    semaphore: asyncio.Semaphore,
    developer_message: str,
    user_message: str,
    final_k: int,
) -> list[CDEMatch]:
    """All CDEs in one prompt — default for sets <=500.

    The developer message here is the batch-level stable prefix. Only the user
    message changes per column, so providers can reuse their prefix cache.
    """
    async with semaphore:
        idx_matches = await ranker.rank(
            developer_message=developer_message,
            user_message=user_message,
        )

    return _resolve_indices(idx_matches, all_cdes, column_name, final_k, column_type)


async def _match_chunked(
    *,
    column_name: str,
    column_type: ColumnType,
    all_cdes: list[CDE],
    ranker: CandidateRanker,
    semaphore: asyncio.Semaphore,
    user_message: str,
    final_k: int,
    cde_chunk_size: int,
    per_chunk_k: int,
    max_pv_samples: int,
) -> list[CDEMatch]:
    """Chunk -> shortlist -> aggregate -> final rank. Fallback for >500 CDEs.

    The chunk-level developer messages are *not* the batch-level cached prefix
    (they each contain a different subset of CDEs), so this path pays more per
    call. It exists because large catalogs cannot fit into one prompt cheaply.
    """
    chunks = [all_cdes[i : i + cde_chunk_size] for i in range(0, len(all_cdes), cde_chunk_size)]

    async def _bounded_chunk(chunk: list[CDE]) -> list[CDE]:
        # Each chunk shortlists its top per_chunk_k candidates. per_chunk_k is
        # intentionally smaller than final_k so the aggregate shortlist stays
        # small enough to fit the final reranking prompt.
        async with semaphore:
            chunk_dev_msg = build_developer_message(
                chunk,
                top_k=per_chunk_k,
                max_pv_samples=max_pv_samples,
            )
            idx_matches = await ranker.rank(
                developer_message=chunk_dev_msg,
                user_message=user_message,
            )

        return [chunk[idx] for idx, _ in _iter_valid_matches(idx_matches, len(chunk), column_name)]

    tasks = [asyncio.create_task(_bounded_chunk(chunk)) for chunk in chunks]
    # As in the batch pipeline: one bad chunk shouldn't kill the column.
    chunk_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Dedup by cde_key: a CDE could appear in exactly one chunk (chunks are
    # disjoint by index), so dedup here is defensive against future chunk-
    # overlap strategies rather than load-bearing today.
    seen: set[str] = set()
    aggregated: list[CDE] = []
    for result in chunk_results:
        if isinstance(result, BaseException):
            logger.exception("Chunk matching failed for column %s", column_name, exc_info=result)
            continue
        for cde in result:
            if cde.cde_key not in seen:
                seen.add(cde.cde_key)
                aggregated.append(cde)

    # Every chunk said "no match" (or every chunk failed). Skip the final
    # rerank since there's nothing to rank.
    if not aggregated:
        return []

    # Final pass: rerank the aggregated shortlist from scratch so the model
    # sees all survivors together and can assign globally-consistent ranks
    # and confidences.
    final_dev_msg = build_developer_message(
        aggregated,
        top_k=final_k,
        max_pv_samples=max_pv_samples,
    )

    async with semaphore:
        idx_matches = await ranker.rank(
            developer_message=final_dev_msg,
            user_message=user_message,
        )

    return _resolve_indices(idx_matches, aggregated, column_name, final_k, column_type)


def _resolve_indices(
    index_matches: list[PotentialMatchIndex],
    cdes: list[CDE],
    column_name: str,
    limit: int,
    column_type: ColumnType,
) -> list[CDEMatch]:
    """Convert model-returned indices into CDEMatch records.

    The model returns indices rather than CDE keys specifically to prevent
    hallucinated keys — _iter_valid_matches drops the no-match signals
    (candidate_index=-1, rank=0) and logs out-of-range indices, so this
    function only sees valid hits. ``rank`` on the wire is positional (the
    1-indexed position within the returned list), not a passthrough of the
    LLM's self-reported rank — confidence already owns the score dimension.
    The column_type is threaded in so every resolved match carries harmonization
    computed from the column's dtype and the resolved target's PV presence.
    """
    resolved = [
        CDEMatch(
            cde_id=cdes[idx].cde_id,
            cde_key=cdes[idx].cde_key,
            rank=position,
            confidence=match.confidence,
            harmonization=compute_harmonization(column_type, cdes[idx]),
        )
        for position, (idx, match) in enumerate(
            _iter_valid_matches(index_matches, len(cdes), column_name), start=1
        )
    ]
    # Enforce the output-length contract: rank is positional, so ``limit`` is
    # the only bound on how many matches reach the client. The model's own
    # top_k hint is advisory, not load-bearing, after this normalization.
    return resolved[:limit]


def _iter_valid_matches(
    index_matches: list[PotentialMatchIndex],
    n: int,
    column_name: str,
) -> Iterator[tuple[int, PotentialMatchIndex]]:
    """Yield (index, match) pairs whose candidate_index points into [0, n).

    Owns the no-match and index-validation rules shared by chunk-level
    shortlisting and final resolution. Two no-match signals are dropped
    silently: candidate_index=-1 (the explicit sentinel) and rank=0 (the
    schema convention for "no good match" — see PotentialMatchIndex.rank).
    Out-of-range indices are logged and skipped so one malformed entry
    doesn't drop the column's valid matches. The schema forbids out-of-range
    values, but models occasionally emit them anyway.
    """
    for match in index_matches:
        if match.candidate_index == -1:
            continue
        if match.rank == 0:
            continue
        if 0 <= match.candidate_index < n:
            yield match.candidate_index, match
        else:
            logger.warning(
                "Invalid index %d (max %d) for column %s",
                match.candidate_index,
                n - 1,
                column_name,
            )
