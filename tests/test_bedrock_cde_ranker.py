from typing import Any

import pytest
from agent_experiment import GPT_5_6_LUNA, Provider, ReasoningEffort

import src.integrations.bedrock_cde_ranker as ranker_module
from src.integrations.bedrock_cde_ranker import (
    BedrockCandidateRanker,
    BedrockCandidateRankerConfig,
    InvalidRecommendationResponseError,
)


class _ProviderClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.response


def _tool_response(tool_input: object) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "name": "return_cde_matches",
                            "toolUseId": "call-1",
                            "input": tool_input,
                        }
                    }
                ]
            }
        }
    }


@pytest.mark.asyncio
async def test_ranker_uses_bedrock_luna_with_medium_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Bedrock returns one valid structured ranking.
    client = _ProviderClient(_tool_response({
        "closest_matches": [
            {"candidate_index": 2, "rank": 1, "confidence": 0.87},
        ]
    }))
    factory_calls: list[tuple[str, Provider, ReasoningEffort]] = []

    def _client_factory(
        region: str,
        *,
        provider: Provider,
        reasoning_effort: ReasoningEffort,
    ) -> _ProviderClient:
        factory_calls.append((region, provider, reasoning_effort))
        return client

    monkeypatch.setattr(ranker_module, "make_provider_client", _client_factory)
    ranker = BedrockCandidateRanker(BedrockCandidateRankerConfig("us-gov-west-1"))

    # When one source column is ranked.
    matches = await ranker.rank("catalog", "source")

    # Then DataChord uses Luna through Bedrock with no direct OpenAI credential path.
    assert factory_calls == [
        ("us-gov-west-1", Provider.BEDROCK, ReasoningEffort.MEDIUM)
    ]
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request["modelId"] == GPT_5_6_LUNA
    assert request["system"] == [{"text": "catalog"}]
    assert request["messages"] == [
        {"role": "user", "content": [{"text": "source"}]}
    ]
    assert request["toolConfig"]["toolChoice"] == {
        "tool": {"name": "return_cde_matches"}
    }
    schema = request["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert schema["required"] == ["closest_matches"]
    assert schema["$defs"]["PotentialMatchIndex"]["required"] == [
        "candidate_index",
        "confidence",
        "rank",
    ]
    assert matches[0].candidate_index == 2
    assert "api_key" not in request


@pytest.mark.asyncio
async def test_ranker_rejects_a_non_tool_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the provider returns text instead of the required tool result.
    client = _ProviderClient({
        "output": {"message": {"content": [{"text": "candidate 1"}]}}
    })
    monkeypatch.setattr(ranker_module, "make_provider_client", lambda *args, **kwargs: client)
    ranker = BedrockCandidateRanker(BedrockCandidateRankerConfig("us-east-2"))

    # When ranking completes, then malformed output is a provider failure.
    with pytest.raises(InvalidRecommendationResponseError, match="exactly one"):
        await ranker.rank("catalog", "source")


@pytest.mark.asyncio
async def test_ranker_rejects_a_tool_result_without_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the provider calls the correct tool but omits its required result list.
    client = _ProviderClient(_tool_response({}))
    monkeypatch.setattr(ranker_module, "make_provider_client", lambda *args, **kwargs: client)
    ranker = BedrockCandidateRanker(BedrockCandidateRankerConfig("us-east-2"))

    # When the response is parsed, then the omitted field cannot become a silent no-match.
    with pytest.raises(InvalidRecommendationResponseError, match="invalid ranking"):
        await ranker.rank("catalog", "source")
