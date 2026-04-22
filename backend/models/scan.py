from pydantic import BaseModel, Field
from typing import List


class ScanRequest(BaseModel):
    tickers: List[str] = Field(
        default=["AMD", "PLTR", "NFLX", "AVGO", "NVDA"],
        description="List of ticker symbols to scan",
    )
    dte_min: int = Field(default=30, ge=1, le=180, description="Minimum days to expiry")
    dte_max: int = Field(default=45, ge=1, le=365, description="Maximum days to expiry")
    max_results: int = Field(default=5, ge=1, le=20, description="Max results per ticker")
    collateral_budget: float = Field(
        default=10000, ge=100, description="Max collateral per position ($)"
    )
    safety: str = Field(
        default="balanced",
        description="One of: conservative, balanced, aggressive",
    )


class ScanResult(BaseModel):
    ticker: str
    price: float
    strike: float
    premium: float
    DTE: int
    cushion_pct: float
    roi_pct: float
    annual_yield_pct: float
    collateral: float
    credit: float
    expiration: str


class ScanResponse(BaseModel):
    results: List[ScanResult]
    count: int
    elapsed_ms: int
    tickers_scanned: List[str]
    params: dict
