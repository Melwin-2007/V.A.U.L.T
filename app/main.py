"""FastAPI entrypoint for V.A.U.L.T Human-in-the-Loop Financial Filing Auditor Agent."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.runs_routes import router as runs_router
from app.api.yoy_routes import router as yoy_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

app = FastAPI(
    title="V.A.U.L.T: Human-in-the-Loop Financial Filing Auditor Agent",
    description=(
        "Audit-grade financial validation and verification platform for SEC Form 10-K "
        "filings with pause-and-resume workflows and cryptographic audit logging."
    ),
    version="1.0.0",
)

# Enable CORS for Streamlit dashboard and browser clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs_router)
app.include_router(yoy_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Basic service health probe."""
    return {"status": "ok", "component": "vault-auditor-agent"}


@app.get("/static/demo_timeline.json", include_in_schema=False)
@app.get("/demo_timeline.json", include_in_schema=False)
def get_demo_timeline() -> FileResponse:
    """Serve the scripted demo timeline for Pitch Demo mode."""
    return FileResponse(DASHBOARD_DIR / "demo_timeline.json")


@app.get("/", include_in_schema=False)
def demo_ui() -> FileResponse:
    """Serve the demo UI for reviewing validations."""
    return FileResponse(STATIC_DIR / "index.html")
