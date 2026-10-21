from __future__ import annotations

import json
import urllib.error
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import Message
from typing import Any

import pytest

from src.domain.cde import CdeType
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import (
    ReferenceDataCorruptError,
    ReferenceDataUnavailableError,
    ReferenceModelNotFoundError,
)
from src.integrations.data_model_store_api import DataModelStoreApiRepository

BASE = "https://example.invalid/staging"
PREFIX = "/data-models/demo/versions/1.0"


@dataclass
class FakeTransport:
    """Serves canned bodies by path, and records what was asked for."""

    routes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    requested: list[str] = field(default_factory=list)
    raise_for: dict[str, Exception] = field(default_factory=dict)
    body_override: bytes | None = None

    def get(self, url: str, headers: Mapping[str, str], timeout: int) -> bytes:
        del timeout
        assert headers["x-api-key"] == "secret"
        path = url.removeprefix(BASE)
        self.requested.append(path)
        bare = path.split("?")[0]
        for pattern, error in self.raise_for.items():
            if bare == pattern:
                raise error
        if self.body_override is not None:
            return self.body_override
        pages = self.routes.get(bare)
        if pages is None:
            raise _http_error(404, "Not Found")
        offset = _offset(path)
        for page in pages:
            if int(page.get("offset", 0)) == offset:
                return json.dumps(page).encode()
        raise AssertionError(f"no canned page for offset {offset} of {bare}")


def _http_error(code: int, reason: str) -> urllib.error.HTTPError:
    """HTTPError wants a real email Message for headers; tests do not care."""
    return urllib.error.HTTPError(BASE, code, reason, Message(), None)


def _offset(path: str) -> int:
    if "offset=" not in path:
        return 0
    return int(path.split("offset=")[1].split("&")[0])


def _cde(key: str, node: str, description: str | None = "A column", cde_id: int = 1) -> dict[str, Any]:
    return {
        "cde_id": cde_id,
        "cde_key": key,
        "node": node,
        "is_key": False,
        "is_required": True,
        "data_type": "value_set",
        "cde_description": description,
    }


def _pv(cde_key: str, value: str) -> dict[str, Any]:
    return {"cde_id": 1, "cde_key": cde_key, "pv_value": value, "is_active": True}


def _repository(transport: FakeTransport) -> DataModelStoreApiRepository:
    return DataModelStoreApiRepository(BASE, "secret", transport)


def _models_page() -> dict[str, Any]:
    return {
        "total": 1,
        "offset": 0,
        "items": [
            {
                "key": "demo",
                "name": "Demo Model",
                "versions": [{"external_version_number": "1.0"}],
            }
        ],
    }


def _transport(
    cdes: list[dict[str, Any]], pvs: list[dict[str, Any]], *, page_size: int | None = None
) -> FakeTransport:
    if page_size is None:
        pv_pages = [{"offset": 0, "total_pvs": len(pvs), "items": pvs}]
    else:
        pv_pages = []
        for start in range(0, max(len(pvs), 1), page_size):
            pv_pages.append(
                {"offset": start, "total_pvs": len(pvs), "items": pvs[start : start + page_size]}
            )
    return FakeTransport(
        routes={
            "/data-models": [_models_page()],
            f"{PREFIX}/cdes": [{"offset": 0, "total": len(cdes), "items": cdes}],
            f"{PREFIX}/pvs": pv_pages,
        }
    )


def _version() -> DataModelVersionReference:
    return DataModelVersionReference(data_model_key="demo", external_version_number="1.0")


def test_models_and_versions_are_listed() -> None:
    # Given a store holding one model with one version.
    repository = _repository(_transport([], []))

    # When the catalog is listed.
    summaries = repository.list_models()

    # Then the model, its label and its versions all survive the mapping.
    assert len(summaries) == 1
    assert summaries[0].data_model_key == "demo"
    assert summaries[0].label == "Demo Model"
    assert [v.external_version_number for v in summaries[0].versions] == ["1.0"]


def test_a_model_without_a_name_falls_back_to_its_key() -> None:
    # Given a model the store has no name for.
    transport = _transport([], [])
    transport.routes["/data-models"] = [
        {"items": [{"key": "demo", "name": None, "versions": []}]}
    ]

    # When the catalog is listed.
    # Then the key stands in, because the domain requires a non-blank label.
    assert _repository(transport).list_models()[0].label == "demo"


def test_a_cde_under_several_nodes_becomes_one_entry() -> None:
    # Given one CDE key used by two nodes, which the store returns twice.
    cdes = [_cde("shared", "participant"), _cde("shared", "sample"), _cde("solo", "participant")]
    repository = _repository(_transport(cdes, [_pv("shared", "Open")]))

    # When the model is loaded.
    model = repository.load_model(_version())

    # Then it appears once, because ReferenceModel forbids duplicate keys.
    assert sorted(cde.cde_key for cde in model.catalog) == ["shared", "solo"]
    assert len(model.catalog) == 2


def test_collapsing_records_which_keys_were_shared() -> None:
    # Given one shared key and one that is not.
    cdes = [_cde("shared", "participant"), _cde("shared", "sample"), _cde("solo", "participant")]
    repository = _repository(_transport(cdes, []))

    # When the model is loaded.
    repository.load_model(_version())

    # Then only the shared one is reported, so the collapse is not silent.
    assert [p.cde_key for p in repository.last_collapsed] == ["shared"]
    assert repository.last_collapsed[0].node_count == 2


def test_differing_descriptions_across_nodes_are_recorded() -> None:
    # Given two nodes describing the same key differently, as PDC does twelve times.
    cdes = [
        _cde("shared", "participant", description="From participant"),
        _cde("shared", "sample", description="From sample"),
    ]
    repository = _repository(_transport(cdes, []))

    # When the model is loaded.
    model = repository.load_model(_version())

    # Then the first wins, and both are reported so the loss is visible.
    assert model.catalog.get("shared").description == "From participant"  # pyright: ignore[reportOptionalMemberAccess]
    assert repository.last_collapsed[0].descriptions == ("From participant", "From sample")


def test_cde_type_follows_whether_the_cde_has_values() -> None:
    # Given one CDE with a value set and one without.
    cdes = [_cde("with_values", "participant"), _cde("without_values", "participant")]
    repository = _repository(_transport(cdes, [_pv("with_values", "Open")]))

    # When the model is loaded.
    model = repository.load_model(_version())

    # Then the type comes from the values, not the store's data_type, which the
    # domain does not model.
    assert model.catalog.get("with_values").cde_type is CdeType.PV  # pyright: ignore[reportOptionalMemberAccess]
    assert model.catalog.get("without_values").cde_type is CdeType.PASSTHROUGH  # pyright: ignore[reportOptionalMemberAccess]


def test_every_cde_gets_a_value_set_even_when_empty() -> None:
    # Given one CDE the store returns no values for.
    cdes = [_cde("with_values", "participant"), _cde("without_values", "participant")]
    repository = _repository(_transport(cdes, [_pv("with_values", "Open")]))

    # When the model is loaded.
    model = repository.load_model(_version())

    # Then it still gets an entry, because ReferenceModel requires the two key
    # sets to match exactly.
    assert set(model.pvs.values) == {"with_values", "without_values"}
    assert model.pvs.get("without_values") == frozenset()


def test_values_shared_by_several_nodes_are_not_duplicated() -> None:
    # Given a key used by two nodes, whose values the store keeps once.
    cdes = [_cde("shared", "participant"), _cde("shared", "sample")]
    repository = _repository(_transport(cdes, [_pv("shared", "Open"), _pv("shared", "Closed")]))

    # When the model is loaded.
    model = repository.load_model(_version())

    # Then each value appears once.
    assert model.pvs.get("shared") == frozenset({"Open", "Closed"})


def test_values_are_fetched_across_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given five values and a page size of two.
    monkeypatch.setattr("src.integrations.data_model_store_api._PAGE_SIZE", 2)
    pvs = [_pv("colour", name) for name in ("red", "green", "blue", "black", "white")]
    transport = _transport([_cde("colour", "participant")], pvs, page_size=2)
    repository = _repository(transport)

    # When the model is loaded.
    model = repository.load_model(_version())

    # Then all five arrive across three requests, because the store answers a
    # too-large request with 413 rather than a short list.
    assert model.pvs.get("colour") == frozenset({"red", "green", "blue", "black", "white"})
    assert sum(1 for path in transport.requested if path.startswith(f"{PREFIX}/pvs")) == 3


def test_values_are_carried_through_character_for_character() -> None:
    # Given values differing only by case, whitespace, punctuation or diacritic.
    values = [
        "Lung Cancer",
        "lung cancer",
        "Lung Cancer ",
        "Lung  Cancer",
        "Lung-Cancer",
        "café",
        "cafe",
    ]
    transport = _transport(
        [_cde("diagnosis", "participant")], [_pv("diagnosis", value) for value in values]
    )

    # When the model is loaded.
    model = _repository(transport).load_model(_version())

    # Then every one survives, because conformance is exact equality against an
    # ontology term and normalising here would change what conforms.
    assert model.pvs.get("diagnosis") == frozenset(values)


def test_a_blank_value_is_skipped() -> None:
    # Given a CDE row carrying no value, which is how a value-less CDE arrives.
    transport = _transport([_cde("colour", "participant")], [_pv("colour", ""), _pv("colour", "red")])

    # When the model is loaded.
    model = _repository(transport).load_model(_version())

    # Then the blank does not become a permissible value.
    assert model.pvs.get("colour") == frozenset({"red"})


def test_an_unknown_version_is_not_found() -> None:
    # Given the store does not publish the requested version.
    transport = _transport([_cde("colour", "participant")], [])
    transport.raise_for[f"{PREFIX}/cdes"] = _http_error(404, "Not Found")

    # When the model is loaded.
    # Then it is not-found rather than unavailable, so a caller can tell the
    # difference between a missing model and a broken store.
    with pytest.raises(ReferenceModelNotFoundError):
        _repository(transport).load_model(_version())


def test_a_server_error_is_unavailable_rather_than_missing() -> None:
    # Given the store fails rather than answering.
    transport = _transport([], [])
    transport.raise_for["/data-models"] = _http_error(500, "Server Error")

    # When the catalog is listed.
    # Then it is unavailable, not a missing model.
    with pytest.raises(ReferenceDataUnavailableError):
        _repository(transport).list_models()


def test_an_unreachable_store_is_unavailable() -> None:
    # Given the store cannot be reached at all.
    transport = _transport([], [])
    transport.raise_for["/data-models"] = urllib.error.URLError("no route")

    # When the catalog is listed.
    # Then a network failure reads the same as a service failure.
    with pytest.raises(ReferenceDataUnavailableError):
        _repository(transport).list_models()


def test_a_non_json_body_is_corrupt() -> None:
    # Given a proxy answers with HTML instead of the expected JSON.
    transport = _transport([], [])
    transport.body_override = b"<html>gateway timeout</html>"

    # When the catalog is listed.
    # Then it is corrupt, not unavailable; the store answered, just not usably.
    with pytest.raises(ReferenceDataCorruptError):
        _repository(transport).list_models()


def test_a_cde_without_a_key_is_corrupt() -> None:
    # Given a CDE the store returns no key for.
    transport = _transport([_cde("", "participant")], [])

    # When the model is loaded.
    # Then it fails here rather than building a model with a blank key.
    with pytest.raises(ReferenceDataCorruptError):
        _repository(transport).load_model(_version())


def test_a_model_without_a_key_is_corrupt() -> None:
    # Given a model whose key is only whitespace.
    transport = _transport([], [])
    transport.routes["/data-models"] = [{"items": [{"key": "  ", "versions": []}]}]

    # When the catalog is listed.
    # Then it fails, because the key identifies the model in every later call.
    with pytest.raises(ReferenceDataCorruptError):
        _repository(transport).list_models()


def test_a_blank_base_url_or_key_is_rejected() -> None:
    # Given a blank URL, then a blank key.
    # When the repository is constructed.
    # Then it refuses, so misconfiguration surfaces here and not as a 403 later.
    with pytest.raises(ValueError, match="base_url"):
        DataModelStoreApiRepository("  ", "secret")
    with pytest.raises(ValueError, match="api_key"):
        DataModelStoreApiRepository(BASE, "  ")
