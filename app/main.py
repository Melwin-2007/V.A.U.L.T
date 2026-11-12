"""FastAPI entrypoint for the Year-on-Year Financial Validation Agent."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.yoy_routes import router as yoy_router

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Year-on-Year Financial Validation Agent",
    description=(
        "Deterministic YoY validation component for the Human-in-the-Loop "
        "SEC 10-K Financial Filing Auditor Agent."
    ),
    version="1.0.0",
)

app.include_router(yoy_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Basic service health probe."""
    return {"status": "ok", "component": "yoy-financial-validation-agent"}


@app.get("/", include_in_schema=False)
def demo_ui() -> FileResponse:
    """Serve the decoupled demo UI for reviewing YoY validations."""
    return FileResponse(STATIC_DIR / "index.html")
