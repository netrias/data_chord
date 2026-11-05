from __future__ import annotations

import httpx
import pytest
from netrias_client import DataModelStoreError

from src.integrations import data_model_store
from src.integrations.data_model_store import fetch_nodes_and_placements_async


class _Client:
    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
        assert headers == {"x-api-key": "test-key"}
        if url.endswith("/nodes"):
            return httpx.Response(200, json={"total": 3, "items": [
                {"node": "diagnosis", "cde_count": 1},
                {"node": "sample", "cde_count": 1},
                {"node": "unused", "cde_count": 0},
            ]})
        assert url.endswith("/cdes?include_description=true")
        return httpx.Response(200, json={"total": 2, "items": [
            {"node": "diagnosis", "cde_key": "stage", "cde_id": 2,
             "cde_description": "Stage", "data_type": "string", "is_required": True, "is_key": True},
            {"node": "sample", "cde_key": "stage", "cde_id": 7,
             "cde_description": "Stage", "data_type": "enum", "is_required": False, "is_key": False},
        ]})


@pytest.mark.asyncio
async def test_fetch_preserves_node_cde_pairs_and_empty_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(data_model_store, "_data_model_store_config", lambda _client:
                        data_model_store._DataModelStoreConfig("https://example.test", "test-key", 5))
    monkeypatch.setattr(data_model_store.httpx, "AsyncClient", lambda **_kwargs: _Client())

    nodes, placements = await fetch_nodes_and_placements_async(object(), "model", "1")

    assert nodes == ("diagnosis", "sample", "unused")
    assert [(item.node, item.cde_key, item.cde_id, item.data_type, item.is_required, item.is_key)
            for item in placements] == [
        ("diagnosis", "stage", 2, "string", True, True),
        ("sample", "stage", 7, "enum", False, False),
    ]


@pytest.mark.asyncio
async def test_fetch_rejects_invalid_is_key(monkeypatch: pytest.MonkeyPatch) -> None:
    class InvalidKeyClient(_Client):
        async def get(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
            response = await super().get(url, headers=headers)
            if "/cdes?" in url:
                body = response.json()
                body["items"][0]["is_key"] = "true"
                return httpx.Response(200, json=body)
            return response

    monkeypatch.setattr(data_model_store, "_data_model_store_config", lambda _client:
                        data_model_store._DataModelStoreConfig("https://example.test", "test-key", 5))
    monkeypatch.setattr(data_model_store.httpx, "AsyncClient", lambda **_kwargs: InvalidKeyClient())
    with pytest.raises(DataModelStoreError, match="unexpected CDE response item"):
        await fetch_nodes_and_placements_async(object(), "model", "1")


@pytest.mark.asyncio
async def test_fetch_rejects_source_node_count_disagreement(monkeypatch: pytest.MonkeyPatch) -> None:
    class WrongCountClient(_Client):
        async def get(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
            response = await super().get(url, headers=headers)
            if url.endswith("/nodes"):
                body = response.json()
                body["items"][0]["cde_count"] = 9
                return httpx.Response(200, json=body)
            return response

    monkeypatch.setattr(data_model_store, "_data_model_store_config", lambda _client:
                        data_model_store._DataModelStoreConfig("https://example.test", "test-key", 5))
    monkeypatch.setattr(data_model_store.httpx, "AsyncClient", lambda **_kwargs: WrongCountClient())
    with pytest.raises(DataModelStoreError, match="CDE count does not match node: diagnosis"):
        await fetch_nodes_and_placements_async(object(), "model", "1")
