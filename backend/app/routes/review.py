"""Endpoints for human-in-the-loop review and approvals."""

from fastapi import APIRouter


router = APIRouter()


@router.get("/batches", summary="Fetch review batches")
async def get_review_batches() -> dict[str, str]:
    return {"step": "review", "status": "placeholder"}


@router.post("/decisions", summary="Submit curator decisions")
async def submit_decisions() -> dict[str, str]:
    return {"step": "review", "action": "submit", "status": "placeholder"}
