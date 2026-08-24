"""Behavior tests for the isolated packaged demo."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.app import dependencies
from src.app.demo_mode import DEMO_WORKFLOW_ID, get_demo_upload, prepare_demo_runtime
from tests.conftest import confirm_mapping_choices


@pytest.fixture
def demo_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[Path]:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_CHORD_MODE", "demo")
    monkeypatch.setenv("DATA_CHORD_PROFILE", "portable")
    monkeypatch.setenv("DATA_CHORD_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATA_CHORD_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("DATA_CHORD_IDENTITY_SOURCE", "shared")
    dependencies.cleanup_services()
    yield data_dir
    dependencies.cleanup_services()


@pytest.mark.asyncio
async def test_demo_bootstrap_loads_the_packaged_standard_and_upload(
    demo_runtime: Path,
) -> None:
    # Given an empty disposable demo runtime.
    assert list(demo_runtime.rglob("*")) == []

    # When the demo bootstrap prepares it.
    await prepare_demo_runtime()

    # Then the normal data clients expose the packaged standard and upload.
    models = dependencies.get_reference_data_repository().list_models()
    upload = get_demo_upload()
    assert [(model.data_model_key, model.label) for model in models] == [
        ("data-chord-demo", "Data Chord Demo Standard")
    ]
    assert upload.file_id == DEMO_WORKFLOW_ID
    assert upload.original_name == "sample.csv"
    assert (demo_runtime / "standards.sqlite").is_file()


@pytest.mark.asyncio
async def test_demo_stage_one_shows_one_locked_ready_file(demo_runtime: Path) -> None:
    # Given the packaged demo is ready and stale browser state is possible.
    await prepare_demo_runtime()
    from backend.app.main import create_app

    app = create_app()

    # When a browser opens the first page.
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/stage-1")

    # Then the page owns one locked file and provides its normal Map action.
    assert response.status_code == 200
    file_input = response.text.split('id="fileInput"', 1)[1].split("/>", 1)[0]
    dropzone = response.text.split('id="dropzone"', 1)[1].split(">", 1)[0]
    assert "disabled" in file_input
    assert "Demo file locked" in response.text
    assert "Normal mode lets you upload your own file" in response.text
    assert str(DEMO_WORKFLOW_ID) in response.text
    assert 'role="button"' not in dropzone
    assert "tabindex" not in dropzone
    assert 'id="analyzeButton"' in response.text


@pytest.mark.asyncio
async def test_demo_workflow_reaches_review_without_a_model_provider(
    demo_runtime: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the packaged demo and provider calls that fail if reached.
    await prepare_demo_runtime()

    def _reject_provider_client(
        region: str,
        *,
        provider: object,
        reasoning_effort: object,
    ) -> object:
        raise AssertionError(
            f"Bedrock must not open: {region}, {provider}, {reasoning_effort}"
        )

    monkeypatch.setattr(
        "src.integrations.agentic_harmonize.make_provider_client",
        _reject_provider_client,
    )
    from backend.app.main import create_app

    # When the normal Stage 1 through Stage 4 APIs process the demo.
    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
    ) as client:
        analysis = await client.post(
            "/stage-1/analyze",
            json={
                "file_id": str(DEMO_WORKFLOW_ID),
                "data_model_key": "data-chord-demo",
                "external_version_number": "1.0",
            },
        )
        await confirm_mapping_choices(client, str(DEMO_WORKFLOW_ID))
        harmonization = await client.post(
            "/stage-3/harmonize",
            json={"file_id": str(DEMO_WORKFLOW_ID)},
        )
        job = harmonization.json()
        for _attempt in range(100):
            if job["status"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.02)
            job = (
                await client.get(
                    f"/stage-3/jobs/{job['job_id']}",
                    params={"file_id": str(DEMO_WORKFLOW_ID)},
                )
            ).json()
        review = await client.post(
            "/stage-4/rows",
            json={"file_id": str(DEMO_WORKFLOW_ID)},
        )

    # Then fixed mappings and harmonizations reach a real review result.
    assert analysis.status_code == 200, analysis.text
    targets = analysis.json()["cde_targets"]
    assert [targets[f"col_{index:04d}"][0]["target"] for index in range(4)] == [
        "record_id",
        "primary_diagnosis",
        "specimen_type",
        "treatment_status",
    ]
    assert job["status"] == "succeeded"
    assert review.status_code == 200, review.text
    assert review.json()["totalOriginalRows"] == 3
