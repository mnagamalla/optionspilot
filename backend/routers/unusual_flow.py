from typing import Optional, List
from fastapi import APIRouter, Query

from backend.services.flow_scanner import (
    scan_unusual_flow,
    WATCHLIST_TICKERS,
    BROADER_MARKET_TICKERS,
)

router = APIRouter(prefix="/flow", tags=["Unusual Flow"])


@router.get("/scan")
def scan_flow(
    universe:          str   = Query(default="watchlist",
                                     description="watchlist | broader | both"),
    extra_tickers:     str   = Query(default="",
                                     description="Comma-separated extra tickers"),
    vol_oi_threshold:  float = Query(default=1.5,
                                     description="Min vol/OI ratio (1.5 | 3 | 5)"),
    min_total_premium: float = Query(default=25000,
                                     description="Min total premium ($25k | $50k | $100k)"),
    dte_min:           int   = Query(default=0,   description="Min days to expiry"),
    dte_max:           int   = Query(default=60,  description="Max days to expiry"),
    option_type:       str   = Query(default="both",
                                     description="calls | puts | both"),
    min_volume:        int   = Query(default=50,  description="Min contract volume"),
    max_results:       int   = Query(default=50,  description="Max results to return"),
):
    """
    Scan for unusual options flow.
    Combines volume spikes (vol/OI ratio) and block trades (total premium size).
    """
    tickers: List[str] = []

    if universe in ("watchlist", "both"):
        tickers += WATCHLIST_TICKERS
    if universe in ("broader", "both"):
        tickers += BROADER_MARKET_TICKERS

    # Add any user-specified extra tickers
    if extra_tickers:
        extras = [t.strip().upper() for t in extra_tickers.split(",") if t.strip()]
        tickers += extras

    # Deduplicate while preserving order (watchlist first)
    seen = set()
    deduped = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            deduped.append(t)

    results = scan_unusual_flow(
        tickers=deduped,
        vol_oi_threshold=vol_oi_threshold,
        min_total_premium=min_total_premium,
        dte_min=dte_min,
        dte_max=dte_max,
        option_type=option_type,
        min_volume=min_volume,
        max_results=max_results,
    )

    return {
        "count":           len(results),
        "tickers_scanned": len(deduped),
        "filters": {
            "universe":          universe,
            "vol_oi_threshold":  vol_oi_threshold,
            "min_total_premium": min_total_premium,
            "dte_min":           dte_min,
            "dte_max":           dte_max,
            "option_type":       option_type,
        },
        "results": results,
    }


@router.get("/tickers")
def get_universes():
    """Return the default ticker universes."""
    return {
        "watchlist":     WATCHLIST_TICKERS,
        "broader_market": BROADER_MARKET_TICKERS,
    }
