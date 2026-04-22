"""
Robinhood integration via robin_stocks.

Key findings from API inspection:
- Option orders: 'account' field is None — cannot filter per account.
  All option orders are fetched once and assigned to the primary account.
- Stock orders: 'account' field contains the account URL — can filter per account.
- Dividends: no account field — fetched once for the primary account.
"""

import os
from typing import List, Dict, Optional

import robin_stocks.robinhood as rh

TRADE_TYPE_MAP = {
    ("sell", "put"):  "short_put",
    ("buy",  "put"):  "long_put",
    ("sell", "call"): "short_call",
    ("buy",  "call"): "long_call",
}


# ─── Auth ─────────────────────────────────────────────────────────────────────

def login(mfa_code: Optional[str] = None) -> bool:
    username = os.getenv("RH_USERNAME")
    password = os.getenv("RH_PASSWORD")
    if not username or not password:
        raise ValueError("RH_USERNAME and RH_PASSWORD must be set in your .env file")
    try:
        rh.login(username=username, password=password, mfa_code=mfa_code,
                 store_session=True, pickle_name="rh_session")
        return True
    except Exception as e:
        raise RuntimeError(f"Robinhood login failed: {e}")


def logout():
    try:
        rh.logout()
    except Exception:
        pass


# ─── Accounts ─────────────────────────────────────────────────────────────────

def fetch_accounts() -> List[Dict]:
    """Return all brokerage accounts using the direct API URL."""
    try:
        raw = rh.helper.request_get(
            "https://api.robinhood.com/accounts/", "results"
        ) or []
        if isinstance(raw, dict):
            raw = [raw]
    except Exception:
        profile = rh.profiles.load_account_profile(info=None) or {}
        raw = [profile] if profile else []

    accounts = []
    for a in raw:
        acct_num = a.get("account_number") or a.get("id", "unknown")
        accounts.append({
            "rh_account_number": acct_num,
            "name":              acct_num,
            "account_type":      a.get("type", "individual"),
        })
    return accounts


def _account_number_from_url(url: str) -> Optional[str]:
    """Extract account number from RH account URL.
    e.g. 'https://api.robinhood.com/accounts/5QR07212/' → '5QR07212'
    """
    if not url:
        return None
    parts = [p for p in url.rstrip("/").split("/") if p]
    return parts[-1] if parts else None


# ─── Option Orders ────────────────────────────────────────────────────────────

def fetch_option_orders(is_primary: bool = False) -> List[Dict]:
    """
    Fetch all filled option orders using robin_stocks built-in method
    (handles pagination automatically).

    The RH API returns account=None on option orders so per-account
    filtering is impossible. We fetch all orders once for the primary
    account and skip for secondary accounts.
    """
    if not is_primary:
        return []

    try:
        orders = rh.orders.get_all_option_orders() or []
    except Exception as e:
        print(f"[robinhood] Could not fetch option orders: {e}")
        return []

    results = []
    for order in orders:
        if order.get("state") != "filled":
            continue
        ticker = order.get("chain_symbol", "")
        for leg in order.get("legs", []):
            option_type = leg.get("option_type", "")
            side        = leg.get("side", "")
            trade_type  = TRADE_TYPE_MAP.get((side, option_type), "unknown")
            for exe in leg.get("executions", []):
                qty   = float(exe.get("quantity", 1))
                price = float(exe.get("price", 0))
                total = round(price * qty * 100, 2)
                results.append({
                    "rh_order_id":  f"{order.get('id','')}_"
                                    f"{leg.get('id','')}_"
                                    f"{exe.get('id','')}",
                    "ticker":       ticker,
                    "trade_type":   trade_type,
                    "option_type":  option_type,
                    "side":         side,
                    "strike":       float(leg.get("strike_price", 0) or 0),
                    "expiry":       leg.get("expiration_date", ""),
                    "quantity":     qty,
                    "premium":      price,
                    "total_amount": total if side == "sell" else -total,
                    "opened_at":    exe.get("timestamp"),
                    "status":       "open",
                })
    return results

# ─── Stock Orders ─────────────────────────────────────────────────────────────

def fetch_stock_orders(account_number: Optional[str] = None) -> List[Dict]:
    """
    Fetch filled stock orders using robin_stocks built-in method
    (handles pagination automatically).

    Stock orders DO include the account URL field, so we filter
    per account after fetching all orders.
    """
    try:
        orders = rh.orders.get_all_stock_orders() or []
    except Exception as e:
        print(f"[robinhood] Could not fetch stock orders: {e}")
        return []

    results = []
    for order in orders:
        if order.get("state") != "filled":
            continue

        # Filter by account number using the URL field
        if account_number:
            order_acct = _account_number_from_url(order.get("account", ""))
            if order_acct and order_acct != account_number:
                continue

        side   = order.get("side", "")
        ticker = order.get("symbol") or _resolve_symbol(order.get("instrument", ""))
        if not ticker:
            continue

        for exe in order.get("executions", []):
            qty   = float(exe.get("quantity", 0))
            price = float(exe.get("price", 0))
            total = round(qty * price, 2)
            results.append({
                "rh_order_id":  f"{order.get('id','')}_stock_{exe.get('id','')}",
                "ticker":       ticker,
                "trade_type":   "stock",
                "side":         side,
                "quantity":     qty,
                "premium":      price,
                "total_amount": total if side == "sell" else -total,
                "opened_at":    exe.get("timestamp"),
                "status":       "closed",
            })
    return results


# ─── Dividends ────────────────────────────────────────────────────────────────

def fetch_dividends(is_primary: bool = False) -> List[Dict]:
    """
    Fetch paid/reinvested dividends.
    RH dividend API has no account field — fetch once for primary account only.
    """
    if not is_primary:
        return []

    try:
        divs = rh.account.get_dividends() or []
    except Exception as e:
        print(f"[robinhood] Could not fetch dividends: {e}")
        return []

    results = []
    for d in divs:
        if d.get("state") not in ("paid", "reinvested"):
            continue
        amount = float(d.get("amount", 0))
        results.append({
            "rh_order_id":  f"div_{d.get('id','')}",
            "ticker":       d.get("symbol", ""),
            "trade_type":   "dividend",
            "total_amount": amount,
            "pnl":          amount,
            "opened_at":    d.get("paid_at") or d.get("payable_date"),
            "status":       "closed",
        })
    return results


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _resolve_symbol(instrument_url: str) -> Optional[str]:
    """Resolve ticker from instrument URL when symbol field is None."""
    if not instrument_url:
        return None
    try:
        data = rh.helper.request_get(instrument_url)
        return data.get("symbol") if data else None
    except Exception:
        return None
