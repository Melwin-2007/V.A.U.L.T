"""FastAPI route for the Accounting Equation Validation Engine."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .accounting_equation import validate_accounting_equation


class AccountingEquationRequest(BaseModel):
    cik: str = Field(..., description="SEC Central Index Key (e.g. '320193' or '0000320193')")
    fiscal_year: Optional[int] = Field(None, description="Optional fiscal year filter")
    accession: Optional[str] = Field(None, description="Optional SEC accession number")


router = APIRouter(prefix="/validate", tags=["validation"])


@router.post("/accounting-equation")
def validate_accounting_equation_endpoint(payload: AccountingEquationRequest) -> Dict[str, Any]:
    """Run deterministic accounting-equation validation for a company filing."""
    try:
        return validate_accounting_equation(
            cik=payload.cik,
            fiscal_year=payload.fiscal_year,
            accession=payload.accession,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def create_app() -> FastAPI:
    app = FastAPI(title="V.A.U.L.T Financial Filing Auditor - Validation API", version="1.0.0")
    app.include_router(router)
    return app


app = create_app()

