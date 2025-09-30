"""Endpoints for executing harmonization plans."""

from fastapi import APIRouter


router = APIRouter()


@router.post("/run", summary="Start harmonization job")
async def run_harmonization() -> dict[str, str]:
    return {"step": "harmonize", "status": "placeholder"}


@router.get("/status/{job_id}", summary="Retrieve harmonization job status")
async def harmonization_status(job_id: str) -> dict[str, str]:
    return {"step": "harmonize", "job_id": job_id, "status": "placeholder"}
