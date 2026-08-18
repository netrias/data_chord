"""Bedrock Luna adapter for the model-neutral CDE ranking boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import local
from typing import Any

from agent_experiment import (
    GPT_5_6_LUNA,
    ConverseClient,
    Model,
    Provider,
    ReasoningEffort,
    make_provider_client,
)
from cde_recommend.openai_response_format import CLOSEST_MATCHES_SCHEMA
from cde_recommend.types import ClosestMatchesIndex, PotentialMatchIndex

_TOOL_NAME = "return_cde_matches"
_MAX_OUTPUT_TOKENS = 4096


class InvalidRecommendationResponseError(RuntimeError):
    """The provider did not return the required ranking tool call."""


@dataclass(frozen=True)
class BedrockCandidateRankerConfig:
    region: str
    model: Model = GPT_5_6_LUNA
    reasoning_effort: ReasoningEffort = ReasoningEffort.MEDIUM

    def __post_init__(self) -> None:
        if not self.region.strip():
            raise ValueError("CDE recommendation requires an AWS region")


class _WorkerState(local):
    client: ConverseClient | None = None


class BedrockCandidateRanker:
    """Rank CDE candidates through Luna hosted by AWS Bedrock."""

    def __init__(self, config: BedrockCandidateRankerConfig) -> None:
        self._config = config
        self._worker_state = _WorkerState()

    async def rank(
        self,
        developer_message: str,
        user_message: str,
    ) -> list[PotentialMatchIndex]:
        return await asyncio.to_thread(
            self._rank,
            developer_message,
            user_message,
        )

    def _rank(
        self,
        developer_message: str,
        user_message: str,
    ) -> list[PotentialMatchIndex]:
        response = self._provider_client().converse(
            modelId=self._config.model,
            system=[{"text": developer_message}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"temperature": 0, "maxTokens": _MAX_OUTPUT_TOKENS},
            toolConfig=_tool_config(),
        )
        return _matches_from_response(response)

    def _provider_client(self) -> ConverseClient:
        client = self._worker_state.client
        if client is None:
            client = make_provider_client(
                self._config.region,
                provider=Provider.BEDROCK,
                reasoning_effort=self._config.reasoning_effort,
            )
            self._worker_state.client = client
        return client


def _tool_config() -> dict[str, Any]:
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": _TOOL_NAME,
                    "description": "Return the ranked CDE candidate indices.",
                    "strict": True,
                    "inputSchema": {"json": CLOSEST_MATCHES_SCHEMA},
                }
            }
        ],
        "toolChoice": {"tool": {"name": _TOOL_NAME}},
    }


def _matches_from_response(response: dict[str, Any]) -> list[PotentialMatchIndex]:
    content = response.get("output", {}).get("message", {}).get("content", [])
    tool_calls = [block["toolUse"] for block in content if isinstance(block, dict) and "toolUse" in block]
    if len(tool_calls) != 1 or tool_calls[0].get("name") != _TOOL_NAME:
        raise InvalidRecommendationResponseError(
            "CDE recommendation must return exactly one ranking tool call"
        )
    raw_input = tool_calls[0].get("input")
    if not isinstance(raw_input, dict) or "closest_matches" not in raw_input:
        raise InvalidRecommendationResponseError(
            "CDE recommendation returned an invalid ranking"
        )
    try:
        ranking = ClosestMatchesIndex.model_validate(raw_input)
    except ValueError as exc:
        raise InvalidRecommendationResponseError(
            "CDE recommendation returned an invalid ranking"
        ) from exc
    return ranking.closest_matches


__all__ = [
    "BedrockCandidateRanker",
    "BedrockCandidateRankerConfig",
    "InvalidRecommendationResponseError",
]
