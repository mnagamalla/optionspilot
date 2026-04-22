import time

from fastapi import APIRouter, HTTPException

from backend.models.scan import ScanRequest, ScanResponse
from backend.services.scanner import run_csp_scan

router = APIRouter(tags=["Cash-Secured Puts"])


@router.post("/scan/csp", response_model=ScanResponse)
def scan_csp(req: ScanRequest):
    """
    Run a cash-secured put scan with the given parameters.
    Returns candidates sorted by annual yield descending.
    """
    if req.dte_min >= req.dte_max:
        raise HTTPException(
            status_code=422, detail="dte_min must be less than dte_max"
        )

    valid_safety = {"conservative", "balanced", "aggressive"}
    if req.safety not in valid_safety:
        raise HTTPException(
            status_code=422,
            detail=f"safety must be one of: {', '.join(valid_safety)}",
        )

    start = time.time()

    raw = run_csp_scan(
        tickers=req.tickers,
        dte_min=req.dte_min,
        dte_max=req.dte_max,
        max_results=req.max_results,
        collateral_budget=req.collateral_budget,
        safety=req.safety,
    )

    return ScanResponse(
        results=raw,
        count=len(raw),
        elapsed_ms=int((time.time() - start) * 1000),
        tickers_scanned=req.tickers,
        params=req.model_dump(),
    )
