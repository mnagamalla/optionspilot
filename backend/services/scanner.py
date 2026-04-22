import warnings
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

import yfinance as yf
from datetime import datetime
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed

SAFETY_CUSHION_MAP = {
    "conservative": 0.10,
    "balanced":     0.08,
    "aggressive":   0.05,
}


def _scan_csp_ticker(ticker, price, expirations, dte_min, dte_max,
                     cushion_min, collateral_budget, max_results):
    stock = yf.Ticker(ticker)
    ticker_results = []
    for exp in expirations:
        exp_date = datetime.strptime(exp, "%Y-%m-%d")
        dte = (exp_date - datetime.today()).days
        if dte < dte_min or dte > dte_max:
            continue
        try:
            chain = stock.option_chain(exp)
            puts  = chain.puts
        except Exception:
            continue
        for _, row in puts.iterrows():
            strike = row["strike"]
            if strike * 100 > collateral_budget:
                continue
            bid     = row.get("bid", 0)
            ask     = row.get("ask", 0)
            premium = (bid + ask) / 2 if bid > 0 and ask > 0 \
                      else row["lastPrice"]
            if premium <= 0:
                continue
            cushion = (price - strike) / price
            if cushion < cushion_min:
                continue
            collateral   = strike * 100
            credit       = round(premium * 100, 2)
            roi          = credit / collateral
            annual_yield = roi * (365 / dte)
            ticker_results.append({
                "ticker":           ticker,
                "price":            round(price, 2),
                "strike":           round(strike, 2),
                "premium":          round(premium, 2),
                "DTE":              dte,
                "cushion_pct":      round(cushion * 100, 2),
                "roi_pct":          round(roi * 100, 2),
                "annual_yield_pct": round(annual_yield * 100, 2),
                "collateral":       round(collateral, 2),
                "credit":           credit,
                "expiration":       exp,
            })
    ticker_results.sort(key=lambda x: x["annual_yield_pct"], reverse=True)
    return ticker_results[:max_results]


def run_csp_scan(
    tickers: List[str],
    dte_min: int = 30,
    dte_max: int = 45,
    max_results: int = 5,
    collateral_budget: float = 10000,
    safety: str = "balanced",
) -> List[dict]:
    cushion_min     = SAFETY_CUSHION_MAP.get(safety, 0.08)
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
                dte_min, dte_max, cushion_min, collateral_budget, max_results
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
                    cushion_min, max_results):
    stock = yf.Ticker(ticker)
    ticker_results = []
    for exp in expirations:
        exp_date = datetime.strptime(exp, "%Y-%m-%d")
        dte = (exp_date - datetime.today()).days
        if dte < dte_min or dte > dte_max:
            continue
        try:
            chain = stock.option_chain(exp)
            calls = chain.calls
        except Exception:
            continue
        for _, row in calls.iterrows():
            strike = row["strike"]
            if strike <= price:
                continue
            bid     = row.get("bid", 0)
            ask     = row.get("ask", 0)
            premium = (bid + ask) / 2 if bid > 0 and ask > 0 \
                      else row["lastPrice"]
            if premium <= 0:
                continue
            cushion = (strike - price) / price
            if cushion < cushion_min:
                continue
            collateral   = price * 100
            credit       = round(premium * 100, 2)
            roi          = credit / collateral
            annual_yield = roi * (365 / dte)
            ticker_results.append({
                "ticker":           ticker,
                "price":            round(price, 2),
                "strike":           round(strike, 2),
                "premium":          round(premium, 2),
                "DTE":              dte,
                "cushion_pct":      round(cushion * 100, 2),
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
    dte_min: int = 30,
    dte_max: int = 45,
    max_results: int = 5,
    safety: str = "balanced",
) -> List[dict]:
    cushion_min     = SAFETY_CUSHION_MAP.get(safety, 0.08)
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
                dte_min, dte_max, cushion_min, max_results
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
