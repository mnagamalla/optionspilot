import yfinance as yf
from typing import Optional
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db import journal_repo as repo
from backend.models.journal import SyncRequest, SyncResponse
import backend.services.robinhood as rh_service

from backend.db.models import Account, Trade, StockLot
from functools import lru_cache
import time

router = APIRouter(prefix="/journal", tags=["Journal"])


@router.post("/sync", response_model=SyncResponse)
def sync_robinhood(req: SyncRequest, db: Session = Depends(get_db)):
    """
    Sync trades from Robinhood.

    Per-account strategy:
    - Option orders: fetched once (all) for primary account — RH API
      returns account=None on option orders so per-account filtering
      is impossible.
    - Stock orders: fetched for all accounts, filtered by account URL.
    - Dividends: fetched once for primary account — no account field in RH API.

    Resync is always safe — rh_order_id deduplication skips existing trades.
    """
    try:
        rh_service.login(mfa_code=req.mfa_code)
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

    try:
        rh_accounts = rh_service.fetch_accounts()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch accounts: {e}")

    if not rh_accounts:
        raise HTTPException(status_code=502, detail="No Robinhood accounts found.")

    total_synced = 0

    for i, rh_acct in enumerate(rh_accounts):
        account     = repo.upsert_account(db, rh_acct)
        acct_number = rh_acct["rh_account_number"]
        is_primary  = (i == 0)

        all_orders = (
            # Options: fetch all only for primary account (account=None in API)
            rh_service.fetch_option_orders(is_primary=is_primary)
            # Stocks: fetch all, filtered per account by URL
            + rh_service.fetch_stock_orders(account_number=acct_number)
            # Dividends: fetch all only for primary account (no account field)
            + rh_service.fetch_dividends(is_primary=is_primary)
        )

        synced_this_account = 0
        for order in all_orders:
            if repo.upsert_trade(db, account.id, order):
                total_synced += 1
                synced_this_account += 1

        repo.update_last_synced(db, account.id)
        print(
            f"[sync] Account {acct_number} ({'primary' if is_primary else 'secondary'}): "
            f"{len(all_orders)} fetched, {synced_this_account} new"
        )

    repo.detect_wheel_cycles(db)
    repo.detect_rolls(db)
    rh_service.logout()

    return SyncResponse(
        status="ok",
        synced=total_synced,
        accounts_found=len(rh_accounts),
        message=f"Synced {total_synced} new trades across {len(rh_accounts)} account(s).",
    )


@router.get("/accounts")
def get_accounts(db: Session = Depends(get_db)):
    accounts = repo.get_accounts(db)
    return [
        {
            "id":                a.id,
            "name":              a.name,
            "rh_account_number": a.rh_account_number,
            "account_type":      a.account_type,
            "last_synced_at":    a.last_synced_at.isoformat() if a.last_synced_at else None,
        }
        for a in accounts
    ]

@router.patch("/accounts/{account_id}")
def rename_account(
    account_id: int,
    payload: dict,
    db: Session = Depends(get_db),
):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name cannot be empty")
    updated = db.query(Account).filter_by(id=account_id).first()
    if not updated:
        raise HTTPException(status_code=404, detail="Account not found")
    updated.name = name
    db.commit()
    return {"id": account_id, "name": name}


@router.get("/export/trades")
def export_trades_csv(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    import csv, io
    from fastapi.responses import StreamingResponse

    trades = repo.get_trades(db, account_id)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "date", "account", "ticker", "trade_type", "side",
        "strike", "expiry", "quantity", "premium",
        "total_amount", "status", "pnl",
    ])
    writer.writeheader()
    for t in trades:
        writer.writerow({
            "date":         (t["opened_at"] or "")[:10],
            "account":      t["account_name"],
            "ticker":       t["ticker"],
            "trade_type":   t["trade_type"],
            "side":         t["side"] or "",
            "strike":       t["strike"] or "",
            "expiry":       t["expiry"] or "",
            "quantity":     t["quantity"],
            "premium":      t["premium"] or "",
            "total_amount": t["total_amount"] or "",
            "status":       t["status"],
            "pnl":          t["pnl"] or "",
        })

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades.csv"},
    )


@router.get("/export/monthly")
def export_monthly_csv(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    import csv, io
    from fastapi.responses import StreamingResponse

    rows = repo.get_monthly_pnl(db, account_id)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "month", "options", "stock", "dividend", "total"
    ])
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "month":    r["label"],
            "options":  r["options"],
            "stock":    r["stock"],
            "dividend": r["dividend"],
            "total":    r["total"],
        })

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=monthly_pnl.csv"},
    )

@router.get("/export/lots")
def export_lots_csv(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    import csv, io
    from fastapi.responses import StreamingResponse

    lots = repo.get_lots_with_basis(db, account_id)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "account", "ticker", "lot", "shares", "avg_cost",
        "cc_premiums", "adj_basis", "current_price", "buffer_pct", "alert"
    ])
    writer.writeheader()
    for l in lots:
        writer.writerow({
            "account":       l["account_name"],
            "ticker":        l["ticker"],
            "lot":           l["lot_number"],
            "shares":        l["shares"],
            "avg_cost":      l["avg_cost"],
            "cc_premiums":   l["cc_premiums"],
            "adj_basis":     l["adj_basis"],
            "current_price": l["current_price"] or "",
            "buffer_pct":    l["buffer_pct"] or "",
            "alert":         l["alert"],
        })

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cost_basis.csv"},
    )

@router.get("/trades")
def get_trades(
    account_id: Optional[int]  = Query(None),
    ticker:     Optional[str]  = Query(None),
    trade_type: Optional[str]  = Query(None),
    from_date:  Optional[date] = Query(None),
    to_date:    Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    return repo.get_trades(db, account_id, ticker, trade_type, from_date, to_date)


@router.get("/pnl/summary")
def get_pnl_summary(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    return repo.get_pnl_summary(db, account_id)


@router.get("/pnl/monthly")
def get_monthly_pnl(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    return repo.get_monthly_pnl(db, account_id)


@router.get("/pnl/cumulative")
def get_cumulative_pnl(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    return repo.get_cumulative_pnl(db, account_id)


@router.get("/winrate")
def get_win_rate(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    return repo.get_win_rate(db, account_id)

@router.get("/pnl/drilldown")
def get_pnl_drilldown(
    period:     str            = Query(..., description="week | month | ytd | all_time"),
    account_id: Optional[int]  = Query(None),
    db: Session = Depends(get_db),
):
    if period not in ("week", "month", "ytd", "all_time"):
        raise HTTPException(status_code=422, detail="period must be week | month | ytd | all_time")
    return repo.get_pnl_drilldown(db, period, account_id)

@router.get("/lots")
def get_lots(
    account_id: Optional[int] = Query(None),
    ticker:     Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    lots    = repo.get_lots_with_basis(db, account_id, ticker)
    tickers = list({l["ticker"] for l in lots if l["ticker"]})
    prices  = {}
    if tickers:
        try:
            data = yf.download(tickers, period="1d", progress=False, group_by="ticker")
            for t in tickers:
                try:
                    prices[t] = float(data["Close"].iloc[-1]) if len(tickers) == 1 \
                                 else float(data[t]["Close"].iloc[-1])
                except Exception:
                    pass
        except Exception:
            pass
    return repo.get_lots_with_basis(db, account_id, ticker, current_prices=prices)


@router.get("/cycles")
def get_cycles(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    return repo.get_wheel_cycles(db, account_id)

@router.get("/positions")
def get_option_positions(
    ticker:     str            = Query(...),
    account_id: Optional[int]  = Query(None),
    db: Session = Depends(get_db),
):
    """
    Detailed option breakdown for a ticker + live mark-to-market
    for open positions via yfinance.
    """
    detail = repo.get_ticker_option_detail(db, ticker, account_id)

    # Fetch live prices for open positions
    today = date.today().isoformat()
    for t in detail["open_trades"]:
        expiry      = t.get("expiry", "")
        strike      = t.get("strike")
        trade_type  = t.get("trade_type", "")
        qty         = t.get("quantity", 1)

        # Expired options are worth 0
        if expiry and expiry < today:
            t["current_mid"]    = 0.0
            t["current_value"]  = 0.0
            t["unrealized_pnl"] = round((t.get("total_amount") or 0), 2)
            continue

        t["current_mid"]    = None
        t["current_value"]  = None
        t["unrealized_pnl"] = None

        if not expiry or not strike:
            continue

        try:
            stock    = yf.Ticker(ticker)
            chain    = stock.option_chain(expiry)
            df       = chain.calls if "call" in trade_type else chain.puts
            row      = df[df["strike"] == strike]
            if row.empty:
                continue
            bid = float(row["bid"].iloc[0])
            ask = float(row["ask"].iloc[0])
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 \
                  else float(row["lastPrice"].iloc[0])
            mid   = round(mid, 2)
            value = round(mid * qty * 100, 2)
            cost  = t.get("total_amount") or 0   # negative for buys
            # unrealized = current value + what you spent (cost is negative for longs)
            t["current_mid"]    = mid
            t["current_value"]  = value
            t["unrealized_pnl"] = round(value + cost, 2)
        except Exception:
            pass

    # Compute totals across open positions
    open_market_value = sum(
        t.get("current_value") or 0 for t in detail["open_trades"]
    )
    open_cost_basis = sum(
        (t.get("total_amount") or 0) for t in detail["open_trades"]
    )
    detail["open_market_value"]   = round(open_market_value, 2)
    detail["open_cost_basis"]     = round(open_cost_basis, 2)
    detail["open_unrealized_pnl"] = round(open_market_value + open_cost_basis, 2)

    return detail

@router.get("/rolls")
def get_rolls(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    # Run detection first to catch any new rolls
    repo.detect_rolls(db, account_id)
    return repo.get_rolls(db, account_id)

# Simple in-memory cache — stores (data, timestamp) per ticker set
_earnings_cache: dict = {}
_CACHE_TTL = 60 * 60 * 24  # 24 hours

@router.get("/earnings")
def get_earnings(
    tickers: str = Query(..., description="Comma-separated tickers"),
):
    """
    Returns upcoming earnings dates for given tickers.
    Cached for 24 hours to avoid hammering yfinance.
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    cache_key   = ",".join(sorted(ticker_list))
    now         = time.time()

    # Return cached if fresh
    if cache_key in _earnings_cache:
        data, ts = _earnings_cache[cache_key]
        if now - ts < _CACHE_TTL:
            return data

    results = []
    for ticker in ticker_list:
        try:
            stock    = yf.Ticker(ticker)
            calendar = stock.calendar
            if calendar is None:
                continue

            # yfinance returns calendar as dict or DataFrame
            if hasattr(calendar, 'to_dict'):
                cal = calendar.to_dict()
            else:
                cal = calendar

            earnings_date = None
            if isinstance(cal, dict):
                ed = cal.get('Earnings Date')
                if ed:
                    if isinstance(ed, list):
                        earnings_date = str(ed[0])[:10]
                    else:
                        earnings_date = str(ed)[:10]

            if earnings_date:
                from datetime import datetime as dt
                today    = date.today()
                earn_dt  = dt.strptime(earnings_date[:10], "%Y-%m-%d").date()
                days_out = (earn_dt - today).days

                results.append({
                    "ticker":        ticker,
                    "earnings_date": earnings_date[:10],
                    "days_out":      days_out,
                    "alert":         "red"    if days_out <= 14 else
                                     "yellow" if days_out <= 30 else
                                     "green",
                })
        except Exception as e:
            print(f"[earnings] {ticker}: {e}")
            continue

    _earnings_cache[cache_key] = (results, now)
    return results

@router.get("/assignments/pending")
def get_pending_assignments(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Returns detected assignments that don't have stock lots yet."""
    return repo.detect_assignments(db, account_id)


@router.post("/assignments/confirm")
def confirm_assignments(
    payload: dict,
    db: Session = Depends(get_db),
):
    """Create stock lots from confirmed assignments."""
    assignments = payload.get("assignments", [])
    if not assignments:
        raise HTTPException(status_code=422, detail="No assignments provided")
    created = repo.create_lots_from_assignments(db, assignments)
    return {"created": created, "message": f"Created {created} stock lot(s)"}


@router.get("/account-value")
def get_account_value(
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Computes total estimated account value:
    - Stock positions at current market price (excludes EBAY, PYPL)
    - Open options at mark-to-market mid price (spreads netted)
    - Cash excluded (cannot be reliably fetched from Robinhood API)
    """
    EXCLUDED_TICKERS = {}

    # ── Stock positions ───────────────────────────────────────────────
    lots = repo.get_lots_with_basis(db, account_id)
    lots = [l for l in lots if l["ticker"] not in EXCLUDED_TICKERS]

    tickers = list({l["ticker"] for l in lots if l["ticker"]})
    prices  = {}
    if tickers:
        try:
            data = yf.download(
                tickers, period="1d", progress=False, group_by="ticker"
            )
            for t in tickers:
                try:
                    prices[t] = float(data["Close"].iloc[-1]) \
                                 if len(tickers) == 1 \
                                 else float(data[t]["Close"].iloc[-1])
                except Exception:
                    pass
        except Exception:
            pass

    stock_value   = 0.0
    stock_cost    = 0.0
    stock_details = []
    for lot in lots:
        current = prices.get(lot["ticker"])
        if not current:
            continue
        mkt_val  = round(current * lot["shares"], 2)
        cost_val = round(lot["adj_basis"] * lot["shares"], 2)
        stock_value += mkt_val
        stock_cost  += cost_val
        stock_details.append({
            "ticker":         lot["ticker"],
            "shares":         lot["shares"],
            "adj_basis":      lot["adj_basis"],
            "current_price":  current,
            "market_value":   mkt_val,
            "unrealized_pnl": round(mkt_val - cost_val, 2),
        })

    # ── Open options mark-to-market ───────────────────────────────────
    q = db.query(Trade).filter(
        Trade.status     == "open",
        Trade.trade_type.in_([
            "short_call", "long_call", "short_put", "long_put",
            "iron_condor", "debit_spread", "credit_spread",
        ])
    )
    if account_id:
        q = q.filter(Trade.account_id == account_id)
    open_options = q.all()

    raw_options = []
    today_str   = date.today().isoformat()

    for opt in open_options:
        if not opt.expiry or not opt.strike:
            continue
        if opt.ticker in EXCLUDED_TICKERS:
            continue
        if opt.expiry < today_str:
            continue
        try:
            stock = yf.Ticker(opt.ticker)
            chain = stock.option_chain(opt.expiry)
            df    = chain.calls if "call" in opt.trade_type else chain.puts
            row   = df[df["strike"] == opt.strike]
            if row.empty:
                continue
            bid = float(row["bid"].iloc[0])
            ask = float(row["ask"].iloc[0])
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 \
                  else float(row["lastPrice"].iloc[0])
            mid        = round(mid, 2)
            qty        = opt.quantity or 1
            multiplier = -1 if opt.side == "sell" else 1
            mkt_val    = round(mid * qty * 100 * multiplier, 2)

            raw_options.append({
                "ticker":       opt.ticker,
                "trade_type":   opt.trade_type,
                "side":         opt.side,
                "strike":       opt.strike,
                "expiry":       opt.expiry,
                "current_mid":  mid,
                "market_value": mkt_val,
            })
        except Exception:
            continue

    # ── Net out matched spread pairs (same ticker + strike + expiry) ──
    from collections import defaultdict as _dd
    spread_map: dict = _dd(list)
    for opt in raw_options:
        key = (opt["ticker"], opt["strike"], opt["expiry"])
        spread_map[key].append(opt)

    options_details = []
    options_value   = 0.0

    for key, legs in spread_map.items():
        if len(legs) == 2:
            sides = {l["trade_type"].split("_")[0] for l in legs}
            if sides == {"long", "short"}:
                net = round(sum(l["market_value"] for l in legs), 2)
                options_details.append({
                    "ticker":       key[0],
                    "trade_type":   "spread",
                    "strike":       key[1],
                    "expiry":       key[2],
                    "market_value": net,
                })
                options_value += net
                continue
        for leg in legs:
            options_details.append(leg)
            options_value += leg["market_value"]

    options_value = round(options_value, 2)

    # ── Totals (no cash — cannot reliably fetch from RH API) ─────────
    total_value      = round(stock_value + options_value, 2)
    unrealized_stock = round(stock_value - stock_cost, 2)

    return {
        "total_value":      total_value,
        "stock_value":      round(stock_value,  2),
        "stock_cost":       round(stock_cost,   2),
        "unrealized_stock": unrealized_stock,
        "options_value":    options_value,
        "stock_details":    sorted(stock_details, key=lambda x: x["ticker"]),
        "options_details":  options_details,
        "excluded_tickers": list(EXCLUDED_TICKERS),
    }
