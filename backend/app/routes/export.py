"""Endpoints for exporting harmonized data and audit bundles."""

from fastapi import APIRouter


router = APIRouter()


@router.get("/bundle", summary="Download harmonized dataset and audit bundle")
async def download_bundle() -> dict[str, str]:
    return {"step": "export", "status": "placeholder"}
