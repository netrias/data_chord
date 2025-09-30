"""Endpoints for reviewing and confirming column-to-model mappings."""

from fastapi import APIRouter


router = APIRouter()


@router.get("/suggestions", summary="Fetch mapping suggestions")
async def get_mapping_suggestions() -> dict[str, str]:
    return {"step": "mappings", "status": "placeholder"}


@router.post("/overrides", summary="Save mapping overrides")
async def save_mapping_overrides() -> dict[str, str]:
    return {"step": "mappings", "action": "save-overrides", "status": "placeholder"}
