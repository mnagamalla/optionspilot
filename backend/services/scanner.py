import warnings
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

import math
import yfinance as yf
from datetime import datetime
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed


def _safe_float(val, default=0.0):
    """Safely convert value to float, treating NaN as default."""
    try:
        v = float(val)
        return default if math.isnan(v) else v
    except (TypeError, ValueError):
        return default


def _safe_int(val, default=0):
    """Safely convert value to int, treating NaN as default."""
    try:
        v = float(val)
        return default if math.isnan(v) else int(v)
    except (TypeError, ValueError):
        return default

# Base cushion thresholds for standard 30-45 DTE options
SAFETY_CUSHION_MAP = {
    "conservative": 0.10,
    "balanced":     0.08,
    "aggressive":   0.05,
}

def _get_cushion_min(safety: str, dte: int) -> float:
    """
    Return the minimum OTM cushion required for a given safety level and DTE.
    Uses square root scaling so safety levels stay meaningfully differentiated
    even at low DTE — linear scaling collapses all levels together near 0DTE.

    Scaling formula: cushion = base * sqrt(min(dte, 45) / 45)

    Example at 2 DTE:
      Conservative: 10% * sqrt(2/45) = 2.1% OTM required
      Balanced:      8% * sqrt(2/45) = 1.7% OTM required
      Aggressive:    5% * sqrt(2/45) = 1.1% OTM required

    Example at 45 DTE (full cushion):
      Conservative: 10% * sqrt(1.0) = 10.0% OTM required
      Balanced:      8% * sqrt(1.0) =  8.0% OTM required
      Aggressive:    5% * sqrt(1.0) =  5.0% OTM required

    Floor: 0.5% minimum to avoid deep ITM junk.
    """
    base  = SAFETY_CUSHION_MAP.get(safety, 0.08)
    scale = math.sqrt(min(dte, 45) / 45)
    return max(base * scale, 0.005)  # floor at 0.5%

# Minimum liquidity thresholds — skip options with no real market
MIN_VOLUME           = 0     # don't filter by volume (can be 0 pre-market)
MIN_OPEN_INTEREST    = 10    # at least 10 open contracts
MAX_SPREAD_PCT       = 0.90  # skip only extremely wide spreads (>90% of ask)
LAST_PRICE_DISCOUNT  = 0.85  # when bid=0, use 85% of lastPrice as conservative estimate


def _realistic_premium(row) -> float:
    """
    Return a realistic fill price for an option row.

    Priority:
      1. Bid price  — what you will actually receive selling to open
      2. 85% of lastPrice — conservative fallback when bid=0
      3. 0.0 — skip this strike (no market data at all)

    NaN values from yfinance are safely coerced to 0.
    """
    bid  = _safe_float(row.get("bid"))
    ask  = _safe_float(row.get("ask"))
    last = _safe_float(row.get("lastPrice"))
    oi   = _safe_int(row.get("openInterest"))

    # Skip if absolutely no market data
    if bid <= 0 and last <= 0 and oi < MIN_OPEN_INTEREST:
        return 0.0

    # Have a real bid — use it directly
    if bid > 0:
        # Only skip if spread is extremely wide AND ask is meaningful
        if ask > 0:
            spread_pct = (ask - bid) / ask
            if spread_pct > MAX_SPREAD_PCT:
                return 0.0
        return round(bid, 2)

    # bid=0 but last price exists — use discounted last price
    if last > 0:
        return round(last * LAST_PRICE_DISCOUNT, 2)

    return 0.0


def _scan_csp_ticker(ticker, price, expirations, dte_min, dte_max,
                     collateral_budget, max_results, safety='balanced'):
    stock = yf.Ticker(ticker)
    ticker_results = []

    for exp in expirations:
        from datetime import date as _date
        exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        dte = (exp_date - _date.today()).days
        if dte < dte_min or dte > dte_max:
            continue  # dte_min=0 allows same-day expiry
        try:
            chain = stock.option_chain(exp)
            puts  = chain.puts
        except Exception:
            continue

        for _, row in puts.iterrows():
            try:
                strike = float(row["strike"])
            except (TypeError, ValueError):
                continue

            if strike * 100 > collateral_budget:
                continue

            premium = _realistic_premium(row)
            if premium <= 0:
                continue

            # Skip options paying less than $0.05/share ($5/contract) — not worth selling
            if premium < 0.05:
                continue

            cushion     = (price - strike) / price
            cushion_min = _get_cushion_min(safety, dte)
            if cushion < cushion_min:
                continue

            collateral   = strike * 100
            credit       = round(premium * 100, 2)
            roi          = credit / collateral
            # For 0DTE treat as 1 day for annualisation to avoid division by zero
            annual_yield = roi * (365 / max(dte, 1))

            ticker_results.append({
                "ticker":           ticker,
                "price":            round(price, 2),
                "strike":           round(strike, 2),
                "premium":          premium,
                "bid":              _safe_float(row.get("bid")),
                "ask":              _safe_float(row.get("ask")),
                "volume":           _safe_int(row.get("volume")),
                "open_interest":    _safe_int(row.get("openInterest")),
                "iv_pct":           round(_safe_float(row.get("impliedVolatility")) * 100, 1),
                "DTE":              dte,
                "cushion_pct":      round(cushion * 100, 2),
                "safety":           safety,
                "roi_pct":          round(roi * 100, 2),
                "annual_yield_pct": round(annual_yield * 100, 2),
                "collateral":       round(collateral, 2),
                "credit":           credit,
                "expiration":       exp,
            })

    # For 0-1 DTE sort by credit amount (raw dollar value) — annual yield is misleading
    # For normal DTE sort by annual yield as usual
    has_short_dte = any(r["DTE"] <= 1 for r in ticker_results)
    if has_short_dte:
        ticker_results.sort(key=lambda x: x["credit"], reverse=True)
    else:
        ticker_results.sort(key=lambda x: x["annual_yield_pct"], reverse=True)
    return ticker_results[:max_results]


def run_csp_scan(
    tickers: List[str],
    dte_min: int = 0,
    dte_max: int = 45,
    max_results: int = 5,
    collateral_budget: float = 10000,
    safety: str = "balanced",
) -> List[dict]:
    cushion_min     = None  # computed per-expiry via _get_cushion_min
    results         = []
    prices          = {}
    expirations_map = {}

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            hist  = stock.history(period="1d")
            if hist.empty:
                continue
            prices[ticker]          = float(hist["Close"].iloc[-1])
            expirations_map[ticker] = stock.options or []
        except Exception as e:
            print(f"[scanner] Error fetching {ticker}: {e}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(
                _scan_csp_ticker,
                ticker, prices[ticker], expirations_map[ticker],
                dte_min, dte_max, collateral_budget, max_results, safety
            ): ticker
            for ticker in prices
        }
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as e:
                print(f"[scanner] Thread error: {e}")

    results.sort(key=lambda x: x["annual_yield_pct"], reverse=True)
    return results


def _scan_cc_ticker(ticker, price, expirations, dte_min, dte_max,
                    max_results, safety='balanced'):
    stock = yf.Ticker(ticker)
    ticker_results = []

    for exp in expirations:
        from datetime import date as _date
        exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        dte = (exp_date - _date.today()).days
        if dte < dte_min or dte > dte_max:
            continue  # dte_min=0 allows same-day expiry
        try:
            chain = stock.option_chain(exp)
            calls = chain.calls
        except Exception:
            continue

        for _, row in calls.iterrows():
            strike = float(row["strike"])

            if strike <= price:
                continue

            premium = _realistic_premium(row)
            if premium <= 0:
                continue

            # Skip options paying less than $0.05/share ($5/contract)
            if premium < 0.05:
                continue

            cushion     = (strike - price) / price
            cushion_min = _get_cushion_min(safety, dte)
            if cushion < cushion_min:
                continue

            collateral   = price * 100
            credit       = round(premium * 100, 2)
            roi          = credit / collateral
            annual_yield = roi * (365 / max(dte, 1))

            ticker_results.append({
                "ticker":           ticker,
                "price":            round(price, 2),
                "strike":           round(strike, 2),
                "premium":          premium,
                "bid":              _safe_float(row.get("bid")),
                "ask":              _safe_float(row.get("ask")),
                "volume":           _safe_int(row.get("volume")),
                "open_interest":    _safe_int(row.get("openInterest")),
                "iv_pct":           round(_safe_float(row.get("impliedVolatility")) * 100, 1),
                "DTE":              dte,
                "cushion_pct":      round(cushion * 100, 2),
                "safety":           safety,
                "roi_pct":          round(roi * 100, 2),
                "annual_yield_pct": round(annual_yield * 100, 2),
                "collateral":       round(collateral, 2),
                "credit":           credit,
                "expiration":       exp,
            })

    ticker_results.sort(key=lambda x: x["annual_yield_pct"], reverse=True)
    return ticker_results[:max_results]


def run_cc_scan(
    tickers: List[str],
    dte_min: int = 0,
    dte_max: int = 45,
    max_results: int = 5,
    safety: str = "balanced",
) -> List[dict]:
    cushion_min     = None  # computed per-expiry via _get_cushion_min
    results         = []
    prices          = {}
    expirations_map = {}

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            hist  = stock.history(period="1d")
            if hist.empty:
                continue
            prices[ticker]          = float(hist["Close"].iloc[-1])
            expirations_map[ticker] = stock.options or []
        except Exception as e:
            print(f"[cc_scanner] Error fetching {ticker}: {e}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(
                _scan_cc_ticker,
                ticker, prices[ticker], expirations_map[ticker],
                dte_min, dte_max, max_results, safety
            ): ticker
            for ticker in prices
        }
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as e:
                print(f"[cc_scanner] Thread error: {e}")

    results.sort(key=lambda x: x["annual_yield_pct"], reverse=True)
    return results