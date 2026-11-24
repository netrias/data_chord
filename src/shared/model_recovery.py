"""Browser contract for choosing a replacement reference model."""

from urllib.parse import urlencode

from fastapi import Request, status
from fastapi.responses import JSONResponse

from src.domain.dataset_workflow_ids import DatasetWorkflowId

RETIRED_MODEL_DETAIL = "This model version is no longer available. Choose a current version and run analysis again."
REFERENCE_UNAVAILABLE_DETAIL = "Reference data is currently unavailable. Please try again later."


def model_recovery_path(request: Request, file_id: DatasetWorkflowId) -> str:
    path = request.url_for("stage_one_upload_page").path
    query = urlencode({"file_id": file_id, "reselect_model": "true"})
    return f"{path}?{query}"


def retired_model_response(recovery_url: str | None = None) -> JSONResponse:
    body = {"code": "model_version_retired", "detail": RETIRED_MODEL_DETAIL}
    if recovery_url is not None:
        body["recovery_url"] = recovery_url
    return JSONResponse(
        body,
        status_code=status.HTTP_409_CONFLICT,
    )
