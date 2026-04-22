import time

from fastapi import APIRouter

from backend.models.scan import ScanRequest, ScanResponse
from backend.services.scanner import run_cc_scan

router = APIRouter(tags=["Covered Calls"])


@router.post("/scan/cc", response_model=ScanResponse)
def scan_cc(req: ScanRequest):
    """
    Covered call scan — Phase 3.
    """
    start = time.time()

    raw = run_cc_scan(
        tickers=req.tickers,
        dte_min=req.dte_min,
        dte_max=req.dte_max,
        max_results=req.max_results,
        safety=req.safety,
    )

    return ScanResponse(
        results=raw,
        count=len(raw),
        elapsed_ms=int((time.time() - start) * 1000),
        tickers_scanned=req.tickers,
        params=req.model_dump(),
    )
