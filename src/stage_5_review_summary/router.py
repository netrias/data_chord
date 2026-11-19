"""
HTTP routes for summarizing harmonization results and generating downloads.

Computes change statistics and packages final CSV with manifest for export.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse

import src.app.dependencies as dependencies
from src.shared.jinja import templates_for_stage
from src.stage_5_review_summary.schemas import PreparedReviewRequest, StageFiveRequest, StageFiveSummaryResponse
from src.stage_5_review_summary.use_cases import (
    build_download_package,
    build_summary,
    prepare_review,
)
from src.storage import UploadStorage

_MODULE_DIR = Path(__file__).parent
_TEMPLATE_DIR = _MODULE_DIR / "templates"

_templates = templates_for_stage(_TEMPLATE_DIR)

stage_five_router = APIRouter(prefix="/stage-5", tags=["Stage 5 Download"])


@stage_five_router.get("", response_class=HTMLResponse, name="stage_five_review_page")
async def render_stage_five(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(request, "stage_5_review.html", {"request": request})


@stage_five_router.post("/summary", response_model=StageFiveSummaryResponse, name="stage_five_summary")
def summarize_harmonized_results(payload: StageFiveRequest) -> StageFiveSummaryResponse:
    storage: UploadStorage = dependencies.get_upload_storage()
    return build_summary(
        file_id=payload.file_id,
        upload_storage=storage,
        workflow_storage=dependencies.get_workflow_storage(),
        user=dependencies.get_user_context(),
    )


@stage_five_router.post("/review", name="stage_five_prepared_review", response_class=StreamingResponse,
                        responses={200: {"content": {"multipart/form-data": {}}}})
def review_with_download(payload: PreparedReviewRequest) -> StreamingResponse:
    """Send both parts of one verified revision. The browser owns the ZIP until the tab closes."""
    review = prepare_review(payload.file_id, dependencies.get_upload_storage(), dependencies.get_workflow_storage(),
                            dependencies.get_user_context(), payload.job_id)
    boundary = uuid4().hex
    marker = f"--{boundary}".encode("ascii")

    def parts():
        yield marker + b'\r\nContent-Disposition: form-data; name="summary"\r\n\r\n'
        yield review.summary.model_dump_json().encode("utf-8")
        yield b'\r\n' + marker + b'\r\nContent-Disposition: form-data; name="download"; filename="review.zip"\r\n'
        yield b'Content-Type: application/zip\r\n\r\n'
        while chunk := review.download.content.read(64 * 1024):
            yield chunk
        yield b"\r\n" + marker + b"--\r\n"

    name = quote(review.download.base_name + ".zip", safe="")
    headers = {"Cache-Control": "no-store", "Content-Disposition": f"inline; filename*=UTF-8''{name}"}
    headers["X-Harmonization-Job-Id"] = review.job_id
    return StreamingResponse(parts(), media_type=f"multipart/form-data; boundary={boundary}", headers=headers)


@stage_five_router.post("/download", name="stage_five_download")
def download_harmonized_data(payload: StageFiveRequest) -> StreamingResponse:
    """Download the current workflow for callers that do not open the Review page."""
    storage = dependencies.get_upload_storage()
    download = build_download_package(
        file_id=payload.file_id,
        upload_storage=storage,
        workflow_storage=dependencies.get_workflow_storage(),
        user=dependencies.get_user_context(),
    )
    return _create_streaming_response(download.base_name, download.content)


def _create_streaming_response(base_name: str, zip_buffer: BytesIO) -> StreamingResponse:
    safe_filename = quote(f"{base_name}.zip", safe="")
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}"},
    )
