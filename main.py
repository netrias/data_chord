"""Convenience entry point for running the API with uvicorn."""

import uvicorn


def main() -> None:
    uvicorn.run(
        "backend.app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
