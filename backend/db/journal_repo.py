"""
All database read/write operations for the Journal tab.
Routers call these — no SQLAlchemy in routers.
"""

from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime, date
from collections import defaultdict

from backend.db.models import (
    Account, StockLot, LotPremium, Trade, WheelCycle, WheelCycleLeg
)

OPTION_TYPES = {
    "short_put", "long_put", "short_call", "long_call",
    "iron_condor", "debit_spread", "credit_spread",
}


# ─── Accounts ─────────────────────────────────────────────────────────────────

def get_accounts(db: Session) -> List[Account]:
    return db.query(Account).order_by(Account.name).all()


def upsert_account(db: Session, data: dict) -> Account:
    acct = db.query(Account).filter_by(
        rh_account_number=data["rh_account_number"]
    ).first()
    if not acct:
        acct = Account(
            name=data.get("name", data["rh_account_number"]),
            rh_account_number=data["rh_account_number"],
            account_type=data.get("account_type", "individual"),
        )
        db.add(acct)
        db.commit()
        db.refresh(acct)
    return acct


def update_last_synced(db: Session, account_id: int):
    db.query(Account).filter_by(id=account_id).update(
        {"last_synced_at": datetime.utcnow()}
    )
    db.commit()


# ─── Trades ───────────────────────────────────────────────────────────────────

def upsert_trade(db: Session, account_id: int, data: dict) -> bool:
    """Returns True if a new trade was inserted, False if already exists."""
    rh_id = data.get("rh_order_id")
    if rh_id and db.query(Trade).filter_by(rh_order_id=rh_id).first():
        return False

    trade = Trade(
        account_id=account_id,
        rh_order_id=rh_id,
        ticker=data.get("ticker", ""),
        trade_type=data.get("trade_type", "unknown"),
        option_type=data.get("option_type"),
        side=data.get("side"),
        strike=data.get("strike"),
        expiry=data.get("expiry"),
        quantity=data.get("quantity", 1),
        premium=data.get("premium"),
        total_amount=data.get("total_amount"),
        status=data.get("status", "open"),
        group_id=data.get("group_id"),
        pnl=data.get("pnl"),
        opened_at=_parse_dt(data.get("opened_at")),
        closed_at=_parse_dt(data.get("closed_at")),
        notes=data.get("notes"),
    )

    # Detect sell-to-close: if a matching open long exists with same
    # ticker/strike/expiry, this is a closing sale not a new short
    if data.get("side") == "sell" and data.get("strike") and data.get("expiry"):
        matching_long = db.query(Trade).filter(       # ← 8 spaces indent
            Trade.account_id == account_id,
            Trade.ticker     == data.get("ticker", "").upper(),
            Trade.strike     == data.get("strike"),
            Trade.expiry     == data.get("expiry"),
            Trade.side       == "buy",
            Trade.status     == "open",
        ).first()

        if matching_long:                             # ← 8 spaces indent
            # This is a closing sale — fix trade_type and compute P&L
            trade.trade_type = matching_long.trade_type
            trade.status     = "closed"

            # P&L = what you received closing - what you paid opening
            received  = abs(data.get("total_amount", 0))
            paid      = abs(matching_long.total_amount or 0)
            trade.pnl = round(received - paid, 2)

            # Mark the original buy as closed
            matching_long.status    = "closed"
            matching_long.closed_at = _parse_dt(data.get("opened_at"))
            matching_long.pnl       = trade.pnl
            db.flush()

    db.add(trade)
    db.commit()
    return True


def get_trades(
    db: Session,
    account_id: Optional[int] = None,
    ticker: Optional[str] = None,
    trade_type: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
) -> List[dict]:
    q = db.query(Trade)
    if account_id:
        q = q.filter(Trade.account_id == account_id)
    if ticker:
        q = q.filter(Trade.ticker == ticker.upper())
    if trade_type:
        q = q.filter(Trade.trade_type == trade_type)
    if from_date:
        q = q.filter(Trade.opened_at >= datetime.combine(from_date, datetime.min.time()))
    if to_date:
        q = q.filter(Trade.opened_at <= datetime.combine(to_date, datetime.max.time()))

    trades   = q.order_by(Trade.opened_at.desc()).all()
    accounts = {a.id: a.name for a in db.query(Account).all()}
    return [_trade_dict(t, accounts) for t in trades]


# ─── P&L ──────────────────────────────────────────────────────────────────────

def get_pnl_summary(db: Session, account_id: Optional[int] = None) -> dict:
    import datetime as dt
    now         = datetime.utcnow()
    ytd_start   = datetime(now.year, 1, 1)
    month_start = datetime(now.year, now.month, 1)
    week_start  = datetime.combine(
        date.today() - dt.timedelta(days=date.today().weekday()),
        datetime.min.time()
    )

    def _totals(from_dt=None, to_dt=None):
        q = db.query(Trade)
        if account_id:
            q = q.filter(Trade.account_id == account_id)
        if from_dt:
            q = q.filter(Trade.opened_at >= from_dt)
        if to_dt:
            q = q.filter(Trade.opened_at <= to_dt)
        return _bucket_pnl(q.all())

    return {
        "all_time":   _totals(),
        "ytd":        _totals(ytd_start),
        "this_month": _totals(month_start),
        "this_week":  _totals(week_start),
    }


def get_monthly_pnl(db: Session, account_id: Optional[int] = None) -> List[dict]:
    q = db.query(Trade)
    if account_id:
        q = q.filter(Trade.account_id == account_id)
    trades = q.filter(Trade.opened_at.isnot(None)).all()

    monthly: dict = defaultdict(lambda: {"options": 0.0, "stock": 0.0, "dividend": 0.0})
    for t in trades:
        key = t.opened_at.strftime("%Y-%m")
        amt = _signed_amount(t)
        if t.trade_type == "dividend":
            monthly[key]["dividend"] += amt
        elif t.trade_type == "stock":
            monthly[key]["stock"] += amt
        else:
            monthly[key]["options"] += amt

    result = []
    for key in sorted(monthly.keys()):
        row = monthly[key]
        result.append({
            "month":    key,
            "label":    datetime.strptime(key, "%Y-%m").strftime("%b %Y"),
            "options":  round(row["options"],  2),
            "stock":    round(row["stock"],    2),
            "dividend": round(row["dividend"], 2),
            "total":    round(row["options"] + row["stock"] + row["dividend"], 2),
        })
    return result


def get_cumulative_pnl(db: Session, account_id: Optional[int] = None) -> List[dict]:
    monthly = get_monthly_pnl(db, account_id)
    running = 0.0
    result  = []
    for row in monthly:
        running += row["total"]
        result.append({
            "month":      row["month"],
            "label":      row["label"],
            "cumulative": round(running, 2),
        })
    return result


def get_win_rate(db: Session, account_id: Optional[int] = None) -> dict:
    q = db.query(Trade).filter(Trade.trade_type.in_(OPTION_TYPES))
    if account_id:
        q = q.filter(Trade.account_id == account_id)
    trades = q.all()

    total   = len(trades)
    expired = sum(1 for t in trades if t.status == "expired")
    profit  = sum(1 for t in trades if t.status == "closed" and (t.pnl or 0) > 0)
    loss    = sum(1 for t in trades if t.status == "closed" and (t.pnl or 0) <= 0)
    open_   = sum(1 for t in trades if t.status == "open")

    by_type: dict = defaultdict(lambda: {"total": 0, "wins": 0})
    for t in trades:
        by_type[t.trade_type]["total"] += 1
        if t.status == "expired" or (t.status == "closed" and (t.pnl or 0) > 0):
            by_type[t.trade_type]["wins"] += 1

    return {
        "total":             total,
        "expired_worthless": expired,
        "closed_profit":     profit,
        "closed_loss":       loss,
        "open":              open_,
        "win_rate_pct":      round((expired + profit) / total * 100, 1) if total else 0,
        "by_type": [
            {
                "type":     k,
                "total":    v["total"],
                "wins":     v["wins"],
                "win_rate": round(v["wins"] / v["total"] * 100, 1) if v["total"] else 0,
            }
            for k, v in by_type.items()
        ],
    }


# ─── Stock Lots / Cost Basis ──────────────────────────────────────────────────

def get_lots_with_basis(
    db: Session,
    account_id: Optional[int] = None,
    ticker: Optional[str] = None,
    current_prices: Optional[dict] = None,
) -> List[dict]:
    q = db.query(StockLot)
    if account_id:
        q = q.filter(StockLot.account_id == account_id)
    if ticker:
        q = q.filter(StockLot.ticker == ticker.upper())
    lots     = q.order_by(StockLot.ticker, StockLot.lot_number).all()
    accounts = {a.id: a.name for a in db.query(Account).all()}

    result = []
    for lot in lots:
        total_premium = sum(p.premium_per_share for p in lot.premiums)
        adj_basis     = round(lot.avg_cost - total_premium, 4)
        current       = (current_prices or {}).get(lot.ticker)

        buffer_pct = None
        alert      = "green"
        if current:
            buffer_pct = round((current - adj_basis) / adj_basis * 100, 2)
            if buffer_pct < 2:
                alert = "red"
            elif buffer_pct < 5:
                alert = "yellow"

        result.append({
            "id":            lot.id,
            "account_id":    lot.account_id,
            "account_name":  accounts.get(lot.account_id, ""),
            "ticker":        lot.ticker,
            "lot_number":    lot.lot_number,
            "shares":        lot.shares,
            "avg_cost":      lot.avg_cost,
            "cc_premiums":   round(total_premium, 4),
            "adj_basis":     adj_basis,
            "current_price": current,
            "buffer_pct":    buffer_pct,
            "alert":         alert,
            "purchase_date": lot.purchase_date.isoformat() if lot.purchase_date else None,
        })
    return result


# ─── Wheel Cycles ─────────────────────────────────────────────────────────────

def get_wheel_cycles(db: Session, account_id: Optional[int] = None) -> List[dict]:
    q = db.query(WheelCycle)
    if account_id:
        q = q.filter(WheelCycle.account_id == account_id)
    cycles   = q.order_by(WheelCycle.opened_at.desc()).all()
    accounts = {a.id: a.name for a in db.query(Account).all()}

    result = []
    for c in cycles:
        legs = sorted(c.legs, key=lambda l: l.sequence)
        result.append({
            "id":            c.id,
            "account_name":  accounts.get(c.account_id, ""),
            "ticker":        c.ticker,
            "status":        c.status,
            "total_premium": c.total_premium,
            "net_pnl":       c.net_pnl,
            "opened_at":     c.opened_at.isoformat() if c.opened_at else None,
            "closed_at":     c.closed_at.isoformat() if c.closed_at else None,
            "legs": [
                {"leg_type": l.leg_type, "sequence": l.sequence, "trade_id": l.trade_id}
                for l in legs
            ],
        })
    return result


def detect_wheel_cycles(db: Session, account_id: Optional[int] = None):
    """Auto-group short_put and short_call trades into wheel cycles."""
    q = db.query(Trade).filter(Trade.trade_type.in_(["short_put", "short_call"]))
    if account_id:
        q = q.filter(Trade.account_id == account_id)
    trades = q.order_by(Trade.opened_at).all()

    open_cycles: dict = {}  # (account_id, ticker) → WheelCycle

    for t in trades:
        key = (t.account_id, t.ticker)
        if db.query(WheelCycleLeg).filter_by(trade_id=t.id).first():
            continue  # already linked

        if t.trade_type == "short_put":
            cycle = WheelCycle(
                account_id=t.account_id,
                ticker=t.ticker,
                status="open",
                total_premium=round(t.total_amount or 0, 2),
                opened_at=t.opened_at,
            )
            db.add(cycle)
            db.flush()
            db.add(WheelCycleLeg(
                cycle_id=cycle.id, trade_id=t.id, leg_type="csp", sequence=1
            ))
            open_cycles[key] = cycle

        elif t.trade_type == "short_call" and key in open_cycles:
            cycle = open_cycles[key]
            cycle.total_premium = round((cycle.total_premium or 0) + (t.total_amount or 0), 2)
            cycle.status = "covered"
            seq = len(cycle.legs) + 1
            db.add(WheelCycleLeg(
                cycle_id=cycle.id, trade_id=t.id, leg_type="cc", sequence=seq
            ))

    db.commit()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _signed_amount(trade: Trade) -> float:
    """
    total_amount is already correctly signed when stored during sync:
      sell side  → positive (credit received)
      buy side   → negative (debit paid)
    Returns as-is for options and dividends.
    Stock is handled separately via FIFO matching in _realized_stock_pnl.
    """
    if trade.pnl is not None:
        return trade.pnl
    if trade.total_amount is None:
        return 0.0
    return trade.total_amount


def _realized_stock_pnl(trades: list) -> float:
    """
    FIFO matching of stock buys vs sells.
    Only closed positions contribute to P&L.
    Open positions (bought but not yet sold) are excluded.
    """
    from collections import defaultdict

    by_ticker: dict = defaultdict(list)
    for t in trades:
        if t.trade_type == "stock" and t.opened_at:
            by_ticker[t.ticker].append(t)

    realized = 0.0
    for ticker, ticker_trades in by_ticker.items():
        sorted_trades = sorted(ticker_trades, key=lambda x: x.opened_at)
        buy_lots = []  # each entry: [cost_per_share, remaining_qty]

        for t in sorted_trades:
            qty   = float(t.quantity or 0)
            price = float(t.premium or 0)  # price per share
            if qty <= 0 or price <= 0:
                continue

            if t.side == "buy":
                buy_lots.append([price, qty])

            elif t.side in ("sell", "sell_short"):
                remaining = qty
                while remaining > 0 and buy_lots:
                    lot_price, lot_qty = buy_lots[0]
                    matched = min(remaining, lot_qty)
                    realized += (price - lot_price) * matched
                    remaining        -= matched
                    buy_lots[0][1]   -= matched
                    if buy_lots[0][1] <= 0:
                        buy_lots.pop(0)
                # Unmatched quantity = short sell with no prior buy
                if remaining > 0:
                    realized += price * remaining

    return round(realized, 2)


def _bucket_pnl(trades: list) -> dict:
    options      = 0.0
    dividend     = 0.0
    stock_trades = []

    for t in trades:
        if t.trade_type == "dividend":
            dividend += (t.total_amount or 0.0)
        elif t.trade_type == "stock":
            stock_trades.append(t)
        else:
            # For closed trades use pnl if available — avoids double counting
            # both legs of a spread or LEAP open/close
            if t.status == "closed" and t.pnl is not None:
                # Only count once — on the closing leg (sell side)
                if t.side == "sell":
                    options += t.pnl
                # Skip the buy leg — its P&L is already captured on the sell leg
            else:
                # Open trades and sell-to-open: use total_amount directly
                options += (t.total_amount or 0.0)

    stock = _realized_stock_pnl(stock_trades)
    total = options + stock + dividend

    return {
        "options":  round(options,  2),
        "stock":    round(stock,    2),
        "dividend": round(dividend, 2),
        "total":    round(total,    2),
    }


def _trade_dict(t: Trade, accounts: dict) -> dict:
    return {
        "id":           t.id,
        "account_id":   t.account_id,
        "account_name": accounts.get(t.account_id, ""),
        "ticker":       t.ticker,
        "trade_type":   t.trade_type,
        "side":         t.side,
        "strike":       t.strike,
        "expiry":       t.expiry,
        "quantity":     t.quantity,
        "premium":      t.premium,
        "total_amount": t.total_amount,
        "status":       t.status,
        "pnl":          t.pnl,
        "opened_at":    t.opened_at.isoformat() if t.opened_at else None,
        "closed_at":    t.closed_at.isoformat() if t.closed_at else None,
        "notes":        t.notes,
    }

def get_pnl_drilldown(
    db: Session,
    period: str,
    account_id: Optional[int] = None,
) -> dict:
    import datetime as dt
    now = datetime.utcnow()

    if period == "week":
        from_dt = datetime.combine(
            date.today() - dt.timedelta(days=date.today().weekday()),
            datetime.min.time()
        )
    elif period == "month":
        from_dt = datetime(now.year, now.month, 1)
    elif period == "ytd":
        from_dt = datetime(now.year, 1, 1)
    else:
        from_dt = None

    q = db.query(Trade)
    if account_id:
        q = q.filter(Trade.account_id == account_id)
    if from_dt:
        q = q.filter(Trade.opened_at >= from_dt)
    trades   = q.order_by(Trade.opened_at.desc()).all()
    accounts = {a.id: a.name for a in db.query(Account).all()}

    # ── Per-ticker aggregation ────────────────────────────────────────────────
    # Use plain dicts — no defaultdict import needed inside function
    ticker_options:  dict = {}
    ticker_stock:    dict = {}
    ticker_dividend: dict = {}
    stock_by_ticker: dict = {}

    for t in trades:
        tkr = t.ticker or ""

        if t.trade_type == "stock":
            stock_by_ticker.setdefault(tkr, []).append(t)
            continue

        if t.trade_type == "dividend":
            ticker_dividend[tkr] = ticker_dividend.get(tkr, 0.0) + (t.total_amount or 0.0)
            continue

        if t.status == "closed" and t.pnl is not None:
           if t.side == "sell":
                ticker_options[tkr] = ticker_options.get(tkr, 0.0) + t.pnl
            # skip buy leg — already captured in sell leg pnl
        else:
            amount = t.total_amount if t.total_amount is not None else 0.0
            ticker_options[tkr] = ticker_options.get(tkr, 0.0) + amount

    # Realized stock P&L per ticker (FIFO)
    for tkr, tkr_trades in stock_by_ticker.items():
        ticker_stock[tkr] = _realized_stock_pnl(tkr_trades)

    # Combine all tickers
    all_tickers = set(
        list(ticker_options.keys()) +
        list(ticker_stock.keys()) +
        list(ticker_dividend.keys())
    )

    per_ticker = []
    for tkr in all_tickers:
        opt = round(ticker_options.get(tkr, 0.0),  2)
        stk = round(ticker_stock.get(tkr,   0.0),  2)
        div = round(ticker_dividend.get(tkr, 0.0),  2)
        net = round(opt + stk + div, 2)
        if opt == 0 and stk == 0 and div == 0:
            continue
        per_ticker.append({
            "ticker":   tkr,
            "options":  opt,
            "stock":    stk,
            "dividend": div,
            "total":    net,
        })

    per_ticker.sort(key=lambda x: x["total"], reverse=True)

    return {
        "period":     period,
        "from_date":  from_dt.isoformat() if from_dt else None,
        "per_ticker": per_ticker,
        "trades":     [_trade_dict(t, accounts) for t in trades],
    }   

def get_ticker_option_detail(
    db: Session,
    ticker: str,
    account_id: Optional[int] = None,
) -> dict:
    """
    All option trades for a specific ticker with open/closed breakdown.
    Live prices are fetched separately in the router via yfinance.
    """
    q = db.query(Trade).filter(
        Trade.ticker == ticker.upper(),
        Trade.trade_type.in_([
            "short_call", "long_call", "short_put", "long_put",
            "iron_condor", "debit_spread", "credit_spread"
        ])
    )
    if account_id:
        q = q.filter(Trade.account_id == account_id)
    trades = q.order_by(Trade.opened_at.desc()).all()

    accounts = {a.id: a.name for a in db.query(Account).all()}

    total_credits = sum(
        (t.total_amount or 0) for t in trades if (t.total_amount or 0) > 0
    )
    total_debits = sum(
        (t.total_amount or 0) for t in trades if (t.total_amount or 0) < 0
    )

    open_trades   = [_trade_dict(t, accounts) for t in trades if t.status == "open"]
    closed_trades = [_trade_dict(t, accounts) for t in trades if t.status != "open"]

    return {
        "ticker":         ticker.upper(),
        "total_credits":  round(total_credits, 2),
        "total_debits":   round(total_debits,  2),
        "net_cash_flow":  round(total_credits + total_debits, 2),
        "open_trades":    open_trades,
        "closed_trades":  closed_trades,
    }

def detect_rolls(db: Session, account_id: Optional[int] = None):
    """
    Detect rolls: buy-to-close + sell-to-open on same ticker+option_type
    within 24 hours. Links them with a shared group_id and roll flag.
    """
    import uuid
    from datetime import timedelta

    q = db.query(Trade).filter(
        Trade.trade_type.in_([
            "short_call","long_call","short_put","long_put"
        ]),
        Trade.group_id.is_(None)   # only unlinked trades
    )
    if account_id:
        q = q.filter(Trade.account_id == account_id)
    trades = q.order_by(Trade.opened_at).all()

    # Group by ticker + option_type
    by_key: dict = defaultdict(list)
    for t in trades:
        option_type = "call" if "call" in t.trade_type else "put"
        by_key[(t.account_id, t.ticker, option_type)].append(t)

    rolls_found = 0
    for key, legs in by_key.items():
        for i, t1 in enumerate(legs):
            if t1.group_id:
                continue
            # Look for a matching opposite leg within 24 hours
            for t2 in legs[i+1:]:
                if t2.group_id:
                    continue
                if not t1.opened_at or not t2.opened_at:
                    continue
                time_diff = abs((t2.opened_at - t1.opened_at).total_seconds())
                if time_diff > 86400:   # 24 hours
                    break

                # Roll = one buy + one sell (close old, open new)
                sides = {t1.side, t2.side}
                if sides != {"buy", "sell"}:
                    continue

                # Assign shared group_id and mark as roll
                roll_id = str(uuid.uuid4())[:8]
                t1.group_id = f"roll_{roll_id}"
                t2.group_id = f"roll_{roll_id}"
                rolls_found += 1
                break

    if rolls_found:
        db.commit()
    return rolls_found


def get_rolls(
    db: Session,
    account_id: Optional[int] = None,
) -> List[dict]:
    """Return all roll pairs grouped by group_id."""
    q = db.query(Trade).filter(
        Trade.group_id.like("roll_%")
    )
    if account_id:
        q = q.filter(Trade.account_id == account_id)
    trades   = q.order_by(Trade.opened_at).all()
    accounts = {a.id: a.name for a in db.query(Account).all()}

    # Group by roll id
    roll_map: dict = defaultdict(list)
    for t in trades:
        roll_map[t.group_id].append(t)

    result = []
    for roll_id, legs in roll_map.items():
        if len(legs) != 2:
            continue
        buy_leg  = next((l for l in legs if l.side == "buy"),  None)
        sell_leg = next((l for l in legs if l.side == "sell"), None)
        if not buy_leg or not sell_leg:
            continue

        net = round(
            (sell_leg.total_amount or 0) + (buy_leg.total_amount or 0), 2
        )
        result.append({
            "roll_id":       roll_id,
            "account_name":  accounts.get(buy_leg.account_id, ""),
            "ticker":        buy_leg.ticker,
            "option_type":   "call" if "call" in buy_leg.trade_type else "put",
            "closed_strike": buy_leg.strike,
            "closed_expiry": buy_leg.expiry,
            "closed_cost":   buy_leg.total_amount,
            "new_strike":    sell_leg.strike,
            "new_expiry":    sell_leg.expiry,
            "new_credit":    sell_leg.total_amount,
            "net_premium":   net,
            "roll_date":     buy_leg.opened_at.isoformat() if buy_leg.opened_at else None,
            "is_credit":     net >= 0,
        })

    result.sort(key=lambda x: x["roll_date"] or "", reverse=True)
    return result

def detect_assignments(
    db: Session,
    account_id: Optional[int] = None,
) -> List[dict]:
    """
    Detect CSP assignments: short_put trades followed by a stock buy
    on the same ticker within 3 days at approximately the strike price.
    Returns list of unprocessed assignments (no matching stock_lot yet).
    """
    q = db.query(Trade).filter(
        Trade.trade_type == "short_put",
        Trade.status.in_(["open", "assigned"])
    )
    if account_id:
        q = q.filter(Trade.account_id == account_id)
    short_puts = q.all()

    assignments = []
    for put in short_puts:
        if not put.strike or not put.expiry:
            continue

        # Check if a matching stock_lot already exists
        existing_lot = db.query(StockLot).filter(
            StockLot.account_id == put.account_id,
            StockLot.ticker     == put.ticker,
        ).first()
        # Skip if lot already exists for this ticker
        if existing_lot:
            continue

        # Look for a stock buy near the strike price within 3 days of expiry
        from datetime import datetime as dt, timedelta
        try:
            expiry_dt = dt.strptime(put.expiry, "%Y-%m-%d")
        except Exception:
            continue

        window_start = expiry_dt - timedelta(days=1)
        window_end   = expiry_dt + timedelta(days=3)

        stock_buy = db.query(Trade).filter(
            Trade.account_id == put.account_id,
            Trade.ticker     == put.ticker,
            Trade.trade_type == "stock",
            Trade.side       == "buy",
            Trade.opened_at  >= window_start,
            Trade.opened_at  <= window_end,
        ).first()

        if stock_buy:
            assignments.append({
                "put_id":       put.id,
                "account_id":   put.account_id,
                "ticker":       put.ticker,
                "strike":       put.strike,
                "expiry":       put.expiry,
                "shares":       100 * (put.quantity or 1),
                "cost_basis":   put.strike,
                "assigned_at":  stock_buy.opened_at.isoformat()
                                if stock_buy.opened_at else None,
            })

    return assignments


def create_lots_from_assignments(
    db: Session,
    assignments: List[dict],
) -> int:
    """Create stock_lots from detected assignments."""
    created = 0
    for a in assignments:
        # Get next lot number for this ticker+account
        existing = db.query(StockLot).filter_by(
            account_id=a["account_id"],
            ticker=a["ticker"],
        ).count()
        lot_number = existing + 1

        lot = StockLot(
            account_id=a["account_id"],
            ticker=a["ticker"],
            lot_number=lot_number,
            shares=a["shares"],
            avg_cost=a["cost_basis"],
            purchase_date=_parse_dt(a["assigned_at"]),
        )
        db.add(lot)

        # Mark the short put as assigned
        put = db.query(Trade).filter_by(id=a["put_id"]).first()
        if put:
            put.status = "assigned"

        created += 1

    if created:
        db.commit()
    return created   