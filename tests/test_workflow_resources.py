"""Small synthetic verification cases for scratch storage and reviewer performance."""

import asyncio
import errno
import io
import threading
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from src.app import dependencies
from src.storage import S3WorkflowStorage, UploadStorage, WorkflowFile
from tests.conftest import (
    confirm_mapping_choices,
    create_harmonized_csv,
    create_manifest_for_file,
    review_state_payload,
    upload_content,
    upload_integrity_fields,
)
from tests.test_s3_workflow_storage import FakeS3Client

pytestmark = pytest.mark.asyncio


async def _upload(client, content):
    return await client.post(
        "/stage-1/upload", data=upload_integrity_fields(content), files={"file": ("synthetic.csv", content, "text/csv")}
    )


def _bytes_used(root):
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


async def _finish(client, file_id):
    response = await client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": "test-data-model",
            "external_version_number": "11.0.4",
        },
    )
    assert response.status_code == 200, response.text
    await confirm_mapping_choices(client, file_id)
    run = await client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert run.status_code == 200, run.text
    job = run.json()
    for _ in range(100):
        if job["status"] != "queued":
            break
        await asyncio.sleep(0.025)
        status = await client.get(f"/stage-3/jobs/{job['job_id']}", params={"file_id": file_id})
        assert status.status_code == 200, status.text
        job = status.json()
    assert job["status"] == "succeeded", job


@pytest.fixture
def scratch_patch(app_client):
    with pytest.MonkeyPatch.context() as patch:
        yield patch


async def test_completed_hosted_workflows_release_temporary_space(app_client, temp_storage, scratch_patch, tmp_path):
    # Given a hosted app with the real S3 adapter, synthetic completed jobs and one current upload.
    s3 = S3WorkflowStorage("offline", FakeS3Client())
    scratch_patch.setattr(dependencies, "get_workflow_storage", lambda: s3)
    assert dependencies.get_runtime_profile().value == "hosted"
    content = b"primary_diagnosis\nDiabetes\n"
    import tempfile
    downloads = tmp_path / "system-temp"
    downloads.mkdir()
    scratch_patch.setattr(tempfile, "tempdir", str(downloads))
    # Fixed, pre-calibrated logical quota for this small fixture; observe actual scratch use at each write.
    capacity = 10_040
    root = temp_storage._base_dir
    original_open = Path.open

    def quota_open(path, mode="r", *args, **kwargs):
        if mode == "wb" and path.parent == temp_storage._data_dir and _bytes_used(root) + len(content) > capacity:
            raise OSError(errno.ENOSPC, "test scratch quota")
        return original_open(path, mode, *args, **kwargs)

    scratch_patch.setattr(Path, "open", quota_open)
    scratch_patch.setattr(temp_storage, "available_bytes", lambda: max(0, capacity - _bytes_used(root)))
    finished = []
    samples = []
    for _ in range(3):
        file_id = await upload_content(app_client, content)
        await _finish(app_client, file_id)
        finished.append(file_id)
        samples.append(_bytes_used(root))
    active = await upload_content(app_client, content)
    user = dependencies.get_user_context()
    with s3.materialize_artifact(user, active, WorkflowFile.ORIGINAL_UPLOAD) as source:
        assert source.read_bytes() == content
    await asyncio.sleep(0.1)
    # When the next normal upload arrives, only idle completed copies could need reclamation.
    response = await _upload(app_client, content)
    # Then the active upload must be intact, and a fresh scratch store must recover an old export.
    with s3.materialize_artifact(user, active, WorkflowFile.ORIGINAL_UPLOAD) as source:
        assert source.read_bytes() == content
    fresh = UploadStorage(tmp_path / "fresh-scratch", temp_storage._constraints)
    scratch_patch.setattr(dependencies, "get_upload_storage", lambda: fresh)
    recovered = await app_client.post("/stage-5/download", json={"file_id": finished[0]})
    assert recovered.status_code == 200, recovered.text
    with zipfile.ZipFile(io.BytesIO(recovered.content)) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".csv"))
        assert archive.read(name).splitlines() == content.splitlines()
    fresh_upload = await _upload(app_client, content)
    assert fresh_upload.status_code == 201
    assert response.status_code == 201, response.text
    assert samples == [0, 0, 0]
    assert list(downloads.iterdir()) == []


async def test_summary_scales_with_edits_and_keeps_health_responsive(app_client, temp_storage, monkeypatch):
    from src.stage_5_review_summary import router as routes
    from src.stage_5_review_summary import use_cases as uc

    read_review = uc.load_readable_review_overrides_record
    build = routes.build_summary
    loop = asyncio.get_running_loop()

    def responsive_build(ready, **kwargs):
        loop.call_soon_threadsafe(ready.set)
        assert ready.wait(0.5), "summary blocked the event loop"
        return build(**kwargs)

    records = []
    # Given grouped synthetic workflows of different sizes, an idle probe, and unedited summary controls.
    for size in [128, 256, 512]:
        file_id = await upload_content(app_client, b"value\n" + b"Foo\n" * size)
        path = temp_storage.load(file_id).saved_path
        create_harmonized_csv(temp_storage, file_id, path, {})
        create_manifest_for_file(temp_storage, file_id, path, {})
        health = await app_client.get("/healthz")
        assert health.status_code == 200
        payload = {"file_id": file_id}
        assert (await app_client.get("/healthz")).status_code == 200
        control = await app_client.post("/stage-5/summary", json=payload)
        assert control.status_code == 200, control.text
        assert control.json()["column_summaries"][0]["reviewer_edited_rows"] == 0
        # When the reviewer changes one repeated term through Stage 4 and reads the summary.
        edits = {str(i + 1): {"col_0000": {"human_value": "Bar", "original_value": "Foo"}} for i in range(size)}
        save = await app_client.post(
            "/stage-4/overrides",
            headers={"If-None-Match": "*"},
            json={**payload, "overrides": edits, "review_state": review_state_payload()},
        )
        assert save.status_code == 200, save.text
        count = 0

        class CountedEvents(tuple):
            def __iter__(self):
                nonlocal count
                for event in super().__iter__():
                    count += 1
                    yield event

        def instrumented_review(storage, user, file_id):
            record = read_review(storage, user, file_id)
            assert record is not None
            return replace(record, value=replace(record.value, events=CountedEvents(record.value.events)))

        with monkeypatch.context() as scoped:
            scoped.setattr(uc, "load_readable_review_overrides_record", instrumented_review)
            response = await app_client.post("/stage-5/summary", json=payload)
        assert response.status_code == 200, response.text
        # Then the edit is represented exactly; observe whether unrelated work can run during the summary.
        (col,) = response.json()["column_summaries"]
        assert col["reviewer_edited_rows"] == size
        (mapping,) = response.json()["term_mappings"]
        assert (mapping["final_value"], mapping["row_count"]) == ("Bar", size)
        done = threading.Event()
        with monkeypatch.context() as scoped:
            from functools import partial

            scoped.setattr(routes, "build_summary", partial(responsive_build, done))
            assert (await app_client.post("/stage-5/summary", json=payload)).status_code == 200
        records.append({"rows": size, "event_visits": count})

    assert records[-1]["event_visits"] <= 3 * records[-2]["event_visits"], records


async def test_failed_worker_scratch_releases_the_job_slot(app_client, temp_storage, scratch_patch):
    # Given a durably stored S3 input and a worker that cannot create its scratch directory.
    from contextlib import contextmanager

    s3 = S3WorkflowStorage("offline", FakeS3Client())
    scratch_patch.setattr(dependencies, "get_workflow_storage", lambda: s3)
    file_id = await upload_content(app_client, b"primary_diagnosis\nDiabetes\n")
    assert (
        await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "data_model_key": "test-data-model", "external_version_number": "11.0.4"},
        )
    ).status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    original = temp_storage.private_workspace

    @contextmanager
    def fail_worker_scratch():
        task = asyncio.current_task()
        if task is not None and task.get_name().startswith("harmonization-"):
            raise OSError(errno.ENOSPC, "worker workspace full")
        with original():
            yield

    # When an accepted worker fails to open its workspace.
    with scratch_patch.context() as faults:
        faults.setattr(temp_storage, "private_workspace", fail_worker_scratch)
        result = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})

    # Then its failure is durable and the next workflow can finish using the same capacity.
    assert result.status_code == 200
    assert result.json()["status"] == "failed"
    next_file = await upload_content(app_client, b"primary_diagnosis\nDiabetes\n")
    await _finish(app_client, next_file)


@pytest.mark.parametrize("grace", [0.0, 5.0], ids=["return-while-draining", "finish-in-grace"])
async def test_worker_shutdown_keeps_files_until_the_provider_finishes(app_client, temp_storage, scratch_patch, grace):
    # Given an S3 workflow whose provider is still reading the local source and writing its output.
    s3 = S3WorkflowStorage("offline", FakeS3Client())
    scratch_patch.setattr(dependencies, "get_workflow_storage", lambda: s3)
    content = b"primary_diagnosis\nDiabetes\n"
    file_id = await upload_content(app_client, content)
    analysis = await app_client.post("/stage-1/analyze", json={
        "file_id": file_id, "data_model_key": "test-data-model", "external_version_number": "11.0.4",
    })
    assert analysis.status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    started = threading.Event()
    release = threading.Event()
    source_paths = []
    observed = []

    class Provider:
        def run(self, *, file_path, output_path, **kwargs):
            source_paths.append(file_path)
            started.set()
            if not release.wait(5):
                raise TimeoutError("provider was never released")
            output_path.write_bytes(file_path.read_bytes())
            observed.append(output_path.read_bytes())
            raise RuntimeError("test provider stopped")

    scratch_patch.setattr(dependencies, "get_harmonize_service", Provider)
    job = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert job.json()["status"] == "queued"
    assert started.is_set()

    # When shutdown cancels the worker while the provider holds both files.
    service = dependencies.get_harmonization_job_service()
    workers = list(service._tasks.values())
    shutdown = asyncio.create_task(service.shutdown(grace_seconds=grace))
    try:
        await asyncio.sleep(0.02)
        assert shutdown.done() == (grace == 0)
        assert source_paths[0].read_bytes() == content
    finally:
        release.set()
        await asyncio.wait_for(shutdown, timeout=5)
        await asyncio.gather(*workers, return_exceptions=True)

    # Then the provider finished its file I/O before cleanup reclaimed the workspace and the source.
    assert observed == [content]
    assert not source_paths[0].exists()
    assert _bytes_used(temp_storage._base_dir) == 0
