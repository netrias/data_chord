"""Concurrent changes to mapping plans, review decisions and exported revisions."""

import asyncio
import csv
import io
import json
import threading
import zipfile
from datetime import UTC, datetime

import httpx
import pytest

import src.app.dependencies as deps
import src.stage_4_review_results.use_cases as review
from src.domain.harmonization import MatchFidelity
from src.integrations.harmonize import FileHarmonizationService, TermHarmonizationResponse
from src.persistence.review_override_store import load_review_overrides_record
from src.storage import WorkflowFile
from tests.conftest import create_csv_content, review_state_payload, upload_content
from tests.review_package import ReviewPackage

pytestmark = pytest.mark.asyncio


class ClinicalProvider:
    def harmonize(self, requests):
        values = {"primary_diagnosis": "Lung Cancer", "therapeutic_agents": "Aspirin"}
        return tuple(TermHarmonizationResponse(values[r.cde], MatchFidelity.STRONG) for r in requests)


async def _analysis(client, file_id):
    return await client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "test-data-model", "external_version_number": "11.0.4"},
    )


async def _upload(client):
    file_id = await upload_content(client, create_csv_content([["code"], ["X"]]), "concurrency.csv")
    response = await _analysis(client, file_id)
    assert response.status_code == 200, response.text
    return file_id


async def _choose(client, file_id, name, target="primary_diagnosis"):
    return await client.post(
        "/stage-2/choices",
        json={"file_id": file_id, "manual_overrides": {"col_0000": target}, "column_renames": {"col_0000": name}},
    )


async def _run(client, file_id, plan_version=None):
    payload = {"file_id": file_id}
    if plan_version:
        payload["plan_version"] = plan_version
    response = await client.post("/stage-3/harmonize", json=payload)
    if response.status_code != 200:
        return response
    task = response.json()
    async with asyncio.timeout(8):
        while task["status"] == "queued":
            await asyncio.sleep(0.01)
            reply = await client.get(f"/stage-3/jobs/{task['job_id']}", params={"file_id": file_id})
            assert reply.status_code == 200
            task = reply.json()
    assert task["status"] == "succeeded", task
    return response


async def _export(client, file_id):
    response = await client.post("/stage-5/download", json={"file_id": file_id})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        return list(csv.reader(io.StringIO(z.read(next(n for n in z.namelist() if n.endswith(".csv"))).decode())))


@pytest.mark.parametrize("competing", [False, True], ids=["control", "intervening-save"])
async def test_start_runs_confirmed_plan(app_client, monkeypatch, competing):
    # Given two views of the same analyzed file and a local deterministic provider.
    monkeypatch.setattr(deps, "_harmonize_service", FileHarmonizationService(ClinicalProvider()))
    file_id = await _upload(app_client)
    for _ in range(2):
        initial = await app_client.get(f"/stage-1/analysis/{file_id}")
        assert initial.json()["column_renames"] == {}
    a = await _choose(app_client, file_id, "confirmed by A")
    assert a.status_code == 200
    b_status = None
    if competing:
        b = await _choose(app_client, file_id, "confirmed by B")
        b_status = b.status_code
        assert b_status in {200, 409}
    # When A sends the normal browser's start request after its confirmation succeeded.
    response = await _run(app_client, file_id, a.json().get("plan_version"))

    # Then an accepted result must use A's header, or A must receive a visible conflict.
    if response.status_code == 409 and competing:
        return
    assert response.status_code == 200, response.text
    rows = await _export(app_client, file_id)
    assert rows == [["confirmed by A"], ["Lung Cancer"]]


def _persisted_events(file_id):
    saved = load_review_overrides_record(deps.get_workflow_storage(), deps.get_user_context(), file_id)
    return [] if saved is None else [event.selected_value for event in saved.value.events]


async def _review_rows(client, file_id):
    rows = await client.post("/stage-4/rows", json={"file_id": file_id})
    assert rows.status_code == 200, rows.text
    return rows


def _review_headers(rows):
    return {"If-Match": rows.headers["etag"]} if "etag" in rows.headers else {"If-None-Match": "*"}


@pytest.mark.parametrize("schedule", ["fresh", "during", "after"], ids=["control", "during-rerun", "after-rerun"])
async def test_old_review_cannot_change_new_run(app_client, monkeypatch, schedule):
    # Given a completed diagnosis run and the first-save precondition returned to its reviewer.
    monkeypatch.setattr(deps, "_harmonize_service", FileHarmonizationService(ClinicalProvider()))
    file_id = await _upload(app_client)
    assert (await _choose(app_client, file_id, "reviewed")).status_code == 200
    assert (await _run(app_client, file_id)).status_code == 200
    first = await _review_rows(app_client, file_id)
    assert await _export(app_client, file_id) == [["reviewed"], ["Lung Cancer"]]
    assert _persisted_events(file_id) == []
    old_headers = _review_headers(first)
    body = {
        "file_id": file_id,
        "overrides": {"1": {"col_0000": {"human_value": "X", "original_value": "X"}}},
        "review_state": review_state_payload(),
    }
    entered, release = threading.Event(), threading.Event()
    pending = None

    # When a second run replaces the baseline, sequence the first save around its actual completion.
    if schedule == "during":
        original_validate = review._validate_review_snapshot

        def gate(overrides, manifest):
            original_validate(overrides, manifest)
            entered.set()
            assert release.wait(10), "test run never released validation"

        monkeypatch.setattr(review, "_validate_review_snapshot", gate)

        async def other_client():
            from backend.app.main import create_app

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app()),
                base_url="http://test",
                headers={"X-Data-Chord-User-ID": "test-user"},
            ) as other:
                return await other.post("/stage-4/overrides", headers=old_headers, json=body)

        pending = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(other_client())))
        assert await asyncio.to_thread(entered.wait, 8)
    try:
        assert (await _choose(app_client, file_id, "reviewed", "therapeutic_agents")).status_code == 200
        assert (await _run(app_client, file_id)).status_code == 200
        before = await _export(app_client, file_id)
        assert before == [["reviewed"], ["Aspirin"]]
        assert _persisted_events(file_id) == []
    finally:
        release.set()
    if pending is not None:
        saved = await pending
    else:
        headers = _review_headers(await _review_rows(app_client, file_id)) if schedule == "fresh" else old_headers
        saved = await app_client.post("/stage-4/overrides", headers=headers, json=body)

    # Then only a fresh review can alter this generation's final value or decision history.
    after = await _export(app_client, file_id)
    events = _persisted_events(file_id)
    if schedule == "fresh":
        assert (saved.status_code, after, events) == (200, [["reviewed"], ["X"]], ["X"])
    else:
        assert (saved.status_code, after, events) == (409, before, [])


async def test_changed_analysis_rejects_choices_from_the_previous_page(app_client):
    # Given a reviewer has opened a mapping page and another reviewer has since confirmed a rename.
    file_id = await _upload(app_client)
    original = await app_client.get(f"/stage-1/analysis/{file_id}")
    assert (await _choose(app_client, file_id, "current")).status_code == 200

    # When the old page confirms the choices it displayed.
    reply = await app_client.post(
        "/stage-2/choices",
        json={
            "file_id": file_id,
            "plan_version": original.json().get("plan_version"),
            "column_renames": {"col_0000": "stale"},
        },
    )

    # Then the obsolete confirmation fails and the newer rename remains saved.
    assert reply.status_code == 409
    current = await app_client.get(f"/stage-1/analysis/{file_id}")
    assert current.json()["column_renames"] == {"col_0000": "current"}


@pytest.mark.parametrize("legacy", [False, True], ids=["current-format", "main-format"])
async def test_reviewed_export_survives_audit_loss_and_partial_export_keeps_manual_decisions(
    app_client, monkeypatch, tmp_path, legacy
):
    # Given a reviewer restores a nonconforming value, then opens a complete summary and export.
    monkeypatch.setattr(deps, "_harmonize_service", FileHarmonizationService(ClinicalProvider()))
    file_id = await _upload(app_client)
    assert (await _choose(app_client, file_id, "reviewed")).status_code == 200
    assert (await _run(app_client, file_id)).status_code == 200
    rows = await _review_rows(app_client, file_id)
    payload = {
        "file_id": file_id,
        "overrides": {"1": {"col_0000": {"human_value": "X", "original_value": "X"}}},
        "review_state": review_state_payload(),
    }
    saved = await app_client.post("/stage-4/overrides", headers=_review_headers(rows), json=payload)
    assert saved.status_code == 200
    storage = deps.get_workflow_storage()
    if legacy:
        user = deps.get_user_context()
        record = storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES)
        assert record is not None and isinstance(record.data, dict)
        main_format = dict(record.data)
        main_format.pop("run_id")
        storage.write_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES, main_format, expected_version=record.version)
    request = {"file_id": file_id}
    complete = ReviewPackage.read(await app_client.post("/stage-5/review", json=request))
    assert [item["value"] for item in complete.summary["non_conformant_items"]] == ["X"]

    # When the durable transformation history becomes unavailable before downloading.
    with storage.materialize_artifact(
        deps.get_user_context(), file_id, WorkflowFile.HARMONIZATION_MANIFEST_BASE
    ) as path:
        path.rename(tmp_path / "held-manifest.parquet")
    latest = ReviewPackage.read(await app_client.post("/stage-5/review", json=request))
    denied = await app_client.post("/stage-5/review", headers={"X-Data-Chord-User-ID": "other"}, json=request)

    # Then the first response retains the complete ZIP, the new one warns, and access still requires ownership.
    assert denied.status_code in {403, 404}
    assert latest.summary["history_available"] is False
    assert latest.summary["non_conformant_items"] == complete.summary["non_conformant_items"]
    for reply, has_history in [(complete, True), (latest, False)]:
        with zipfile.ZipFile(io.BytesIO(reply.archive)) as bundle:
            (name,) = [n for n in bundle.namelist() if n.endswith(".csv")]
            assert list(csv.reader(io.StringIO(bundle.read(name).decode()))) == [["reviewed"], ["X"]]
            assert ("AUDIT-WARNING.txt" in bundle.namelist()) is not has_history
            if has_history:
                (history,) = [n for n in bundle.namelist() if n.endswith("_manifest.json")]
                assert json.loads(bundle.read(history))[0]["active_values"] == {"1": "X"}

    if legacy:
        # Given this same older-format record now predates the replacement job.
        record = storage.read_json(deps.get_user_context(), file_id, WorkflowFile.REVIEW_OVERRIDES)
        assert record is not None and isinstance(record.data, dict)
        stale = dict(record.data, created_at=datetime(2000, 1, 1, tzinfo=UTC).isoformat())
        storage.write_json(deps.get_user_context(), file_id, WorkflowFile.REVIEW_OVERRIDES, stale,
                           expected_version=record.version)
        # When the reviewer requests the download with no history to validate it.
        refused = await app_client.post("/stage-5/review", json=request)
        # Then an unverified old correction cannot enter the current download.
        assert refused.status_code == 409
        assert "predate" in refused.json()["detail"]

        # Given a recent legacy review that instead cites the wrong original cell value.
        current = storage.read_json(deps.get_user_context(), file_id, WorkflowFile.REVIEW_OVERRIDES)
        assert current is not None
        mismatched = dict(record.data)
        events = mismatched["events"]
        assert isinstance(events, list) and len(events) == 1 and isinstance(events[0], dict)
        mismatched["events"] = [dict(events[0], original_value="different source")]
        storage.write_json(deps.get_user_context(), file_id, WorkflowFile.REVIEW_OVERRIDES, mismatched,
                           expected_version=current.version)
        # When the reviewer requests the partial download again.
        refused = await app_client.post("/stage-5/review", json=request)
        # Then the original-cell check rejects it even though its creation time is valid.
        assert refused.status_code == 409
        assert "verified against its saved edits" in refused.json()["detail"]
