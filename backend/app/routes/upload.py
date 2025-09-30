"""Endpoints for ingesting source datasets."""

from fastapi import APIRouter


router = APIRouter()


@router.post("/", summary="Upload dataset")
async def upload_dataset() -> dict[str, str]:
    return {"step": "upload", "status": "placeholder"}
