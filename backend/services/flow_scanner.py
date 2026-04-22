"""
Unusual options flow scanner.
Detects two types of signals:
  - Volume Spike: volume/OI ratio exceeds threshold (new money entering)
  - Block Trade:  total premium value exceeds threshold (whale-size trade)
"""

import yfinance as yf
from typing import List, Optional
from datetime import datetime

# ─── Ticker universes ─────────────────────────────────────────────────────────

WATCHLIST_TICKERS = [
    "AMD", "PLTR", "NFLX", "AVGO", "NVDA", "SOFI",
    "TSM", "META", "MSFT", "AAPL", "GOOGL", "AMZN",
]

BROADER_MARKET_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA",
    "TSLA", "AAPL", "MSFT", "NVDA", "AMZN", "META",
    "GOOGL", "NFLX", "AMD", "AVGO", "PLTR",
    "JPM", "BAC", "GS", "XOM", "COIN", "HOOD",
    "SMCI", "ARM", "MSTR", "RIVN", "LYFT",
]


def scan_unusual_flow(
    tickers: List[str],
    vol_oi_threshold: float = 1.5,
    min_total_premium: float = 25000,
    dte_min: int = 0,
    dte_max: int = 60,
    option_type: str = "both",      # "calls" | "puts" | "both"
    min_volume: int = 50,           # ignore very thin options
    max_results: int = 50,
) -> List[dict]:
    """
    Scan tickers for unusual options flow.
    Returns results sorted by total_premium descending.
    """
    results = []

    for ticker in tickers:
        try:
            stock     = yf.Ticker(ticker)
            hist      = stock.history(period="1d")
            if hist.empty:
                continue
            spot = float(hist["Close"].iloc[-1])

            expirations = stock.options
            if not expirations:
                continue

            for exp in expirations:
                dte = (datetime.strptime(exp, "%Y-%m-%d") - datetime.today()).days
                if dte < dte_min or dte > dte_max:
                    continue

                chain = stock.option_chain(exp)

                sides = []
                if option_type in ("calls", "both"):
                    sides.append(("call", chain.calls))
                if option_type in ("puts", "both"):
                    sides.append(("put", chain.puts))

                for side, df in sides:
                    for _, row in df.iterrows():
                        volume = int(row.get("volume", 0) or 0)
                        oi     = int(row.get("openInterest", 0) or 0)

                        if volume < min_volume:
                            continue

                        # Bid/ask mid — fall back to lastPrice
                        bid     = float(row.get("bid", 0) or 0)
                        ask     = float(row.get("ask", 0) or 0)
                        premium = (bid + ask) / 2 if bid > 0 and ask > 0 \
                                  else float(row.get("lastPrice", 0) or 0)
                        if premium <= 0:
                            continue

                        total_premium = round(volume * premium * 100, 2)

                        # Vol/OI ratio — guard against zero OI
                        vol_oi = round(volume / oi, 2) if oi > 0 else 99.0

                        # Signal classification
                        is_spike = vol_oi >= vol_oi_threshold
                        is_block = total_premium >= min_total_premium
                        if not is_spike and not is_block:
                            continue

                        signals = []
                        if is_spike:
                            signals.append("spike")
                        if is_block:
                            signals.append("block")

                        strike    = float(row["strike"])
                        otm_pct   = round(
                            ((strike - spot) / spot * 100)
                            if side == "call"
                            else ((spot - strike) / spot * 100),
                            2
                        )
                        # Sentiment: calls = bullish, puts = bearish
                        sentiment = "bullish" if side == "call" else "bearish"

                        iv = float(row.get("impliedVolatility", 0) or 0)

                        results.append({
                            "ticker":        ticker,
                            "spot":          round(spot, 2),
                            "option_type":   side,
                            "strike":        strike,
                            "expiry":        exp,
                            "dte":           dte,
                            "volume":        volume,
                            "open_interest": oi,
                            "vol_oi_ratio":  vol_oi,
                            "premium":       round(premium, 2),
                            "total_premium": total_premium,
                            "otm_pct":       otm_pct,
                            "iv_pct":        round(iv * 100, 1),
                            "sentiment":     sentiment,
                            "signals":       signals,
                        })

        except Exception as e:
            print(f"[flow_scanner] Error scanning {ticker}: {e}")
            continue

    # Sort by total premium descending — biggest money first
    results.sort(key=lambda x: x["total_premium"], reverse=True)
    return results[:max_results]
