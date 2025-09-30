"""Expose FastAPI application and register route modules."""

from fastapi import FastAPI

from .routes import export, harmonize, mappings, review, upload


app = FastAPI(title="Data Chord", version="0.1.0")


app.include_router(upload.router, prefix="/api/upload", tags=["upload"])
app.include_router(mappings.router, prefix="/api/mappings", tags=["mappings"])
app.include_router(harmonize.router, prefix="/api/harmonize", tags=["harmonize"])
app.include_router(review.router, prefix="/api/review", tags=["review"])
app.include_router(export.router, prefix="/api/export", tags=["export"])


@app.get("/health", tags=["meta"])
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["meta"])
def readiness() -> dict[str, str]:
    return {"status": "ready"}
